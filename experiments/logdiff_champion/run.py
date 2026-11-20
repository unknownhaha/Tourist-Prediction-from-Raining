"""Frozen Champion/Challenger on the raw 2025 ingest, starting from the log-diff forest.

The initial Champion is Protocol B's selected model: ``rf|logdiff|ext|d10_leaf3``,
alpha 1, fit on 2019-01..2024-12. Promotion uses the same presets as
``ChampionPolicy``. PSI stays on the four base features against the 2023-2024
reference. Extended-feature columns are not given their own PSI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler

from experiments.beat_seasonal_naive.run import (
    RF_CURRENT,
    Spec,
    _rain_history,
    build_feature_table,
    fit_spec,
    matrix_for,
)
from src.common.calendar import month_range
from src.common.paths import DATA, ROOT
from src.model.leakage import TIME_SPLIT, build_forecast_features, shift_year_month
from src.stream.champion_policy import promotion_rule
from src.stream.clean import clean_panel
from src.stream.drift import PageHinkley
from src.stream.metrics import _block, wape
from src.stream.raw_2025 import read_month
from src.stream.replay import prepare_month
from src.stream.studio_models import (
    PH_DELTA,
    PH_THRESHOLD,
    _proportions,
    _reference_bins,
    psi_alarm,
    psi_score,
)
from src.stream.validate import FEATURE_COLUMNS, validate_history

HERE = Path(__file__).resolve().parent
STREAM = [f"2025-{month:02d}" for month in range(1, 13)]
CANDIDATES = ("logdiff_expanding", "logdiff_12m", "rf_expanding", "rf_12m", "linear_expanding")
FROZEN_WAPE = 0.04788460348726727
LEVEL_SPEC = Spec("rf_level_base", "rf", "level", "base", tuple(sorted(RF_CURRENT.items())))
LOGDIFF_SPEC = Spec("rf_logdiff_ext", "rf", "logdiff", "ext", tuple(sorted(RF_CURRENT.items())))


def _panel() -> pd.DataFrame:
    frame = pd.read_parquet(DATA / "model" / "province_month_unified.parquet")
    frame["province_id"] = frame["province_id"].astype(str).str.zfill(2)
    frame["year_month"] = frame["year_month"].astype(str)
    return frame.sort_values(["year_month", "province_id"]).reset_index(drop=True)


def _psi(features: pd.DataFrame, month: str, bins: dict) -> dict:
    results = {}
    for name, (edges, reference) in bins[month[-2:]].items():
        current = _proportions(features[name], edges)
        score = psi_score(reference, current)
        results[name] = {"value": score, "alarm": bool(psi_alarm(score))}
    return results


def _reference(history: pd.DataFrame) -> dict:
    frames = []
    for month in month_range("2023-01", "2024-12"):
        features = build_forecast_features(history, month)
        features["province_id"] = features["province_id"].astype(str).str.zfill(2)
        features["year_month"] = month
        frames.append(features.sort_values("province_id"))
    return _reference_bins(pd.concat(frames, ignore_index=True))


def _ingest(history: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Raw 2025 months through validate, clean, DQ, and base features."""
    validate_history(history, STREAM)
    revealed = []
    reports = []
    output = Path("/tmp/logdiff-champion-dq")
    for month in STREAM:
        raw = read_month(month)
        raw["province_id"] = raw["province_id"].astype(str).str.zfill(2)
        panel = pd.concat([history, *revealed, raw], ignore_index=True)
        features, labels, _audit, report = prepare_month(panel, month, output, "logdiff-champion", None)
        cleaned, actions = clean_panel(panel)
        cleaned["province_id"] = cleaned["province_id"].astype(str).str.zfill(2)
        cleaned["year_month"] = cleaned["year_month"].astype(str)
        revealed.append(cleaned.loc[cleaned["year_month"] == month].copy())
        reports.append({
            "month": month,
            "hard_fail": bool(report["hard_fail"]),
            "n_warnings": int(report["n_warnings"]),
            "n_cleaning_actions": len(actions),
            "base_rows": int(len(features)),
            "label_rows": int(len(labels)),
        })
    combined = pd.concat([history, *revealed], ignore_index=True)
    combined["province_id"] = combined["province_id"].astype(str).str.zfill(2)
    combined["year_month"] = combined["year_month"].astype(str)
    return combined.sort_values(["year_month", "province_id"]).reset_index(drop=True), reports


