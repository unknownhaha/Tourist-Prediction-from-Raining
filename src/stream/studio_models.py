"""Frozen 2024 evaluation and monthly expanding-window forecasts for the studio."""
from __future__ import annotations

import json
import platform
import warnings
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler

from src.common.calendar import month_range
from src.model.leakage import TIME_SPLIT, build_forecast_features
from src.stream.drift import PageHinkley
from src.stream.metrics import summarize, wape
from src.stream.validate import FEATURE_COLUMNS, N_PROVINCES


FEATURES = [*FEATURE_COLUMNS, "is_covid", "is_covid_lag12"]
TARGET = "guest_arrivals_total"
MODEL_KEYS = ("rf_fixed", "rf_monthly", "linear", "seasonal")
RF_PARAMETERS = dict(n_estimators=300, max_depth=10, min_samples_leaf=3, random_state=42)
PSI_THRESHOLD = 0.15
PH_DELTA = 0.5
PH_THRESHOLD = 3.0


def _fit_forest(x, y):
    # Parallel training is seeded per tree. Sum predictions in a stable order.
    forest = RandomForestRegressor(**RF_PARAMETERS, n_jobs=4).fit(x, y)
    forest.n_jobs = 1
    return forest


def _predict(model, x):
    return np.maximum(0.0, model.predict(x))


def _forecast_history(panel):
    history = panel.loc[panel.year_month.between(TIME_SPLIT["lag_history"][0], TIME_SPLIT["test"][1])]
    expected = month_range(TIME_SPLIT["lag_history"][0], TIME_SPLIT["test"][1])
    counts = history.groupby("year_month").province_id.nunique()
    if (list(counts.index) != expected or not counts.eq(N_PROVINCES).all()
            or history.province_id.nunique() != N_PROVINCES
            or history.duplicated(["year_month", "province_id"]).any()):
        raise ValueError("2018–2024 history requires exactly one row per province and month")
    if not np.isfinite(history[[TARGET, "rain_avg_mm"]].to_numpy(dtype=float)).all():
        raise ValueError("Warm-start target or rainfall is missing")
    if (history[[TARGET, "rain_avg_mm"]] < 0).any().any():
        raise ValueError("Warm-start target or rainfall is negative")
    frames = []
    for month in month_range(TIME_SPLIT["train"][0], TIME_SPLIT["test"][1]):
        feats = build_forecast_features(history, month)
        actual = history.loc[history.year_month == month, ["province_id", TARGET]]
        frame = feats.merge(actual, on="province_id", validate="one_to_one")
        frames.append(frame.sort_values("province_id").assign(year_month=month))
    return pd.concat(frames, ignore_index=True)


def _proportions(values, edges):
    # Upper-inclusive bins match the calibration simulation. Half-count
    # smoothing keeps all four probabilities finite, including empty bins.
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("PSI input contains non-finite values")
    counts = np.bincount(np.searchsorted(edges, values, side="left"), minlength=4)
    return (counts + 0.5) / (len(values) + 2)


def _reference_bins(rows):
    bins = {}
    for month, group in rows.groupby(rows.year_month.str[-2:]):
        if len(group) != 2 * N_PROVINCES:
            raise ValueError(f"Incomplete seasonal reference for calendar month {month}")
        bins[month] = {}
        for name in FEATURE_COLUMNS:
            edges = np.quantile(group[name], [0.25, 0.5, 0.75])
            bins[month][name] = edges, _proportions(group[name], edges)
    return bins


def psi_score(reference, current):
    return float(np.sum((current - reference) * np.log(current / reference)))


def psi_alarm(score):
    return score >= PSI_THRESHOLD


def _runtime():
    return {"python": platform.python_version(), "sklearn": sklearn.__version__, "numpy": np.__version__}


def _numbers_close(left, right, rel=1e-6, abs_tol=1e-9):
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_numbers_close(left[k], right[k], rel, abs_tol) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_numbers_close(a, b, rel, abs_tol) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= max(abs_tol, rel * max(abs(float(left)), abs(float(right))))
    return left == right


def _baseline_payload(metrics):
    return {
        "decision_date": "2026-11-20", "split_role": "test",
        "train": {"start": TIME_SPLIT["train"][0], "end": TIME_SPLIT["train"][1]},
        "validation_for_hyperparameters": {"start": TIME_SPLIT["validation"][0], "end": TIME_SPLIT["validation"][1]},
        "test": {"start": TIME_SPLIT["test"][0], "end": TIME_SPLIT["test"][1]},
        "tuned_on_test": False, "model_version": "rf-depth10-fit-2022-12",
        "hyperparameters": RF_PARAMETERS, "metrics": metrics,
    }


def _freeze_baseline(path, metrics):
    payload = _baseline_payload(metrics)
    runtime = _runtime()
    path = Path(path)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        recorded = existing.get("runtime")
        comparable = {key: value for key, value in existing.items() if key != "runtime"}
        if not _numbers_close(comparable, payload):
            raise ValueError(f"Existing frozen baseline differs from the 2024 evaluation: {path}")
        if recorded != runtime:
            warnings.warn(
                f"Baseline runtime {recorded} differs from {runtime}; numeric metrics matched within tolerance",
                UserWarning,
                stacklevel=2,
            )
        return
    payload["runtime"] = runtime
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as handle:
        temp = Path(handle.name)
        json.dump(payload, handle, indent=2, allow_nan=False)
        handle.write("\n")
    temp.replace(path)


