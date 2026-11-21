"""Rolling-origin backtest for 2023 and 2024.

Target 2023: frozen models train on 2019-01..2022-12.
Target 2024: frozen models train on 2019-01..2023-12.
Monthly refit: an expanding window from 2019-01 through t-1 for every month t.
2018 is lag history only. Every feature for month t reads months before t.
No hyperparameter is tuned here; the specs are copied from earlier runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.beat_seasonal_naive.run import _rain_history, build_feature_table, fit_spec, matrix_for  # noqa: E402
from experiments.new_features.dq import DataQualityError, assess_feature_inputs  # noqa: E402
from experiments.new_features.features import YOY3_COLUMNS, build_new_features  # noqa: E402
from experiments.new_features.run import LEVEL, LOGDIFF, _load, _matrix  # noqa: E402
from experiments.new_features.validate_inputs import (  # noqa: E402
    FeatureValidationError,
    validate_feature_inputs,
    write_validation_report,
)
from src.model.leakage import shift_year_month  # noqa: E402
from src.stream.metrics import _block, wape  # noqa: E402

HERE = Path(__file__).resolve().parent
MODELS = ("rf_logdiff", "rf_level", "rf_logdiff_yoy3", "seasonal_naive")
TARGETS = {2023: "2022-12", 2024: "2023-12"}
KNOWN_2025 = {
    "rf_logdiff": {"frozen": 0.04788460348726727, "monthly": 0.04829016837105007},
    "seasonal_naive": {"frozen": 0.05480419201387209, "monthly": 0.05480419201387209},
    "rf_level": {"frozen": "about 0.0627", "monthly": "about 0.0596"},
    "rf_logdiff_yoy3": {"frozen": "0.0469", "monthly": "0.0470"},
}


def build_table(panel: pd.DataFrame) -> pd.DataFrame:
    base = build_feature_table(panel)
    extra = build_new_features(panel)
    base["province_id"] = base["province_id"].astype(str).str.zfill(2)
    table = base.merge(extra.drop(columns=["region"]), on=["province_id", "year_month"], validate="one_to_one")
    return table.sort_values(["year_month", "province_id"]).reset_index(drop=True)


def check_inputs(panel: pd.DataFrame) -> tuple[dict, dict]:
    extra = build_new_features(panel)
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
    return validation, quality


def training_window(table: pd.DataFrame, train_end: str) -> pd.DataFrame:
    """Rows a model may learn from: 2019-01 through train_end, nothing later."""
    return table.loc[table["year_month"].between("2019-01", train_end)]


def predict(model: str, table: pd.DataFrame, history: dict, train_end: str, months: list[str], spec=None) -> np.ndarray:
    """Fit on rows up to train_end, predict `months`. Every month must be after train_end."""
    if any(month <= train_end for month in months):
        raise AssertionError(f"target month not after train_end {train_end}")
    mask = table["year_month"].isin(months)
    part = table.loc[mask]
    lag12 = part["guest_lag_12"].to_numpy(dtype=float)
    if model == "seasonal_naive":
        return lag12.copy()
    # Truncate the table so no row after train_end can reach the fit or the
    # rain-anomaly baseline; the target rows carry only t-1 features.
    visible = pd.concat([training_window(table, train_end), part]).sort_values(["year_month", "province_id"])
    if model == "rf_level":
        matrix = matrix_for(visible, history, "base", train_end)
        fitted = fit_spec(spec or LEVEL, visible, matrix, train_end)
    else:
        extra = tuple(YOY3_COLUMNS) if model == "rf_logdiff_yoy3" else ()
        matrix = _matrix(visible, history, train_end, extra)
        fitted = fit_spec(spec or LOGDIFF, visible, matrix, train_end)
    assert fitted.train_end == train_end
    target_index = visible.index[visible["year_month"].isin(months)]
    pred = fitted.predict(matrix.loc[target_index], visible.loc[target_index, "guest_lag_12"].to_numpy(dtype=float))
    # Restore table order.
    return pd.Series(pred, index=target_index).reindex(part.index).to_numpy(dtype=float)


def run_year(table: pd.DataFrame, history: dict, year: int, model: str, mode: str, spec=None) -> np.ndarray:
    months = [f"{year}-{m:02d}" for m in range(1, 13)]
    if mode == "frozen":
        return predict(model, table, history, TARGETS[year] if year in TARGETS else f"{year - 1}-12", months, spec)
    pieces = [predict(model, table, history, shift_year_month(month, 1), [month], spec) for month in months]
    return np.concatenate(pieces)


def score(part: pd.DataFrame, pred: np.ndarray) -> dict:
    y = part["guest_arrivals_total"].to_numpy(dtype=float)
    naive = part["guest_lag_12"].to_numpy(dtype=float)
    block = _block(y, pred, naive)
    block["naive_wape"] = wape(y, naive)
    block["beats_naive"] = bool(block["wape"] < block["naive_wape"])
    block["bias"] = float(pred.sum() / y.sum() - 1.0)
    frame = part[["province_id"]].copy()
    frame["model"] = np.abs(y - pred)
    frame["naive"] = np.abs(y - naive)
    sums = frame.groupby("province_id")[["model", "naive"]].sum()
    block["provinces_beating_naive"] = int((sums["model"] < sums["naive"]).sum())
    block["provinces"] = int(len(sums))
    return block


def per_month(part: pd.DataFrame, pred: np.ndarray) -> dict:
    out = {}
    y = part["guest_arrivals_total"].to_numpy(dtype=float)
    months = part["year_month"].to_numpy()
    for month in sorted(set(months)):
        keep = months == month
        out[month] = wape(y[keep], pred[keep])
    return out


def logdiff_2023_analysis(table: pd.DataFrame, pred_frozen: np.ndarray) -> dict:
    part = table.loc[table["year_month"].between("2023-01", "2023-12")].copy()
    train = table.loc[table["year_month"].between("2019-01", "2022-12")]
    y = part["guest_arrivals_total"].to_numpy(dtype=float)
    lag = part["guest_lag_12"].to_numpy(dtype=float)
    ok = (y > 0) & (lag > 0)
    part["log_ratio"] = np.where(ok, np.log(np.where(ok, y, 1)) - np.log(np.where(ok, lag, 1)), np.nan)
    part["pred"] = pred_frozen
    part["pred_log_ratio"] = np.where(ok & (pred_frozen > 0), np.log(np.where(ok & (pred_frozen > 0), pred_frozen, 1)) - np.log(np.where(ok, lag, 1)), np.nan)
    ty = train["guest_arrivals_total"].to_numpy(dtype=float)
    tl = train["guest_lag_12"].to_numpy(dtype=float)
    tok = (ty > 0) & (tl > 0)
    train_ratio = np.log(ty[tok]) - np.log(tl[tok])
    qs = [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]

    def describe(values: np.ndarray) -> dict:
        values = values[np.isfinite(values)]
        return {"n": int(len(values)), "mean": float(values.mean()), "std": float(values.std(ddof=1)),
                **{f"q{int(q * 100):02d}": float(np.quantile(values, q)) for q in qs},
                "share_abs_gt_log2": float(np.mean(np.abs(values) > np.log(2))),
                "share_gt_log2": float(np.mean(values > np.log(2)))}

    by_month = (part.groupby("year_month")
                .apply(lambda g: pd.Series({
                    "median_actual_log_ratio": float(np.nanmedian(g["log_ratio"])),
                    "median_pred_log_ratio": float(np.nanmedian(g["pred_log_ratio"])),
                    "share_ratio_gt_2x": float(np.nanmean(g["log_ratio"] > np.log(2))),
                }), include_groups=False)
                .reset_index().to_dict(orient="records"))
    extreme = part.loc[np.isfinite(part["log_ratio"])].assign(ratio=lambda f: np.exp(f["log_ratio"]))
    extreme = extreme.reindex(extreme["log_ratio"].abs().sort_values(ascending=False).index).head(10)
    extreme_rows = [
        {"province_id": r.province_id, "province_en": r.province_en, "year_month": r.year_month,
         "actual": float(r.guest_arrivals_total), "lag_12": float(r.guest_lag_12), "ratio": float(r.ratio),
         "pred": float(r.pred)}
        for r in extreme.itertuples()
    ]
    part["abs_model"] = np.abs(y - pred_frozen)
    part["abs_naive"] = np.abs(y - lag)
    prov = part.groupby(["province_id", "province_en"]).agg(
        actual=("guest_arrivals_total", "sum"), abs_model=("abs_model", "sum"), abs_naive=("abs_naive", "sum"),
        pred=("pred", "sum"), lag12=("guest_lag_12", "sum"),
    ).reset_index()
    prov["wape_model"] = prov["abs_model"] / prov["actual"]
    prov["wape_naive"] = prov["abs_naive"] / prov["actual"]
    prov["share_of_total_abs_error"] = prov["abs_model"] / prov["abs_model"].sum()
    worst = prov.sort_values("abs_model", ascending=False).head(10)
    return {
        "actual_log_ratio_2023": describe(part["log_ratio"].to_numpy(dtype=float)),
        "predicted_log_ratio_2023_frozen": describe(part["pred_log_ratio"].to_numpy(dtype=float)),
        "training_label_2019_2022": describe(train_ratio),
        "by_month": by_month,
        "extreme_ratios": extreme_rows,
        "worst_provinces_by_abs_error": worst.to_dict(orient="records"),
    }


def main() -> None:
    panel = _load()
    validation, quality = check_inputs(panel)
    print("VALIDATION warnings", validation.get("n_warnings"), "DQ", quality["scores"], flush=True)
    table = build_table(panel)
    history = _rain_history(panel)
    rows, month_rows, preds = [], [], {}
    for year in (2023, 2024, 2025):
        part = table.loc[table["year_month"].between(f"{year}-01", f"{year}-12")]
        for model in MODELS:
            for mode in ("frozen", "monthly"):
                if model == "seasonal_naive" and mode == "monthly":
                    pred = preds[(year, model, "frozen")]
                else:
                    pred = run_year(table, history, year, model, mode)
                preds[(year, model, mode)] = pred
                metrics = score(part, pred)
                rows.append({"year": year, "model": model, "mode": mode, **metrics})
                for month, value in per_month(part, pred).items():
                    month_rows.append({"year": year, "model": model, "mode": mode, "year_month": month, "wape": value})
                print(year, model, mode, round(metrics["wape"], 6), flush=True)
    part_2023 = table.loc[table["year_month"].between("2023-01", "2023-12")]
    analysis = logdiff_2023_analysis(table, preds[(2023, "rf_logdiff", "frozen")])
    out_rows = [{**r, "train_window_frozen": f"2019-01..{TARGETS.get(r['year'], str(r['year'] - 1) + '-12')}"} for r in rows]
    payload = {
        "protocol": {
            "targets": {"2023": "frozen train 2019-01..2022-12", "2024": "frozen train 2019-01..2023-12",
                        "2025": "frozen train 2019-01..2024-12 (recomputed check)"},
            "monthly": "expanding refit from 2019-01 through t-1",
            "specs": {"rf_logdiff": LOGDIFF.name, "rf_level": LEVEL.name, "rf_logdiff_yoy3": LOGDIFF.name + " + yoy_3m_log"},
            "note_2024": "2024 was the selection year for rf_logdiff (Protocol B), so its 2024 score is optimistic.",
        },
        "known_2025": KNOWN_2025,
        "validation": {"n_warnings": validation.get("n_warnings"), "n_hard": validation.get("n_hard")},
        "dq_scores": quality["scores"],
        "results": out_rows,
        "monthly_wape": month_rows,
        "logdiff_2023_analysis": analysis,
        "rows_2023": int(len(part_2023)),
    }
    (HERE / "results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(out_rows).to_csv(HERE / "results.csv", index=False)
    pd.DataFrame(month_rows).to_csv(HERE / "monthly_wape.csv", index=False)


if __name__ == "__main__":
    main()