def _fit_linear(table: pd.DataFrame, train_end: str):
    train = table["year_month"].between(TIME_SPLIT["train"][0], train_end)
    rows = table.loc[train]
    values = rows[["guest_lag_1", "guest_lag_12", "rain_lag_1", "rain_roll3", "is_covid", "is_covid_lag12"]]
    ok = np.isfinite(values.to_numpy(dtype=float)).all(axis=1)
    y = rows["guest_arrivals_total"].to_numpy(dtype=float)[ok]
    scaler = StandardScaler().fit(values.loc[ok])
    model = LinearRegression().fit(scaler.transform(values.loc[ok]), y)
    medians = values.loc[ok].median(numeric_only=True)

    def predict(month_table: pd.DataFrame) -> np.ndarray:
        frame = month_table[list(values.columns)].apply(pd.to_numeric, errors="coerce")
        for column in frame.columns:
            frame[column] = frame[column].fillna(medians[column])
        raw = model.predict(scaler.transform(frame.to_numpy(dtype=float)))
        return np.maximum(np.where(np.isfinite(raw), raw, 0.0), 0.0)

    return predict


def _window(table: pd.DataFrame, train_end: str, last_n: int | None) -> pd.DataFrame:
    rows = table.loc[table["year_month"].between(TIME_SPLIT["train"][0], train_end)]
    if last_n is None:
        return rows
    keep = list(dict.fromkeys(rows["year_month"]))[-last_n:]
    return rows.loc[rows["year_month"].isin(keep)]


def _fit_named(name: str, table: pd.DataFrame, history: dict, train_end: str):
    last_n = 12 if name.endswith("_12m") else None
    rows = _window(table, train_end, last_n)
    if name.startswith("logdiff"):
        matrix = matrix_for(rows, history, "ext", train_end)
        return fit_spec(LOGDIFF_SPEC, rows, matrix, train_end)
    if name.startswith("rf_"):
        matrix = matrix_for(rows, history, "base", train_end)
        return fit_spec(LEVEL_SPEC, rows, matrix, train_end)
    if name == "linear_expanding":
        return _fit_linear(rows, train_end)
    raise KeyError(name)


def _predict_named(name: str, fitted, month_table: pd.DataFrame, history: dict, train_end: str) -> np.ndarray:
    if name == "linear_expanding":
        return fitted(month_table)
    feature_set = "ext" if name.startswith("logdiff") else "base"
    matrix = matrix_for(month_table, history, feature_set, train_end)
    return fitted.predict(matrix, month_table["guest_lag_12"].to_numpy(dtype=float))


def _month_wape(name: str, table: pd.DataFrame, history: dict, train_end: str, month: str) -> float:
    fitted = _fit_named(name, table, history, train_end)
    month_table = table.loc[table["year_month"] == month].sort_values("province_id")
    pred = _predict_named(name, fitted, month_table, history, train_end)
    y = month_table["guest_arrivals_total"].to_numpy(dtype=float)
    return wape(y, pred)


def _calibration(name: str, table: pd.DataFrame, history: dict) -> tuple[float, float]:
    scores = []
    if name.endswith("_12m"):
        for month in month_range("2023-01", "2023-12"):
            scores.append(_month_wape(name, table, history, shift_year_month(month, 1), month))
    else:
        fitted = _fit_named(name, table, history, "2022-12")
        for month in month_range("2023-01", "2023-12"):
            month_table = table.loc[table["year_month"] == month].sort_values("province_id")
            pred = _predict_named(name, fitted, month_table, history, "2022-12")
            scores.append(wape(month_table["guest_arrivals_total"].to_numpy(dtype=float), pred))
    std = float(np.std(scores, ddof=1))
    if not np.isfinite(std) or std == 0:
        raise ValueError(f"No usable 2023 WAPE variation for {name}")
    return float(np.mean(scores)), std


