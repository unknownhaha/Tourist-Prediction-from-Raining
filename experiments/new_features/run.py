"""Protocol B for five leakage-safe feature groups on the log-diff forest.

Train 2019-01..2023-12, choose the feature subset on 2024 WAPE, then score
2025 frozen and monthly. 2025 is not used to choose.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.beat_seasonal_naive.run import (  # noqa: E402
    RF_CURRENT,
    Spec,
    _rain_history,
    build_feature_table,
    fit_spec,
    matrix_for,
)
from experiments.new_features.dq import DataQualityError, assess_feature_inputs  # noqa: E402
from experiments.new_features.features import FEATURE_GROUPS, build_new_features  # noqa: E402
from experiments.new_features.validate_inputs import FeatureValidationError, validate_feature_inputs, write_validation_report  # noqa: E402
from src.common.paths import DATA  # noqa: E402
from src.model.leakage import TIME_SPLIT, build_forecast_features, shift_year_month  # noqa: E402
from src.stream.champion_policy import promotion_rule  # noqa: E402
from src.stream.drift import PageHinkley  # noqa: E402
from src.stream.metrics import _block, wape  # noqa: E402
from src.stream.studio_models import PH_DELTA, PH_THRESHOLD, _proportions, _reference_bins, psi_alarm, psi_score  # noqa: E402

HERE = Path(__file__).resolve().parent
LOGDIFF = Spec("rf|logdiff|ext|d10_leaf3", "rf", "logdiff", "ext", tuple(sorted(RF_CURRENT.items())))
LEVEL = Spec("rf|level|base|d10_leaf3", "rf", "level", "base", tuple(sorted(RF_CURRENT.items())))
STREAM = [f"2025-{month:02d}" for month in range(1, 13)]
CANDIDATES = ("logdiff_expanding", "logdiff_12m", "rf_expanding", "rf_12m", "linear_expanding")
FROZEN_TARGET = 0.04788460348726727
MONTHLY_TARGET = 0.04829016837105007


def _load() -> pd.DataFrame:
    panel = pd.read_parquet(DATA / "model" / "province_month_unified.parquet")
    panel["province_id"] = panel["province_id"].astype(str).str.zfill(2)
    panel["year_month"] = panel["year_month"].astype(str)
    return panel.sort_values(["year_month", "province_id"]).reset_index(drop=True)


def _matrix(table: pd.DataFrame, history: dict, train_end: str, extra: tuple[str, ...]) -> pd.DataFrame:
    base = matrix_for(table, history, "ext", train_end)
    if not extra:
        return base
    added = table.loc[base.index, list(extra)].apply(pd.to_numeric, errors="coerce")
    return pd.concat([base, added], axis=1)


def _predict_total(model, matrix: pd.DataFrame, table: pd.DataFrame, mask: pd.Series) -> np.ndarray:
    part = table.loc[mask]
    return model.predict(matrix.loc[mask], part["guest_lag_12"].to_numpy(dtype=float))


def _fit_split(table: pd.DataFrame, matrix: pd.DataFrame, train_end: str):
    models = {}
    for name, value_col, lag_col in (
        ("thai", "guest_arrivals_thai", "thai_lag_12"),
        ("foreign", "guest_arrivals_foreign", "foreign_lag_12"),
    ):
        renamed = table.copy()
        renamed["guest_arrivals_total"] = renamed[value_col]
        renamed["guest_lag_12"] = renamed[lag_col]
        models[name] = fit_spec(LOGDIFF, renamed, matrix, train_end)
        models[name + "_lag"] = lag_col
    return models


def _predict_split(models, matrix: pd.DataFrame, table: pd.DataFrame, mask: pd.Series) -> np.ndarray:
    part = table.loc[mask]
    thai = models["thai"].predict(matrix.loc[mask], part["thai_lag_12"].to_numpy(dtype=float))
    foreign = models["foreign"].predict(matrix.loc[mask], part["foreign_lag_12"].to_numpy(dtype=float))
    return np.maximum(thai + foreign, 0.0)


def _score_mask(table: pd.DataFrame, pred: np.ndarray, mask: pd.Series) -> dict:
    part = table.loc[mask]
    block = _block(part["guest_arrivals_total"].to_numpy(dtype=float), pred, part["guest_lag_12"].to_numpy(dtype=float))
    naive = _block(part["guest_arrivals_total"].to_numpy(dtype=float), part["guest_lag_12"].to_numpy(dtype=float), part["guest_lag_12"].to_numpy(dtype=float))
    block["naive_wape"] = naive["wape"]
    block["beats_naive"] = bool(block["wape"] < naive["wape"])
    return block


def _monthly(table, history, extra, split: bool) -> tuple[np.ndarray, list[dict]]:
    pieces = []
    rows = []
    for month in STREAM:
        end = shift_year_month(month, 1)
        matrix = _matrix(table, history, end, extra)
        mask = table["year_month"] == month
        if split:
            pred = _predict_split(_fit_split(table, matrix, end), matrix, table, mask)
        else:
            pred = _predict_total(fit_spec(LOGDIFF, table, matrix, end), matrix, table, mask)
        pieces.append(pred)
        part = table.loc[mask]
        y = part["guest_arrivals_total"].to_numpy(dtype=float)
        rows.append({
            "year_month": month,
            "wape_model": wape(y, pred),
            "wape_naive": wape(y, part["guest_lag_12"].to_numpy(dtype=float)),
            "n": int(len(part)),
        })
    return np.concatenate(pieces), rows


def _provinces_improved(table: pd.DataFrame, pred: np.ndarray, mask: pd.Series) -> int:
    part = table.loc[mask, ["province_id", "guest_arrivals_total", "guest_lag_12"]].copy()
    part["abs_model"] = np.abs(part["guest_arrivals_total"].to_numpy(dtype=float) - pred)
    part["abs_naive"] = np.abs(part["guest_arrivals_total"] - part["guest_lag_12"])
    improved = 0
    for _, group in part.groupby("province_id"):
        if group["abs_model"].sum() < group["abs_naive"].sum():
            improved += 1
    return improved


def _psi_bins(panel: pd.DataFrame) -> dict:
    frames = []
    for month in pd.period_range("2023-01", "2024-12", freq="M").strftime("%Y-%m"):
        features = build_forecast_features(panel, month)
        features["province_id"] = features["province_id"].astype(str).str.zfill(2)
        features["year_month"] = month
        frames.append(features)
    return _reference_bins(pd.concat(frames, ignore_index=True))


def _psi(month_table: pd.DataFrame, month: str, bins: dict) -> dict:
    results = {}
    for name, (edges, reference) in bins[month[-2:]].items():
        current = _proportions(month_table[name], edges)
        score = psi_score(reference, current)
        results[name] = {"value": score, "alarm": bool(psi_alarm(score))}
    return results


def _fit_named(name: str, table, history, extra, train_end: str, split: bool):
    last = 12 if name.endswith("_12m") else None
    rows = table.loc[table["year_month"].between("2019-01", train_end)]
    if last is not None:
        keep = list(dict.fromkeys(rows["year_month"]))[-last:]
        rows = rows.loc[rows["year_month"].isin(keep)]
    if name.startswith("logdiff"):
        matrix = _matrix(rows, history, train_end, extra if not split else ())
        if split:
            return ("split", _fit_split(rows, matrix, train_end))
        return ("single", fit_spec(LOGDIFF, rows, matrix, train_end))
    if name.startswith("rf_"):
        matrix = matrix_for(rows, history, "base", train_end)
        return ("single", fit_spec(LEVEL, rows, matrix, train_end))
    train = rows["year_month"].between("2019-01", train_end)
    values = rows.loc[train, ["guest_lag_1", "guest_lag_12", "rain_lag_1", "rain_roll3", "is_covid", "is_covid_lag12"]]
    ok = np.isfinite(values.to_numpy(dtype=float)).all(axis=1)
    y = rows.loc[train, "guest_arrivals_total"].to_numpy(dtype=float)[ok]
    scaler = StandardScaler().fit(values.loc[ok].to_numpy(dtype=float))
    model = LinearRegression().fit(scaler.transform(values.loc[ok].to_numpy(dtype=float)), y)

    def predict(month_table: pd.DataFrame) -> np.ndarray:
        frame = month_table[list(values.columns)].to_numpy(dtype=float)
        raw = model.predict(scaler.transform(np.nan_to_num(frame, nan=0.0)))
        return np.maximum(raw, 0.0)

    return ("linear", predict)


def _predict_named(kind, fitted, month_table, history, extra, train_end: str) -> np.ndarray:
    if kind == "linear":
        return fitted(month_table)
    if kind == "split":
        matrix = _matrix(month_table, history, train_end, ())
        return _predict_split(fitted, matrix, month_table, pd.Series(True, index=month_table.index))
    feature_set = "ext" if fitted.spec.feature_set == "ext" else "base"
    if feature_set == "ext":
        matrix = _matrix(month_table, history, train_end, extra)
    else:
        matrix = matrix_for(month_table, history, "base", train_end)
    return fitted.predict(matrix, month_table["guest_lag_12"].to_numpy(dtype=float))


def _champion(table, history, extra, split: bool, preset: str, bins: dict) -> dict:
    rule = None if preset == "no_promotion" else promotion_rule(preset)
    kind, active = _fit_named("logdiff_expanding", table, history, extra, "2024-12", split)
    active_name, active_end = "logdiff_expanding", "2024-12"
    detectors = {name: PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=12) for name in CANDIDATES}
    calibration = {}
    for name in CANDIDATES:
        scores = []
        if name.endswith("_12m"):
            for month in pd.period_range("2023-01", "2023-12", freq="M").strftime("%Y-%m"):
                end = shift_year_month(month, 1)
                fit_kind, fitted = _fit_named(name, table, history, extra, end, split and name.startswith("logdiff"))
                month_table = table.loc[table["year_month"] == month].sort_values("province_id")
                pred = _predict_named(fit_kind, fitted, month_table, history, extra, end)
                scores.append(wape(month_table["guest_arrivals_total"].to_numpy(dtype=float), pred))
        else:
            fit_kind, fitted = _fit_named(name, table, history, extra, "2022-12", split and name.startswith("logdiff"))
            for month in pd.period_range("2023-01", "2023-12", freq="M").strftime("%Y-%m"):
                month_table = table.loc[table["year_month"] == month].sort_values("province_id")
                pred = _predict_named(fit_kind, fitted, month_table, history, extra, "2022-12")
                scores.append(wape(month_table["guest_arrivals_total"].to_numpy(dtype=float), pred))
        calibration[name] = (float(np.mean(scores)), float(np.std(scores, ddof=1)))
    streak = {name: 0 for name in CANDIDATES}
    last_rain = None
    triggers, promotions, served = [], [], []
    for month in STREAM:
        month_table = table.loc[table["year_month"] == month].sort_values("province_id")
        serving = active_name
        pred = _predict_named(kind, active, month_table, history, extra, active_end)
        y = month_table["guest_arrivals_total"].to_numpy(dtype=float)
        lag = month_table["guest_lag_12"].to_numpy(dtype=float)
        score = _block(y, pred, lag)
        psi = _psi_from(month_table, month, bins)
        cutoff = shift_year_month(month, 1)
        rain = psi["rain_lag_1"]
        exceeded = rain["alarm"]
        rain["alarm"] = bool(exceeded and last_rain == cutoff)
        rain["pending_confirmation"] = bool(exceeded and not rain["alarm"])
        last_rain = month if exceeded else None
        mean, std = calibration[serving]
        ph_alarm = bool(detectors[serving].update((score["wape"] - mean) / std))
        reasons = [name for name, event in psi.items() if event["alarm"]]
        if ph_alarm:
            reasons.append("Page-Hinkley")
        decision = "KEEP"
        if rule is not None and reasons:
            fitted, scores = {}, {}
            for name in CANDIDATES:
                fit_kind, model = _fit_named(name, table, history, extra, cutoff, split and name.startswith("logdiff"))
                fitted[name] = (fit_kind, model)
                scores[name] = _block(y, _predict_named(fit_kind, model, month_table, history, extra, cutoff), lag)
            for name in CANDIDATES:
                streak[name] = streak.get(name, 0) + 1 if rule.beats(scores[name], score) else 0
            winners = [name for name in CANDIDATES if streak[name]]
            if winners:
                best = min(winners, key=lambda name: scores[name]["wape"])
                if streak[best] >= rule.confirm_months:
                    kind, active = fitted[best]
                    active_name, active_end = best, cutoff
                    if rule.seed_detector:
                        seeded = PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=0)
                        center, scale = calibration[best]
                        revealed = [item for item in STREAM if item <= month]
                        values = []
                        for past in revealed[-12:-1]:
                            end = shift_year_month(past, 1)
                            fit_kind, model = _fit_named(best, table, history, extra, end, split and best.startswith("logdiff"))
                            past_table = table.loc[table["year_month"] == past].sort_values("province_id")
                            values.append(wape(past_table["guest_arrivals_total"].to_numpy(dtype=float),
                                               _predict_named(fit_kind, model, past_table, history, extra, end)))
                        values.append(scores[best]["wape"])
                        for value in values[-12:]:
                            seeded.update((value - center) / scale)
                        detectors[best] = seeded
                    else:
                        detectors[best] = PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=12)
                    decision = "PROMOTE"
                    promotions.append({"month": month, "winner": best, "effective_from": str(pd.Period(month, freq="M") + 1)})
        elif rule is not None:
            streak = {name: 0 for name in CANDIDATES}
        if reasons:
            triggers.append({"month": month, "trigger": reasons})
        for province_id, y_i, pred_i, lag_i in zip(month_table["province_id"], y, pred, lag):
            served.append({"year_month": month, "province_id": province_id, "y_true": float(y_i), "y_pred": float(pred_i), "y_naive": float(lag_i)})
    frame = pd.DataFrame(served)
    overall = _block(frame["y_true"].to_numpy(), frame["y_pred"].to_numpy(), frame["y_naive"].to_numpy())
    return {"preset": preset, "triggers": triggers, "promotions": promotions, "served": overall}


def _psi_from(month_table, month, bins):
    results = {}
    for name, (edges, reference) in bins[month[-2:]].items():
        current = _proportions(month_table[name], edges)
        score = psi_score(reference, current)
        results[name] = {"value": score, "alarm": bool(psi_alarm(score))}
    return results


def main() -> None:
    panel = _load()
    print("FEATURES", flush=True)
    base = build_feature_table(panel)
    extra = build_new_features(panel)
    base["province_id"] = base["province_id"].astype(str).str.zfill(2)
    table = base.merge(extra.drop(columns=["region"]), on=["province_id", "year_month"], validate="one_to_one")
    arrivals = panel.loc[panel["year_month"] >= "2019-01", ["province_id", "year_month", "guest_arrivals_thai", "guest_arrivals_foreign"]]
    table = table.merge(arrivals, on=["province_id", "year_month"], validate="one_to_one")
    try:
        validation = validate_feature_inputs(panel, extra)
    except FeatureValidationError as exc:
        write_validation_report(exc.report, HERE / "validation_report.json")
        raise
    write_validation_report(validation, HERE / "validation_report.json")
    try:
        quality = assess_feature_inputs(panel, extra)
    except DataQualityError as exc:
        (HERE / "dq_report.json").write_text(json.dumps(exc.report, indent=2), encoding="utf-8")
        raise
    (HERE / "dq_report.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    print("DQ", quality["scores"], flush=True)
    history = _rain_history(panel)
    groups = list(FEATURE_GROUPS)
    subsets = []
    for size in range(len(groups) + 1):
        for combo in itertools.combinations(groups, size):
            columns = tuple(column for name in combo for column in FEATURE_GROUPS[name])
            subsets.append(("|".join(combo) if combo else "baseline", columns, False))
    subsets.append(("split_models", (), True))
    val_mask = table["year_month"].between("2024-01", "2024-12")
    matrix_2023 = _matrix(table, history, "2023-12", ())
    print("SELECT", len(subsets), flush=True)
    selection = []
    for index, (name, columns, split) in enumerate(subsets, start=1):
        matrix = matrix_2023 if not columns else pd.concat(
            [matrix_2023, table.loc[matrix_2023.index, list(columns)]], axis=1
        )
        if split:
            pred = _predict_split(_fit_split(table, matrix, "2023-12"), matrix, table, val_mask)
        else:
            pred = _predict_total(fit_spec(LOGDIFF, table, matrix, "2023-12"), matrix, table, val_mask)
        metrics = _score_mask(table, pred, val_mask)
        selection.append({"name": name, "features": list(columns), "split": split, **metrics})
        print(f"  {index}/{len(subsets)} {name} {metrics['wape']:.6f}", flush=True)
    selection.sort(key=lambda row: (row["wape"], len(row["features"]), row["name"]))
    winner = selection[0]
    report_names = {"baseline", "split", "yoy3", "region", "holidays", "festivals", "split|yoy3|region|holidays|festivals", winner["name"], "split_models"}
    stream_mask = table["year_month"].between("2025-01", "2025-12")
    rows = []
    month_rows = []
    focus = {}
    for item in selection:
        record = {"stage": "2024_validation", **item}
        rows.append(record)
        if item["name"] not in report_names:
            continue
        print("2025", item["name"], flush=True)
        columns = tuple(item["features"])
        split = item["split"]
        matrix = _matrix(table, history, "2024-12", columns)
        if split:
            frozen = _predict_split(_fit_split(table, matrix, "2024-12"), matrix, table, stream_mask)
        else:
            frozen = _predict_total(fit_spec(LOGDIFF, table, matrix, "2024-12"), matrix, table, stream_mask)
        frozen_metrics = _score_mask(table, frozen, stream_mask)
        monthly, by_month = _monthly(table, history, columns, split)
        monthly_metrics = _score_mask(table, monthly, stream_mask)
        rows.append({"stage": "2025_frozen", "name": item["name"], **frozen_metrics,
                     "provinces_improved": _provinces_improved(table, frozen, stream_mask)})
        rows.append({"stage": "2025_monthly", "name": item["name"], **monthly_metrics,
                     "provinces_improved": _provinces_improved(table, monthly, stream_mask)})
        for month in by_month:
            month_rows.append({"name": item["name"], "mode": "monthly_model_vs_naive", **month})
            # frozen month slice
        part = table.loc[stream_mask].reset_index(drop=True)
        cursor = 0
        for month in STREAM:
            block = part["year_month"] == month
            count = int(block.sum())
            y = part.loc[block, "guest_arrivals_total"].to_numpy(dtype=float)
            month_rows.append({
                "name": item["name"],
                "mode": "frozen",
                "year_month": month,
                "wape_model": wape(y, frozen[cursor:cursor + count]),
                "wape_naive": wape(y, part.loc[block, "guest_lag_12"].to_numpy(dtype=float)),
                "n": count,
            })
            cursor += count
        focus[item["name"]] = {"frozen": frozen_metrics, "monthly": monthly_metrics}
    bins = _psi_bins(panel)
    champion = []
    for preset in ("legacy", "margin_0005_confirm_2", "margin_001_confirm_2"):
        print("CHAMPION", preset, flush=True)
        champion.append(_champion(table, history, tuple(winner["features"]), winner["split"], preset, bins))
    payload = {
        "selected_on_2024": winner,
        "baseline_check": {
            "protocol_b_2025_frozen": FROZEN_TARGET,
            "protocol_b_2025_monthly": MONTHLY_TARGET,
            "recomputed_frozen": focus["baseline"]["frozen"]["wape"],
            "recomputed_monthly": focus["baseline"]["monthly"]["wape"],
        },
        "selection": selection,
        "focus": focus,
        "months": month_rows,
        "champion": champion,
        "validation_warnings": validation["n_warnings"],
        "dq_scores": quality["scores"],
    }
    (HERE / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    pd.DataFrame(rows).to_csv(HERE / "results.csv", index=False)
    pd.DataFrame(month_rows).to_csv(HERE / "monthly_2025.csv", index=False)
    print(json.dumps({"winner": winner["name"], "wape_2024": winner["wape"],
                      "baseline_frozen": focus["baseline"]["frozen"]["wape"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
