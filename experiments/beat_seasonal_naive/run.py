"""Leakage-safe search for a 2025 forecast that beats seasonal naive on WAPE.

Train on 2019-01..2022-12. Choose variants and hyperparameters on 2023 only.
Score 2024 after that choice. Refit the chosen configuration on 2019-01..2024-12
and score 2025 with frozen weights and with a monthly expanding refit.

2018 is lag history only. Features for month t read months strictly before t.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from src.common.calendar import month_range
from src.common.paths import ROOT
from src.model.leakage import (
    TIME_SPLIT,
    assert_no_future_leakage,
    build_forecast_features,
    shift_year_month,
)
from src.stream.metrics import _block

HERE = Path(__file__).resolve().parent
PANEL_PATH = ROOT / "data" / "model" / "province_month_unified.parquet"
BASELINE_PATH = ROOT / "reports" / "baseline_test_2024.json"

BASE_FEATURES = [
    "guest_lag_1",
    "guest_lag_12",
    "rain_lag_1",
    "rain_roll3",
    "is_covid",
    "is_covid_lag12",
]
EXTRA_NUMERIC = [
    "guest_lag_2",
    "guest_lag_3",
    "yoy_growth",
    "rain_anomaly",
    "month_sin",
    "month_cos",
]
RF_CURRENT = dict(n_estimators=300, max_depth=10, min_samples_leaf=3, random_state=42, n_jobs=4)


@dataclass(frozen=True)
class Spec:
    name: str
    family: str
    target: str
    feature_set: str
    params: tuple


def _months(start: str, end: str) -> list[str]:
    return month_range(start, end)


def _lookup(wide: pd.DataFrame, province_ids: pd.Series, month: str) -> np.ndarray:
    if month not in wide.columns:
        return np.full(len(province_ids), np.nan)
    return wide.reindex(province_ids)[month].to_numpy(dtype=float)


def build_feature_table(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per province for every month from 2019-01 through 2025-12."""
    guest = panel.pivot(index="province_id", columns="year_month", values="guest_arrivals_total")
    rain = panel.pivot(index="province_id", columns="year_month", values="rain_avg_mm")
    identity = (
        panel[["province_id", "province_en", "province_th", "region"]]
        .drop_duplicates("province_id")
        .set_index("province_id")
    )
    frames = []
    for month in _months(TIME_SPLIT["train"][0], TIME_SPLIT["online_stream"][1]):
        base = build_forecast_features(panel, month)
        sources = {
            "guest_lag_2": shift_year_month(month, 2),
            "guest_lag_3": shift_year_month(month, 3),
            "guest_lag_13": shift_year_month(month, 13),
        }
        assert_no_future_leakage(sources.values(), month)
        base["province_id"] = base["province_id"].astype(str).str.zfill(2)
        ids = base["province_id"]
        lag2 = _lookup(guest, ids, sources["guest_lag_2"])
        lag3 = _lookup(guest, ids, sources["guest_lag_3"])
        lag13 = _lookup(guest, ids, sources["guest_lag_13"])
        lag1 = base["guest_lag_1"].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            yoy = np.where(np.isfinite(lag13) & (lag13 > 0) & np.isfinite(lag1), lag1 / lag13, np.nan)
        calendar_month = int(month[5:7])
        angle = 2 * math.pi * calendar_month / 12
        actual = panel.loc[panel["year_month"] == month, ["province_id", "guest_arrivals_total"]].copy()
        actual["province_id"] = actual["province_id"].astype(str).str.zfill(2)
        frame = base.merge(actual, on="province_id", how="left", validate="one_to_one")
        frame["guest_lag_2"] = lag2
        frame["guest_lag_3"] = lag3
        frame["guest_lag_13"] = lag13
        frame["yoy_growth"] = yoy
        frame["month_sin"] = math.sin(angle)
        frame["month_cos"] = math.cos(angle)
        frame["calendar_month"] = calendar_month
        frame["year_month"] = month
        frame = frame.join(identity, on="province_id")
        frames.append(frame)
    table = pd.concat(frames, ignore_index=True)
    table["is_covid"] = table["is_covid"].astype(float)
    table["is_covid_lag12"] = table["is_covid_lag12"].astype(float)
    return table.sort_values(["year_month", "province_id"]).reset_index(drop=True)