def _trailing(name: str, table: pd.DataFrame, history: dict, revealed: list[str], current: float) -> list[float]:
    scores = []
    for month in revealed[-12:-1]:
        scores.append(_month_wape(name, table, history, shift_year_month(month, 1), month))
    scores.append(float(current))
    return scores[-12:]


def _serve(name: str, fitted, month_table: pd.DataFrame, history: dict, train_end: str) -> np.ndarray:
    return _predict_named(name, fitted, month_table, history, train_end)


def _run_month(preset, rule, table, history, psi_by_month, calibration):
    active_name = "logdiff_expanding"
    active_end = "2024-12"
    active = _fit_named(active_name, table, history, active_end)
    detectors = {name: PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=12) for name in CANDIDATES}
    streak = {name: 0 for name in CANDIDATES}
    last_rain = None
    rows = []
    triggers = []
    promotions = []
    for month in STREAM:
        month_table = table.loc[table["year_month"] == month].sort_values("province_id")
        serving_name = active_name
        pred = _serve(serving_name, active, month_table, history, active_end)
        y = month_table["guest_arrivals_total"].to_numpy(dtype=float)
        lag = month_table["guest_lag_12"].to_numpy(dtype=float)
        score = _block(y, pred, lag)
        psi = {name: dict(event) for name, event in psi_by_month[month].items()}
        cutoff = shift_year_month(month, 1)
        rain = psi["rain_lag_1"]
        exceeded = rain["alarm"]
        rain["alarm"] = bool(exceeded and last_rain == cutoff)
        rain["pending_confirmation"] = bool(exceeded and not rain["alarm"])
        last_rain = month if exceeded else None
        mean, std = calibration[serving_name]
        ph_alarm = bool(detectors[serving_name].update(float((score["wape"] - mean) / std)))
        reasons = [name for name, event in psi.items() if event["alarm"]]
        if ph_alarm:
            reasons.append("Page-Hinkley")
        decision = "KEEP"
        if rule is not None and reasons:
            fitted = {}
            scores = {}
            for name in CANDIDATES:
                fitted[name] = _fit_named(name, table, history, cutoff)
                scores[name] = _block(y, _serve(name, fitted[name], month_table, history, cutoff), lag)
            for name in CANDIDATES:
                streak[name] = streak.get(name, 0) + 1 if rule.beats(scores[name], score) else 0
            winners = [name for name in CANDIDATES if streak[name] > 0]
            if winners:
                best = min(winners, key=lambda name: scores[name]["wape"])
                if streak[best] >= rule.confirm_months:
                    active, active_name, active_end = fitted[best], best, cutoff
                    if rule.seed_detector:
                        seeded = PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=0)
                        center, scale = calibration[best]
                        for value in _trailing(best, table, history, [item for item in STREAM if item <= month], scores[best]["wape"]):
                            seeded.update(float((value - center) / scale))
                        detectors[best] = seeded
                    else:
                        detectors[best] = PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=12)
                    decision = "PROMOTE"
                    promotions.append({
                        "month": month,
                        "winner": best,
                        "effective_from": str(pd.Period(month, freq="M") + 1),
                        "champion_wape": score["wape"],
                        "winner_wape": scores[best]["wape"],
                    })
        elif rule is not None:
            streak = {name: 0 for name in CANDIDATES}
        if reasons:
            triggers.append({"month": month, "trigger": reasons})
        for province_id, y_i, pred_i, lag_i in zip(month_table["province_id"], y, pred, lag):
            rows.append({"year_month": month, "province_id": province_id, "y_true": float(y_i),
                         "y_pred": float(pred_i), "y_naive": float(lag_i), "decision": decision,
                         "served_strategy": serving_name})
    return _summarize(preset, triggers, promotions, rows)


