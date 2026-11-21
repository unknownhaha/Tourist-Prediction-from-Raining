"""2025 replay with PSI, ADWIN, promotion presets D and E, and rollback.

The Champion is the log-diff forest fit through 2024-12. The 3-month YoY
model is one challenger. ``river`` is not installed; ADWIN is the local
implementation with delta 0.002.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.beat_seasonal_naive.run import (  # noqa: E402
    RF_CURRENT,
    Spec,
    _rain_history,
    build_feature_table,
    fit_spec,
    matrix_for,
)
from experiments.new_features.dq import assess_feature_inputs  # noqa: E402
from experiments.new_features.features import build_new_features  # noqa: E402
from experiments.new_features.validate_inputs import validate_feature_inputs  # noqa: E402
from experiments.psi_adwin.policy import replay  # noqa: E402
from src.common.paths import DATA  # noqa: E402
from src.stream.champion_policy import promotion_rule  # noqa: E402
from src.stream.metrics import wape  # noqa: E402
from src.stream.studio_models import _proportions, _reference_bins, psi_alarm, psi_score  # noqa: E402

HERE = Path(__file__).resolve().parent
MONTHS = [f"2025-{n:02d}" for n in range(1, 13)]
LOGDIFF = Spec("logdiff", "rf", "logdiff", "ext", tuple(sorted(RF_CURRENT.items())))


def _load():
    panel = pd.read_parquet(DATA / "model" / "province_month_unified.parquet")
    panel["province_id"] = panel["province_id"].astype(str).str.zfill(2)
    panel["year_month"] = panel["year_month"].astype(str)
    return panel.sort_values(["year_month", "province_id"]).reset_index(drop=True)


def _bins(frame: pd.DataFrame, end: str) -> dict:
    start = str(pd.Period(end, freq="M") - 23)
    window = frame.loc[frame["year_month"].between(start, end)].copy()
    return _reference_bins(window)


def _psi_call(store, month: str, reference_end: str):
    bins = store["bins"].get(reference_end)
    if bins is None:
        bins = _bins(store["psi_frame"], reference_end)
        store["bins"][reference_end] = bins
    features = store["by_month"][month]
    alarms = []
    rain = False
    bucket = bins.get(month[-2:])
    if bucket is None:
        return False, [], False
    for name, (edges, reference) in bucket.items():
        current = _proportions(features[name], edges)
        score = psi_score(reference, current)
        if psi_alarm(score):
            alarms.append(name)
            if name == "rain_lag_1":
                rain = True
    return bool(alarms), alarms, rain


class Models:
    def __init__(self, table, history):
        self.table = table
        self.history = history
        self.cache = {}

    def _rows(self, train_end: str, last_n: int | None = None, months: list[str] | None = None):
        rows = self.table.loc[self.table["year_month"].between("2019-01", train_end)]
        if months is not None:
            rows = rows.loc[rows["year_month"].isin(months)]
        elif last_n is not None:
            keep = list(dict.fromkeys(rows["year_month"]))[-last_n:]
            rows = rows.loc[rows["year_month"].isin(keep)]
        return rows

    def _fit(self, key: str):
        if key in self.cache:
            return self.cache[key]
        name, stamp = key.split("@", 1)
        extra = ("yoy_3m_log",) if name == "yoy3_expanding" else ()
        if name == "logdiff_seasonal_2y":
            year, month = map(int, stamp.split("-"))
            chosen = [f"{year - 1:04d}-{month:02d}", f"{year - 2:04d}-{month:02d}"]
            rows = self._rows("2025-12", months=chosen)
            train_end = max(chosen)
        elif name == "logdiff_12m":
            rows = self._rows(stamp, last_n=12)
            train_end = stamp
        else:
            rows = self._rows(stamp)
            train_end = stamp
        matrix = matrix_for(rows, self.history, "ext", train_end)
        if extra:
            matrix = pd.concat([matrix, rows.loc[matrix.index, list(extra)]], axis=1)
        model = fit_spec(LOGDIFF, rows, matrix, train_end)
        self.cache[key] = (model, extra, train_end)
        return self.cache[key]

    def predict(self, key: str, month: str) -> np.ndarray:
        model, extra, train_end = self._fit(key)
        month_table = self.table.loc[self.table["year_month"] == month].sort_values("province_id")
        matrix = matrix_for(month_table, self.history, "ext", train_end)
        if extra:
            matrix = pd.concat([matrix, month_table.loc[matrix.index, list(extra)]], axis=1)
        return model.predict(matrix, month_table["guest_lag_12"].to_numpy(dtype=float))


def main() -> None:
    panel = _load()
    print("FEATURES", flush=True)
    table = build_feature_table(panel)
    new = build_new_features(panel)
    table["province_id"] = table["province_id"].astype(str).str.zfill(2)
    table = table.merge(new.drop(columns=["region"]), on=["province_id", "year_month"], validate="one_to_one")
    validation = validate_feature_inputs(panel, new)
    quality = assess_feature_inputs(panel, new)
    (HERE / "validation_report.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    (HERE / "dq_report.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    print("DQ", quality["scores"]["overall"], flush=True)
    history = _rain_history(panel)
    models = Models(table, history)
    by_month = {}
    y, naive = {}, {}
    psi_rows = []
    for month in pd.period_range("2023-01", "2025-12", freq="M").strftime("%Y-%m"):
        part = table.loc[table["year_month"] == month].sort_values("province_id")
        if month.startswith("2025"):
            by_month[month] = part
            y[month] = part["guest_arrivals_total"].to_numpy(dtype=float)
            naive[month] = part["guest_lag_12"].to_numpy(dtype=float)
        psi_rows.append(part[["province_id", "year_month", "guest_lag_1", "guest_lag_12", "rain_lag_1", "rain_roll3"]])
    psi_frame = pd.concat(psi_rows, ignore_index=True)
    store = {"psi_frame": psi_frame, "bins": {}, "by_month": by_month}
    fixed_alarms = []
    seasonal_alarms = []
    for month in MONTHS:
        fixed_hit, fixed_features, _ = _psi_call(store, month, "2024-12")
        seasonal_bins = _bins(psi_frame.loc[psi_frame["year_month"].str[-2:] == month[-2:]], "2024-12")
        # The two-year slice above still includes every year of that month up to 2024-12.
        seasonal_hit, seasonal_features, _ = _psi_call({"psi_frame": psi_frame.loc[psi_frame["year_month"].str.endswith(month[-2:]) & (psi_frame["year_month"] <= "2024-12")], "bins": {}, "by_month": by_month}, month, "2024-12")
        fixed_alarms.append((fixed_hit, tuple(fixed_features)))
        seasonal_alarms.append((seasonal_hit, tuple(seasonal_features)))
    reference_match = fixed_alarms == seasonal_alarms
    print("REFERENCE_MATCH", reference_match, flush=True)

    config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    candidates = tuple(config["challengers"])
    delta = float(config["adwin_delta"])
    gate_months = int(config["psi_gate_months"])
    rollback_months = int(config["rollback_months"])

    def predict(key, month):
        return models.predict(key, month)

    def one(mode, reference, refresh, preset, role):
        label = f"{role}|{mode}|{reference}|refresh={int(refresh)}|{preset}"
        print(label, flush=True)

        def psi(month, reference_end, _reference=reference, _refresh=refresh):
            if _reference == "seasonal_2y" and not _refresh:
                end = "2024-12"
            elif _refresh:
                end = reference_end
            else:
                end = "2024-12"
            return _psi_call(store, month, end)

        result = replay(
            MONTHS, y, naive, predict, psi, candidates, promotion_rule(preset),
            trigger_mode=mode, delta=delta, gate_months=gate_months,
            rollback_months=rollback_months, refresh=refresh,
        )
        served = result["served"]
        naive_wape = wape(
            np.array([value for month in MONTHS for value in y[month]]),
            np.array([value for month in MONTHS for value in naive[month]]),
        )
        alerts = [event for event in result["events"] if event["event"] == "alert" and event["trigger"]]
        promotions = [event for event in result["events"] if event["event"] == "promote"]
        rollbacks = [event for event in result["events"] if event["event"] == "rollback"]
        summary = {
            "role": role,
            "config": label,
            "trigger_mode": mode,
            "reference": reference,
            "refresh": refresh,
            "preset": preset,
            "wape": served["wape"],
            "mae": served["mae"],
            "rmse": served["rmse"],
            "mase": served["mase"],
            "n": served["n"],
            "naive_wape": naive_wape,
            "below_naive": bool(served["wape"] < naive_wape),
            "n_alerts": len(alerts),
            "alerts": [{"month": event["month"], "psi": event["psi"], "adwin": event["adwin"], "features": event["features"]} for event in alerts],
            "n_promotions": len(promotions),
            "promotions": [{"month": event["month"], "winner": event["winner"], "effective_from": event["effective_from"]} for event in promotions],
            "n_rollbacks": len(rollbacks),
            "rollbacks": rollbacks,
            "registry": result["registry"],
        }
        for event in result["events"]:
            events.append({"config": label, "role": role, **event})
        return summary

    feature_names = set(table.columns)
    if "rain_avg_mm" in feature_names or "is_covid" not in feature_names or "rain_lag_1" not in feature_names:
        raise RuntimeError("Champion features must keep lagged rain and the COVID flags, with no same-month rain")
    summaries = []
    events = []
    primary_key = (
        config["psi_reference"],
        bool(config["refresh_reference_after_promotion"]),
        config["promotion_preset"],
    )
    for mode in config["trigger_modes_under_primary"]:
        summaries.append(one(mode, *primary_key, "primary"))
    for mode in config["trigger_modes_under_primary"]:
        for reference in ("seasonal_2y", "fixed_2023_2024"):
            for refresh in (False, True):
                for preset in (config["promotion_preset"], *config["comparison_presets"]):
                    if (reference, refresh, preset) == primary_key:
                        continue
                    summaries.append(one(mode, reference, refresh, preset, "comparison"))
    payload = {
        "primary": config,
        "delta": delta,
        "gate_months": gate_months,
        "rollback_months": rollback_months,
        "adwin": "local Bifet-Gavalda cut; river is not installed",
        "adwin_input": "per-province absolute error of the served forecast, in province_id order",
        "seasonal_2y_matches_fixed_2023_2024_alerts": reference_match,
        "validation_warnings": validation["n_warnings"],
        "dq_overall": quality["scores"]["overall"],
        "runs": summaries,
    }
    (HERE / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    pd.DataFrame([{key: value for key, value in row.items() if key not in {"alerts", "promotions", "rollbacks", "registry"}} for row in summaries]).to_csv(HERE / "results.csv", index=False)
    (HERE / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    print("DONE", reference_match, "runs", len(summaries), flush=True)


if __name__ == "__main__":
    main()