@dataclass
class StudioModels:
    scaler: StandardScaler
    fixed_forest: RandomForestRegressor
    monthly_forest: RandomForestRegressor
    linear: LinearRegression
    x_history: np.ndarray
    y_history: np.ndarray
    baseline: dict
    bins: dict
    calibration: dict
    detectors: dict
    fit_end: str = "2024-12"

    @property
    def versions(self):
        return {
            "rf_fixed": "rf-depth10-fit-2024-12",
            "rf_monthly": f"rf-depth10-fit-{self.fit_end}",
            "linear": f"linear-fit-{self.fit_end}",
            "seasonal": "seasonal-naive-lag12",
        }

    def predict(self, features):
        if TARGET in features:
            raise ValueError("Prediction frame contains the target")
        x = self.scaler.transform(features[FEATURES])
        fixed = _predict(self.fixed_forest, x)
        monthly = fixed.copy() if self.monthly_forest is self.fixed_forest else _predict(self.monthly_forest, x)
        return {"rf_fixed": fixed, "rf_monthly": monthly,
                "linear": _predict(self.linear, x), "seasonal": features.guest_lag_12.to_numpy(dtype=float)}

    def psi(self, features, month):
        results = {}
        for name, (edges, reference) in self.bins[month[-2:]].items():
            current = _proportions(features[name], edges)
            score = psi_score(reference, current)
            results[name] = {"value": score, "alarm": psi_alarm(score), "edges": edges.tolist(),
                             "reference": reference.tolist(), "current": current.tolist()}
        return results

    def ph(self, metrics):
        return {name: self.ph_single(name, metrics[name]["wape"]) for name in self.detectors}

    def ph_single(self, name, current):
        detector = self.detectors[name]
        mean, std = self.calibration[name]
        if current is None or not np.isfinite(current):
            return {"value": detector.statistic, "alarm": False, "z": None, "skipped": True}
        z = float((current - mean) / std)
        alarm = detector.update(z)
        return {"value": detector.statistic, "alarm": alarm, "z": z}

    def learn(self, features, actual, month):
        expected = str(pd.Period(self.fit_end, freq="M") + 1)
        if month != expected:
            raise ValueError(f"Next training month must be {expected}, received {month}")
        y = np.asarray(actual, dtype=float)
        if len(features) != N_PROVINCES or y.shape != (N_PROVINCES,) or not np.isfinite(y).all():
            raise ValueError(f"Learning {month} requires one observed target per province")
        x_history = np.concatenate([self.x_history, self.scaler.transform(features[FEATURES])])
        y_history = np.concatenate([self.y_history, y])
        forest = _fit_forest(x_history, y_history)
        linear = LinearRegression().fit(x_history, y_history)
        self.x_history, self.y_history = x_history, y_history
        self.monthly_forest, self.linear, self.fit_end = forest, linear, month


def prepare(panel, baseline_path, champion_only=False):
    """Evaluate 2024 strictly out of sample before refitting through December 2024."""
    rows = _forecast_history(panel)
    train = rows.year_month.between(*TIME_SPLIT["train"])
    valid = rows.year_month.between(*TIME_SPLIT["validation"])
    test = rows.year_month.between(*TIME_SPLIT["test"])
    scaler = StandardScaler().fit(rows.loc[train, FEATURES])
    x = scaler.transform(rows[FEATURES])
    y = rows[TARGET].to_numpy(dtype=float)
    historical = {"rf_fixed": _fit_forest(x[train], y[train]),
                  "linear": LinearRegression().fit(x[train], y[train])}
    baseline, calibration = {}, {}
    for model_key in historical if champion_only else ("rf_fixed", "linear", "seasonal"):
        predicted = (rows.guest_lag_12.to_numpy(dtype=float) if model_key == "seasonal"
                     else _predict(historical[model_key], x))
        if model_key in historical:
            scores = [wape(y[part.index], predicted[part.index])
                      for _, part in rows.loc[valid].groupby("year_month")]
            mean, std = float(np.mean(scores)), float(np.std(scores, ddof=1))
            if not np.isfinite(std) or std == 0:
                raise ValueError(f"No usable historical WAPE variation for {model_key}")
            calibration[model_key] = mean, std
        log = rows.loc[test, ["province_id", "year_month"]].assign(y_true=y[test], y_pred=predicted[test])
        report = summarize(log, rows.loc[test, "guest_lag_12"])
        for item in report["per_province"]:
            item["n_months"] = item.pop("n")
        baseline[model_key] = {"overall": report["overall"], "per_province": report["per_province"]}
    baseline["rf_monthly"] = baseline["rf_fixed"]
    calibration["rf_monthly"] = calibration["rf_fixed"]
    _freeze_baseline(baseline_path, baseline["rf_fixed"])
    forest = _fit_forest(x, y)
    detectors = {key: PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=12) for key in calibration}
    linear = None if champion_only else LinearRegression().fit(x, y)
    return StudioModels(scaler, forest, forest, linear, x, y,
                        baseline, _reference_bins(rows[valid | test]), calibration, detectors)