def _summarize(preset: str, triggers: list, promotions: list, rows: list) -> dict:
    frame = pd.DataFrame(rows)
    y = frame["y_true"].to_numpy(dtype=float)
    pred = frame["y_pred"].to_numpy(dtype=float)
    naive = frame["y_naive"].to_numpy(dtype=float)
    overall = _block(y, pred, naive)
    overall["bias"] = float((pred.sum() - y.sum()) / y.sum())
    by_month = []
    for month, part in frame.groupby("year_month", sort=True):
        by_month.append({
            "year_month": month,
            "wape_served": wape(part["y_true"].to_numpy(dtype=float), part["y_pred"].to_numpy(dtype=float)),
            "wape_naive": wape(part["y_true"].to_numpy(dtype=float), part["y_naive"].to_numpy(dtype=float)),
            "n": int(len(part)),
        })
    improved = 0
    for _, part in frame.groupby("province_id"):
        if part["y_true"].sub(part["y_pred"]).abs().sum() < part["y_true"].sub(part["y_naive"]).abs().sum():
            improved += 1
    return {
        "preset": preset,
        "triggers": triggers,
        "n_triggers": len(triggers),
        "promotions": promotions,
        "n_promotions": len(promotions),
        "served": overall,
        "by_month": by_month,
        "provinces_improved": improved,
        "provinces": int(frame["province_id"].nunique()),
        "rows": rows,
    }


def main() -> None:
    unified = _panel()
    history = unified.loc[unified["year_month"] < "2025-01"].copy()
    print("INGEST", flush=True)
    combined, dq = _ingest(history)
    table = build_feature_table(combined)
    rain = _rain_history(combined)
    bins = _reference(history)
    psi_by_month = {}
    for month in STREAM:
        base = table.loc[table["year_month"] == month].sort_values("province_id")
        psi_by_month[month] = _psi(base, month, bins)
    print("CALIBRATE", flush=True)
    calibration = {name: _calibration(name, table, rain) for name in CANDIDATES}
    print({name: calibration[name] for name in CANDIDATES}, flush=True)
    runs = [
        ("no_promotion", None),
        ("legacy", promotion_rule("legacy")),
        ("margin_0005_confirm_1", promotion_rule("margin_0005_confirm_1")),
        ("margin_0005_confirm_2", promotion_rule("margin_0005_confirm_2")),
        ("margin_001_confirm_2", promotion_rule("margin_001_confirm_2")),
    ]
    results = []
    for name, rule in runs:
        print("RUN", name, flush=True)
        results.append(_run_month(name, rule, table, rain, psi_by_month, calibration))
    frozen = results[0]["served"]["wape"]
    payload = {
        "psi_features": list(FEATURE_COLUMNS),
        "psi_reference": "2023-01..2024-12 base features only; extended columns are not scored",
        "candidates": list(CANDIDATES),
        "champion": "logdiff_expanding rf|logdiff|ext|d10_leaf3 alpha 1 fit 2019-01..2024-12",
        "dq": dq,
        "frozen_wape": frozen,
        "frozen_wape_matches_protocol_b": bool(np.isclose(frozen, FROZEN_WAPE, rtol=0, atol=0)),
        "frozen_wape_abs_diff": abs(frozen - FROZEN_WAPE),
        "protocol_b_frozen_wape": FROZEN_WAPE,
        "previous_rf_champion_wape": {
            "legacy": 0.0601809721016076,
            "margin_0005_confirm_1": 0.06028049862702164,
            "margin_0005_confirm_2": 0.06254650120481828,
            "margin_001_confirm_2": 0.06390442846674334,
        },
        "runs": [{key: value for key, value in result.items() if key != "rows"} for result in results],
    }
    (HERE / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    flat = []
    for result in results:
        for row in result["rows"]:
            flat.append({"preset": result["preset"], **row})
    pd.DataFrame(flat).to_csv(HERE / "served_rows.csv", index=False)
    print(json.dumps({
        "frozen_wape": frozen,
        "abs_diff": payload["frozen_wape_abs_diff"],
        "match": payload["frozen_wape_matches_protocol_b"],
        "runs": [{k: result[k] for k in ("preset", "n_triggers", "n_promotions")} | {"wape": result["served"]["wape"]}
                 for result in results],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