def _rain_history(panel: pd.DataFrame) -> dict[tuple[str, int], tuple[np.ndarray, np.ndarray]]:
    history = {}
    work = panel[["province_id", "year_month", "month", "rain_avg_mm"]].copy()
    work["province_id"] = work["province_id"].astype(str).str.zfill(2)
    for (province_id, calendar_month), group in work.groupby(["province_id", "month"], sort=False):
        group = group.sort_values("year_month")
        history[(province_id, int(calendar_month))] = (
            group["year_month"].to_numpy(),
            group["rain_avg_mm"].to_numpy(dtype=float),
        )
    return history


def rain_anomaly(table: pd.DataFrame, history: dict, train_end: str) -> np.ndarray:
    """Province-month rain minus the mean of earlier months, capped at train_end."""
    out = np.zeros(len(table), dtype=float)
    months = table["year_month"].to_numpy()
    provinces = table["province_id"].to_numpy()
    calendar = table["calendar_month"].to_numpy()
    rain = table["rain_lag_1"].to_numpy(dtype=float)
    cache: dict[tuple, float] = {}
    for i in range(len(table)):
        limit = min(shift_year_month(str(months[i]), 1), train_end)
        key = (provinces[i], int(calendar[i]), limit)
        if key not in cache:
            labels, values = history[(provinces[i], int(calendar[i]))]
            taken = values[labels <= limit]
            taken = taken[np.isfinite(taken)]
            cache[key] = float(taken.mean()) if len(taken) else np.nan
        baseline = cache[key]
        out[i] = rain[i] - baseline if np.isfinite(rain[i]) and np.isfinite(baseline) else np.nan
    return out


def feature_columns(table: pd.DataFrame, feature_set: str) -> list[str]:
    provinces = sorted(table["province_id"].unique())
    one_hot = [f"province_{province_id}" for province_id in provinces]
    if feature_set == "base":
        return list(BASE_FEATURES)
    if feature_set == "ext":
        return list(BASE_FEATURES) + list(EXTRA_NUMERIC) + one_hot
    raise KeyError(feature_set)


def design_matrix(table: pd.DataFrame, columns: list[str], anomaly: np.ndarray) -> pd.DataFrame:
    frame = table[BASE_FEATURES + ["guest_lag_2", "guest_lag_3", "yoy_growth", "month_sin", "month_cos"]].copy()
    frame["rain_anomaly"] = anomaly
    provinces = sorted(table["province_id"].unique())
    ids = table["province_id"].to_numpy()
    for province_id in provinces:
        frame[f"province_{province_id}"] = (ids == province_id).astype(float)
    return frame[columns].apply(pd.to_numeric, errors="coerce")


def _fit_estimator(spec: Spec):
    params = dict(spec.params)
    if spec.family == "rf":
        return RandomForestRegressor(**params)
    if spec.family == "hgb":
        return HistGradientBoostingRegressor(**params)
    if spec.family == "ridge":
        return Ridge(**params)
    if spec.family == "lgbm":
        return LGBMRegressor(**params)
    raise KeyError(spec.family)


