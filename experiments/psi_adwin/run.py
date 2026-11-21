"""2025 replay under the primary config, varying only the ADWIN input.

Province absolute errors are compared at deltas 0.002, 1e-3, 1e-4, 1e-5,
and 1e-6. Monthly WAPE is compared at deltas 0.002, 0.01, 0.05, 0.1, 0.2,
and 0.5, with no seed and with two out-of-sample Champion histories.
``river`` is not installed. ADWIN is the local implementation.
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
from experiments.psi_adwin.policy import _previous, replay  # noqa: E402
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


def seed_cut_log(series: list[dict], deltas: list[float]) -> list[dict]:
    """Where the historical WAPE stream cuts, before any 2025 month is added."""
    from experiments.psi_adwin.adwin import ADWIN

    scored = [row for row in series if row["wape"] is not None]
    found = []
    for delta in deltas:
        detector = ADWIN(float(delta))
        for index, row in enumerate(scored):
            if not detector.update(float(row["wape"])):
                continue
            start = scored[index + 1 - len(detector.window)]
            found.append({
                "delta": float(delta),
                "cut_on": row["month"],
                "window_after_cut": len(detector.window),
                "window_starts": start["month"],
                "dropped_through": scored[index - len(detector.window)]["month"],
            })
    return found


def champion_oos_wape(models: Models, table: pd.DataFrame, start: str, end: str) -> list[dict]:
    """One-step WAPE of the warm-start Champion recipe.

    Month t is predicted by ``logdiff_expanding`` fit on 2019-01 through t-1.
    That is the same spec as the model frozen at 2024-12 for the 2025 replay.
    2019-01 has no prior modeling month, so it is recorded without a WAPE.
    """
    series = []
    for month in pd.period_range(start, end, freq="M").strftime("%Y-%m"):
        train_end = _previous(month)
        if train_end < "2019-01":
            series.append({
                "month": month,
                "wape": None,
                "trained_through": None,
                "n_train_months": 0,
                "reason": "no modeling rows exist before 2019-01",
            })
            continue
        part = table.loc[table["year_month"] == month].sort_values("province_id")
        pred = models.predict(f"logdiff_expanding@{train_end}", month)
        actual = part["guest_arrivals_total"].to_numpy(dtype=float)
        n_train = int(table.loc[table["year_month"].between("2019-01", train_end), "year_month"].nunique())
        value = wape(actual, pred)
        series.append({
            "month": month,
            "wape": value,
            "trained_through": train_end,
            "n_train_months": n_train,
            "reason": "expanding one-step out of sample",
        })
        print(f"OOS {month} train_through={train_end} n_months={n_train} wape={value:.6f}", flush=True)
    return series


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
    gate_months = int(config["psi_gate_months"])
    rollback_months = int(config["rollback_months"])
    preset = config["promotion_preset"]
    feature_names = set(table.columns)
    if "rain_avg_mm" in feature_names or "is_covid" not in feature_names or "rain_lag_1" not in feature_names:
        raise RuntimeError("Champion features must keep lagged rain and the COVID flags, with no same-month rain")

    def predict(key, month):
        return models.predict(key, month)

    print("OOS_SEED", flush=True)
    history_wape = champion_oos_wape(models, table, "2019-01", "2024-12")
    usable = [row for row in history_wape if row["wape"] is not None]

    def seed_values(name: str) -> list[float] | None:
        if name == "none":
            return None
        if name == "oos_expanding_2019_2024":
            return [float(row["wape"]) for row in usable]
        if name == "oos_expanding_2023_2024":
            return [float(row["wape"]) for row in usable if row["month"] >= "2023-01"]
        raise KeyError(name)

    def psi(month, reference_end):
        end = "2024-12" if not config["refresh_reference_after_promotion"] else reference_end
        return _psi_call(store, month, end)

    summaries = []
    events = []

    def one(error_mode: str, delta: float, seed_name: str):
        seed = seed_values(seed_name)
        for mode in config["trigger_modes_under_primary"]:
            label = f"primary|{error_mode}|delta={delta}|seed={seed_name}|{mode}"
            print(label, flush=True)
            result = replay(
                MONTHS, y, naive, predict, psi, candidates, promotion_rule(preset),
                trigger_mode=mode, delta=delta, gate_months=gate_months,
                rollback_months=rollback_months,
                refresh=bool(config["refresh_reference_after_promotion"]),
                error_mode=error_mode, seed=seed,
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
                "role": "primary",
                "config": label,
                "error_mode": error_mode,
                "delta": delta,
                "seed": seed_name,
                "seed_length": result["seed_length"],
                "seed_cuts": result["seed_cuts"],
                "window_after_seed": result["window_after_seed"],
                "trigger_mode": mode,
                "reference": config["psi_reference"],
                "refresh": bool(config["refresh_reference_after_promotion"]),
                "preset": preset,
                "wape": served["wape"],
                "mae": served["mae"],
                "rmse": served["rmse"],
                "mase": served["mase"],
                "n": served["n"],
                "naive_wape": naive_wape,
                "below_naive": bool(served["wape"] < naive_wape),
                "adwin_months": result["adwin_alarm_months"],
                "n_adwin": len(result["adwin_alarm_months"]),
                "n_alerts": len(alerts),
                "alert_months": [event["month"] for event in alerts],
                "alerts": [{"month": event["month"], "psi": event["psi"], "adwin": event["adwin"], "features": event["features"]} for event in alerts],
                "n_promotions": len(promotions),
                "promotions": [{"month": event["month"], "winner": event["winner"], "effective_from": event["effective_from"]} for event in promotions],
                "n_rollbacks": len(rollbacks),
                "rollbacks": rollbacks,
                "registry": result["registry"],
            }
            summaries.append(summary)
            for event in result["events"]:
                events.append({"config": label, **event})

    comparison = config["adwin_comparison"]
    for delta in comparison["province_absolute_error"]["deltas"]:
        one("province_abs", float(delta), comparison["province_absolute_error"]["seed"])
    for seed_name in comparison["monthly_wape"]["seeds"]:
        for delta in comparison["monthly_wape"]["deltas"]:
            one("monthly_wape", float(delta), seed_name)
    payload = {
        "primary": config,
        "gate_months": gate_months,
        "rollback_months": rollback_months,
        "adwin": "local Bifet-Gavalda cut; river is not installed; a window shorter than 32 points is not cut",
        "adwin_bound": "The cut assumes values on [0, 1]. Province absolute errors are not on that scale.",
        "seed_method": {
            "recipe": "rf|logdiff|ext|d10_leaf3, the warm-start Champion spec",
            "protocol": "For month t, fit on 2019-01 through t-1 and score t. The fit does not see t.",
            "omitted": "2019-01 has no prior modeling month.",
            "oos_expanding_2019_2024": "Every scored month from 2019-02 through 2024-12. Early 2019 fits use fewer than 12 training months.",
            "oos_expanding_2023_2024": "The same series restricted to 2023-01..2024-12. Each of those months has at least 48 training months.",
            "unseeded": "The 12 monthly WAPE values of 2025 are shorter than the 32-point cut, so that seed cannot alarm.",
        },
        "oos_wape": history_wape,
        "seed_cuts_before_2025": seed_cut_log(history_wape, comparison["monthly_wape"]["deltas"]),
        "seasonal_2y_matches_fixed_2023_2024_alerts": reference_match,
        "validation_warnings": validation["n_warnings"],
        "dq_overall": quality["scores"]["overall"],
        "runs": summaries,
    }
    (HERE / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    flat = []
    for row in summaries:
        item = {key: value for key, value in row.items() if key not in {"alerts", "promotions", "rollbacks", "registry"}}
        item["adwin_months"] = " ".join(row["adwin_months"])
        item["alert_months"] = " ".join(row["alert_months"])
        flat.append(item)
    pd.DataFrame(flat).to_csv(HERE / "results.csv", index=False)
    (HERE / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    print("DONE", reference_match, "runs", len(summaries), flush=True)


if __name__ == "__main__":
    main()
