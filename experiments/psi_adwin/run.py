"""Primary 2025 replay approved on 2026-11-21.

ADWIN reads monthly WAPE at delta 0.002, seeded with the Champion's
2023-2024 expanding out-of-sample WAPE. Challengers train only when PSI and
ADWIN both fire. PSI alone is logged as a watch. DS/ML still own the final
values. ``river`` is not installed.
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


def champion_oos_wape(models: Models, table: pd.DataFrame, start: str, end: str,
                      model_name: str = "logdiff_expanding") -> list[dict]:
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
        pred = models.predict(f"{model_name}@{train_end}", month)
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
    if config["challenger_trigger"] != "psi_and_adwin" or config["psi_only"] != "watch":
        raise RuntimeError("The primary trigger trains Challengers only when PSI and ADWIN both fire")
    if config["adwin_input"] != "monthly_wape" or config["adwin_seed"] != "oos_expanding_2023_2024":
        raise RuntimeError("The primary ADWIN input is monthly WAPE seeded with 2023-2024")
    candidates = tuple(config["challengers"])
    gate_months = int(config["psi_gate_months"])
    rollback_months = int(config["rollback_months"])
    preset = config["promotion_preset"]
    delta = float(config["adwin_delta"])
    feature_names = set(table.columns)
    if "rain_avg_mm" in feature_names or "is_covid" not in feature_names or "rain_lag_1" not in feature_names:
        raise RuntimeError("Champion features must keep lagged rain and the COVID flags, with no same-month rain")

    def predict(key, month):
        return models.predict(key, month)

    print("OOS_SEED", flush=True)
    history_wape = champion_oos_wape(models, table, "2023-01", "2024-12")
    seed = [float(row["wape"]) for row in history_wape]

    def psi(month, reference_end):
        end = "2024-12" if not config["refresh_reference_after_promotion"] else reference_end
        return _psi_call(store, month, end)

    label = "primary|monthly_wape|delta=0.002|seed=oos_expanding_2023_2024|psi_and_adwin"
    print(label, flush=True)
    result = replay(
        MONTHS, y, naive, predict, psi, candidates, promotion_rule(preset),
        trigger_mode=config["challenger_trigger"], delta=delta, gate_months=gate_months,
        rollback_months=rollback_months,
        refresh=bool(config["refresh_reference_after_promotion"]),
        error_mode=config["adwin_input"], seed=seed,
    )
    served = result["served"]
    naive_wape = wape(
        np.array([value for month in MONTHS for value in y[month]]),
        np.array([value for month in MONTHS for value in naive[month]]),
    )
    watches = [event for event in result["events"] if event["event"] == "watch"]
    retrains = [event for event in result["events"] if event["event"] == "alert" and event["retrain"]]
    promotions = [event for event in result["events"] if event["event"] == "promote"]
    rollbacks = [event for event in result["events"] if event["event"] == "rollback"]
    summary = {
        "role": "primary",
        "config": label,
        "decision_date": config["decision_date"],
        "error_mode": config["adwin_input"],
        "delta": delta,
        "seed": config["adwin_seed"],
        "seed_length": result["seed_length"],
        "seed_cuts": result["seed_cuts"],
        "window_after_seed": result["window_after_seed"],
        "trigger_mode": config["challenger_trigger"],
        "psi_only": config["psi_only"],
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
        "watch_months": [event["month"] for event in watches],
        "watches": [{"month": event["month"], "features": event["features"]} for event in watches],
        "n_retrains": len(retrains),
        "retrain_months": [event["month"] for event in retrains],
        "n_promotions": len(promotions),
        "promotions": promotions,
        "n_rollbacks": len(rollbacks),
        "rollbacks": rollbacks,
        "registry": result["registry"],
    }
    payload = {
        "primary": config,
        "decision_date": config["decision_date"],
        "owners": config["owners"],
        "gate_months": gate_months,
        "rollback_months": rollback_months,
        "adwin": "local Bifet-Gavalda cut; river is not installed; a window shorter than 32 points is not cut",
        "seed_method": {
            "recipe": "rf|logdiff|ext|d10_leaf3, the warm-start Champion spec",
            "protocol": "For each month t in 2023-01..2024-12, fit on 2019-01 through t-1 and score t. The fit does not see t.",
            "n_train_months": "Each scored month has at least 48 training months.",
        },
        "oos_wape": history_wape,
        "seed_cuts_before_2025": seed_cut_log(history_wape, [delta]),
        "seasonal_2y_matches_fixed_2023_2024_alerts": reference_match,
        "validation_warnings": validation["n_warnings"],
        "dq_overall": quality["scores"]["overall"],
        "primary_result": summary,
    }
    (HERE / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    flat = {key: value for key, value in summary.items() if key not in {"watches", "promotions", "rollbacks", "registry"}}
    flat["adwin_months"] = " ".join(summary["adwin_months"])
    flat["watch_months"] = " ".join(summary["watch_months"])
    flat["retrain_months"] = " ".join(summary["retrain_months"])
    pd.DataFrame([flat]).to_csv(HERE / "results.csv", index=False)
    (HERE / "events.jsonl").write_text(
        "".join(json.dumps({"config": label, **event}) + "\n" for event in result["events"]),
        encoding="utf-8",
    )
    print(
        "DONE", "wape", served["wape"], "watches", len(watches),
        "retrains", len(retrains), "promotions", len(promotions), flush=True,
    )


if __name__ == "__main__":
    main()