def _training_label(target: str, y: np.ndarray, lag12: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    finite = np.isfinite(y) & np.isfinite(lag12)
    if target == "level":
        return finite, y
    if target == "ratio":
        ok = finite & (lag12 > 0)
        label = np.zeros_like(y, dtype=float)
        label[ok] = y[ok] / lag12[ok]
        return ok, label
    if target == "log1p":
        ok = finite & (y >= 0)
        label = np.full_like(y, np.nan, dtype=float)
        label[ok] = np.log1p(y[ok])
        return ok, label
    if target == "logdiff":
        ok = finite & (y > 0) & (lag12 > 0)
        label = np.zeros_like(y, dtype=float)
        label[ok] = np.log(y[ok]) - np.log(lag12[ok])
        return ok, label
    raise KeyError(target)


def reconstruct(target: str, raw: np.ndarray, lag12: np.ndarray) -> np.ndarray:
    lag_ok = np.isfinite(lag12)
    if target == "level":
        pred = raw
    elif target == "ratio":
        pred = np.where(lag_ok & (lag12 > 0), lag12 * raw, np.where(lag_ok, lag12, 0.0))
    elif target == "log1p":
        pred = np.expm1(raw)
    elif target == "logdiff":
        pred = np.where(lag_ok & (lag12 > 0), lag12 * np.exp(raw), np.where(lag_ok, lag12, 0.0))
    else:
        raise KeyError(target)
    pred = np.where(np.isfinite(pred), pred, np.where(lag_ok, lag12, 0.0))
    return np.maximum(pred, 0.0)


class Fitted:
    def __init__(self, spec: Spec, estimator, scaler: StandardScaler | None, medians: pd.Series, columns: list[str], train_end: str):
        self.spec = spec
        self.estimator = estimator
        self.scaler = scaler
        self.medians = medians
        self.columns = columns
        self.train_end = train_end

    def predict(self, matrix: pd.DataFrame, lag12: np.ndarray) -> np.ndarray:
        filled = matrix.copy()
        for column in self.columns:
            filled[column] = filled[column].fillna(self.medians[column])
        values = filled[self.columns].to_numpy(dtype=float)
        if self.scaler is not None:
            values = self.scaler.transform(values)
        raw = np.asarray(self.estimator.predict(values), dtype=float)
        return reconstruct(self.spec.target, raw, lag12)


def fit_spec(spec: Spec, table: pd.DataFrame, matrix: pd.DataFrame, train_end: str) -> Fitted:
    train = table["year_month"].between(TIME_SPLIT["train"][0], train_end)
    y = table.loc[train, "guest_arrivals_total"].to_numpy(dtype=float)
    lag12 = table.loc[train, "guest_lag_12"].to_numpy(dtype=float)
    ok, label = _training_label(spec.target, y, lag12)
    base_ok = np.isfinite(matrix.loc[train, BASE_FEATURES].to_numpy(dtype=float)).all(axis=1)
    ok = ok & base_ok
    rows = matrix.loc[train].iloc[np.flatnonzero(ok)]
    medians = rows.median(numeric_only=True)
    filled = rows.fillna(medians)
    values = filled[matrix.columns].to_numpy(dtype=float)
    scaler = None
    if spec.family in {"rf", "ridge"}:
        scaler = StandardScaler().fit(values)
        values = scaler.transform(values)
    estimator = _fit_estimator(spec)
    estimator.fit(values, label[ok])
    if spec.family == "rf":
        estimator.n_jobs = 1
    return Fitted(spec, estimator, scaler, medians, list(matrix.columns), train_end)


def score_frame(table: pd.DataFrame, pred: np.ndarray) -> dict:
    y = table["guest_arrivals_total"].to_numpy(dtype=float)
    naive = table["guest_lag_12"].to_numpy(dtype=float)
    block = _block(y, pred, naive)
    block["beats_naive"] = bool(block["wape"] < _block(y, naive, naive)["wape"])
    block["naive_wape"] = _block(y, naive, naive)["wape"]
    return block


def specs() -> list[Spec]:
    built = []
    rf_grid = [
        ("d6_leaf3", dict(n_estimators=300, max_depth=6, min_samples_leaf=3, random_state=42, n_jobs=4)),
        ("d10_leaf3", dict(RF_CURRENT)),
        ("d16_leaf1", dict(n_estimators=300, max_depth=16, min_samples_leaf=1, random_state=42, n_jobs=4)),
    ]
    hgb_grid = [
        ("d3_lr0.1", dict(max_depth=3, learning_rate=0.1, max_iter=200, min_samples_leaf=20, random_state=42)),
        ("d6_lr0.05", dict(max_depth=6, learning_rate=0.05, max_iter=300, min_samples_leaf=20, random_state=42)),
    ]
    ridge_grid = [(f"a{alpha:g}", dict(alpha=alpha, random_state=42)) for alpha in (0.1, 1.0, 10.0, 100.0)]
    lgbm_grid = [
        ("leaves31_lr0.05", dict(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=20,
                                 subsample=1.0, colsample_bytree=1.0, random_state=42, n_jobs=1, verbosity=-1)),
        ("leaves15_lr0.1", dict(n_estimators=200, learning_rate=0.1, num_leaves=15, min_child_samples=20,
                                subsample=1.0, colsample_bytree=1.0, random_state=42, n_jobs=1, verbosity=-1)),
    ]
    families = [("rf", rf_grid), ("hgb", hgb_grid), ("ridge", ridge_grid), ("lgbm", lgbm_grid)]
    targets = ("level", "ratio", "log1p", "logdiff")
    for family, grid in families:
        for feature_set in ("base", "ext"):
            for target in targets:
                for tag, params in grid:
                    name = f"{family}|{target}|{feature_set}|{tag}"
                    built.append(Spec(name, family, target, feature_set, tuple(sorted(params.items()))))
    return built


def matrix_for(table: pd.DataFrame, history: dict, feature_set: str, train_end: str) -> pd.DataFrame:
    anomaly = rain_anomaly(table, history, train_end)
    columns = feature_columns(table, feature_set)
    return design_matrix(table, columns, anomaly)


def leakage_self_check(panel: pd.DataFrame, table: pd.DataFrame) -> None:
    month = "2024-06"
    full = build_forecast_features(panel, month)
    trimmed = panel.loc[panel["year_month"] < month]
    again = build_forecast_features(trimmed, month)
    pd.testing.assert_frame_equal(full.reset_index(drop=True), again.reset_index(drop=True))
    sources = [shift_year_month(month, lag) for lag in (1, 2, 3, 12, 13)]
    assert_no_future_leakage(sources, month)
    held = table.loc[table["year_month"] == month, ["guest_lag_2", "guest_lag_3", "guest_lag_13", "yoy_growth"]]
    rebuilt = build_feature_table(trimmed)
    other = rebuilt.loc[rebuilt["year_month"] == month, ["guest_lag_2", "guest_lag_3", "guest_lag_13", "yoy_growth"]]
    pd.testing.assert_frame_equal(held.reset_index(drop=True), other.reset_index(drop=True))


def blend(model_pred: np.ndarray, naive: np.ndarray, alpha: float) -> np.ndarray:
    mixed = alpha * model_pred + (1.0 - alpha) * np.maximum(np.where(np.isfinite(naive), naive, 0.0), 0.0)
    return np.maximum(mixed, 0.0)


def best_alpha(y: np.ndarray, model_pred: np.ndarray, naive: np.ndarray) -> tuple[float, dict]:
    alphas = [index / 20 for index in range(21)]
    chosen = 0.0
    chosen_score = None
    for alpha in alphas:
        score = _block(y, blend(model_pred, naive, alpha), naive)
        if chosen_score is None or score["wape"] < chosen_score["wape"] - 1e-15:
            chosen = alpha
            chosen_score = score
    return chosen, chosen_score


def province_report(table: pd.DataFrame, pred: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    clean = []
    pred_series = pd.Series(pred, index=table.index)
    for province_id, part in table.groupby("province_id", sort=True):
        y = part["guest_arrivals_total"].to_numpy(dtype=float)
        hat = pred_series.loc[part.index].to_numpy(dtype=float)
        naive = part["guest_lag_12"].to_numpy(dtype=float)
        clean.append(
            {
                "province_id": province_id,
                "province_en": part["province_en"].iloc[0],
                "region": part["region"].iloc[0],
                "n": int(len(part)),
                "wape_model": float(np.abs(y - hat).sum() / np.abs(y).sum()),
                "wape_naive": float(np.abs(y - naive).sum() / np.abs(y).sum()),
                "abs_error_model": float(np.abs(y - hat).sum()),
                "abs_error_naive": float(np.abs(y - naive).sum()),
                "improved": bool(np.abs(y - hat).sum() < np.abs(y - naive).sum()),
            }
        )
    provinces = pd.DataFrame(clean).sort_values("abs_error_naive", ascending=False)
    regions = []
    for region, part in table.groupby("region", sort=True):
        y = part["guest_arrivals_total"].to_numpy(dtype=float)
        hat = pred_series.loc[part.index].to_numpy(dtype=float)
        naive = part["guest_lag_12"].to_numpy(dtype=float)
        regions.append(
            {
                "region": region,
                "n": int(len(part)),
                "wape_model": float(np.abs(y - hat).sum() / np.abs(y).sum()),
                "wape_naive": float(np.abs(y - naive).sum() / np.abs(y).sum()),
                "abs_error_model": float(np.abs(y - hat).sum()),
                "abs_error_naive": float(np.abs(y - naive).sum()),
            }
        )
    return provinces, pd.DataFrame(regions)


def record(spec: Spec | None, split: str, metrics: dict, **flags) -> dict:
    row = {
        "name": "seasonal_naive" if spec is None else spec.name,
        "family": "naive" if spec is None else spec.family,
        "target": "lag12" if spec is None else spec.target,
        "feature_set": "" if spec is None else spec.feature_set,
        "params": "" if spec is None else json.dumps(dict(spec.params), sort_keys=True),
        "split": split,
        "current_rf": bool(spec and spec.family == "rf" and spec.target == "level" and spec.feature_set == "base"
                           and dict(spec.params) == RF_CURRENT),
    }
    row.update(flags)
    row.update(metrics)
    return row


def predict_split(fitted: Fitted, table: pd.DataFrame, history: dict, split_mask: pd.Series) -> np.ndarray:
    part = table.loc[split_mask]
    matrix = matrix_for(table, history, fitted.spec.feature_set, fitted.train_end)
    return fitted.predict(matrix.loc[split_mask], part["guest_lag_12"].to_numpy(dtype=float))


def main() -> None:
    panel = pd.read_parquet(PANEL_PATH)
    panel["province_id"] = panel["province_id"].astype(str).str.zfill(2)
    table = build_feature_table(panel)
    leakage_self_check(panel, table)
    history = _rain_history(panel)
    catalog = specs()
    train_end = TIME_SPLIT["train"][1]
    val_end = TIME_SPLIT["validation"][1]
    test_end = TIME_SPLIT["test"][1]
    masks = {
        "validation": table["year_month"].between(*TIME_SPLIT["validation"]),
        "test": table["year_month"].between(*TIME_SPLIT["test"]),
    }
    matrices = {
        feature_set: matrix_for(table, history, feature_set, train_end)
        for feature_set in ("base", "ext")
    }
    rows = []
    naive_pred = {
        split: table.loc[mask, "guest_lag_12"].to_numpy(dtype=float)
        for split, mask in masks.items()
    }
    for split, pred in naive_pred.items():
        rows.append(record(None, split, score_frame(table.loc[masks[split]], pred), alpha=None,
                           selected_overall=False, selected_hyperparams=False, blend=False))

    fitted: dict[str, Fitted] = {}
    val_pred: dict[str, np.ndarray] = {}
    test_pred: dict[str, np.ndarray] = {}
    for index, spec in enumerate(catalog, start=1):
        print(f"FIT {index}/{len(catalog)} {spec.name}", flush=True)
        model = fit_spec(spec, table, matrices[spec.feature_set], train_end)
        fitted[spec.name] = model
        for split, mask in masks.items():
            part = table.loc[mask]
            pred = model.predict(matrices[spec.feature_set].loc[mask], part["guest_lag_12"].to_numpy(dtype=float))
            if split == "validation":
                val_pred[spec.name] = pred
            else:
                test_pred[spec.name] = pred

    val_table = table.loc[masks["validation"]]
    y_val = val_table["guest_arrivals_total"].to_numpy(dtype=float)
    naive_val = naive_pred["validation"]
    val_scores = {name: score_frame(val_table, pred) for name, pred in val_pred.items()}
    groups: dict[tuple, list[str]] = {}
    for spec in catalog:
        groups.setdefault((spec.family, spec.target, spec.feature_set), []).append(spec.name)
    selected_hyper = set()
    for names in groups.values():
        winner = min(names, key=lambda name: val_scores[name]["wape"])
        selected_hyper.add(winner)

    blend_choice = {}
    blend_val = {}
    blend_test = {}
    y_test = table.loc[masks["test"], "guest_arrivals_total"].to_numpy(dtype=float)
    naive_test = naive_pred["test"]
    for spec in catalog:
        alpha, _ = best_alpha(y_val, val_pred[spec.name], naive_val)
        blend_choice[spec.name] = alpha
        blend_val[spec.name] = blend(val_pred[spec.name], naive_val, alpha)
        blend_test[spec.name] = blend(test_pred[spec.name], naive_test, alpha)

    candidates = []
    for spec in catalog:
        candidates.append((val_scores[spec.name]["wape"], spec.name, False))
        blend_metrics = score_frame(val_table, blend_val[spec.name])
        candidates.append((blend_metrics["wape"], spec.name, True))
    _, selected_name, selected_is_blend = min(candidates, key=lambda item: (item[0], item[2], item[1]))
    selected_spec = next(spec for spec in catalog if spec.name == selected_name)

    for spec in catalog:
        for split, mask, pred in (
            ("validation", masks["validation"], val_pred[spec.name]),
            ("test", masks["test"], test_pred[spec.name]),
        ):
            rows.append(record(
                spec, split, score_frame(table.loc[mask], pred),
                alpha=1.0, selected_overall=spec.name == selected_name and not selected_is_blend,
                selected_hyperparams=spec.name in selected_hyper, blend=False,
            ))
        for split, mask, pred in (
            ("validation", masks["validation"], blend_val[spec.name]),
            ("test", masks["test"], blend_test[spec.name]),
        ):
            rows.append(record(
                spec, split, score_frame(table.loc[mask], pred),
                alpha=blend_choice[spec.name],
                selected_overall=spec.name == selected_name and selected_is_blend,
                selected_hyperparams=False, blend=True,
            ))

    # 2025: seasonal naive, the frozen current RF, and the 2023-selected config.
    stream = table["year_month"].between(*TIME_SPLIT["online_stream"])
    stream_table = table.loc[stream]
    naive_2025 = stream_table["guest_lag_12"].to_numpy(dtype=float)
    rows.append(record(None, "2025_frozen", score_frame(stream_table, naive_2025), alpha=None,
                       selected_overall=False, selected_hyperparams=False, blend=False))
    rows.append(record(None, "2025_monthly", score_frame(stream_table, naive_2025), alpha=None,
                       selected_overall=False, selected_hyperparams=False, blend=False))

    def eval_2025(spec: Spec, is_selected: bool) -> None:
        frozen_model = fit_spec(spec, table, matrix_for(table, history, spec.feature_set, test_end), test_end)
        frozen_pred = predict_split(frozen_model, table, history, stream)
        alpha = blend_choice[spec.name] if is_selected and selected_is_blend else 1.0
        if is_selected and selected_is_blend:
            frozen_pred = blend(frozen_pred, naive_2025, alpha)
        rows.append(record(spec, "2025_frozen", score_frame(stream_table, frozen_pred), alpha=alpha,
                           selected_overall=is_selected, selected_hyperparams=spec.name in selected_hyper,
                           blend=bool(is_selected and selected_is_blend)))
        monthly = []
        for month in _months(*TIME_SPLIT["online_stream"]):
            end = shift_year_month(month, 1)
            model = fit_spec(spec, table, matrix_for(table, history, spec.feature_set, end), end)
            mask = table["year_month"] == month
            pred = predict_split(model, table, history, mask)
            if is_selected and selected_is_blend:
                pred = blend(pred, table.loc[mask, "guest_lag_12"].to_numpy(dtype=float), alpha)
            monthly.append(pred)
        monthly_pred = np.concatenate(monthly)
        rows.append(record(spec, "2025_monthly", score_frame(stream_table, monthly_pred), alpha=alpha,
                           selected_overall=is_selected, selected_hyperparams=spec.name in selected_hyper,
                           blend=bool(is_selected and selected_is_blend)))
        return frozen_pred if not is_selected else None

    current = next(spec for spec in catalog if spec.family == "rf" and spec.target == "level"
                   and spec.feature_set == "base" and dict(spec.params) == RF_CURRENT)
    print("2025 current RF", flush=True)
    eval_2025(current, current.name == selected_name and not selected_is_blend)
    if not (current.name == selected_name and not selected_is_blend):
        print("2025 selected", selected_spec.name, "blend" if selected_is_blend else "raw", flush=True)
        eval_2025(selected_spec, True)

    test_hat = blend_test[selected_name] if selected_is_blend else test_pred[selected_name]
    provinces, regions = province_report(table.loc[masks["test"]], test_hat)

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    current_2024 = next(row for row in rows if row["current_rf"] and row["split"] == "test" and not row["blend"])
    sanity = {
        "baseline_file_wape": baseline["metrics"]["overall"]["wape"],
        "recomputed_current_rf_2024_wape": current_2024["wape"],
        "abs_diff": abs(current_2024["wape"] - baseline["metrics"]["overall"]["wape"]),
    }
    payload = {
        "protocol": {
            "train": list(TIME_SPLIT["train"]),
            "validation_for_selection": list(TIME_SPLIT["validation"]),
            "test_scored_once": list(TIME_SPLIT["test"]),
            "refit_for_2025": ["2019-01", "2024-12"],
            "stream": list(TIME_SPLIT["online_stream"]),
            "selection": "minimum 2023 WAPE among raw models and their 2023-chosen blends",
            "blend_alpha_grid": [index / 20 for index in range(21)],
            "blend_tie": "smallest alpha when 2023 WAPE ties",
            "rain_anomaly": "mean of the same province-calendar month strictly before the row and no later than the fit's train end",
            "frozen_2025": "one fit on 2019-01..2024-12; lag features still use revealed months before t",
            "monthly_2025": "refit on every month strictly before t, then predict t",
            "lightgbm": "4.7.0",
        },
        "selected": {
            "name": selected_name,
            "blend": selected_is_blend,
            "alpha": blend_choice[selected_name] if selected_is_blend else 1.0,
        },
        "sanity_current_rf_2024": sanity,
        "rows": rows,
        "provinces_improved_2024": int(provinces["improved"].sum()),
        "provinces_scored_2024": int(len(provinces)),
        "regions_2024": regions.to_dict("records"),
        "top10_naive_error_2024": provinces.head(10).to_dict("records"),
    }
    (HERE / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    pd.DataFrame(rows).to_csv(HERE / "results.csv", index=False)
    provinces.to_csv(HERE / "provinces_2024.csv", index=False)
    regions.to_csv(HERE / "regions_2024.csv", index=False)
    print(json.dumps({"selected": payload["selected"], "sanity": sanity,
                      "improved": payload["provinces_improved_2024"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
