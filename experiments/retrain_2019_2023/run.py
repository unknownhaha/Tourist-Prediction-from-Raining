"""Fresh level-target fit. Train 2019-01..2023-12, test 2024-01..2024-12.

The six documented forecast features only. 2018 is lag history and is not a
training row. 2024 is not used to fit, scale, or choose a hyperparameter.
2025 is not scored. Hyperparameters are fixed in advance.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.preprocessing import StandardScaler

from src.common.calendar import month_range
from src.common.paths import ROOT
from src.model.leakage import (
    assert_no_future_leakage,
    build_forecast_features,
    forecast_source_months,
)
from src.stream.metrics import mae, rmse, wape

HERE = Path(__file__).resolve().parent
PANEL_PATH = ROOT / "data" / "model" / "province_month_unified.parquet"
FEATURES = (
    "guest_lag_1",
    "guest_lag_12",
    "rain_lag_1",
    "rain_roll3",
    "is_covid",
    "is_covid_lag12",
)
TRAIN = ("2019-01", "2023-12")
TEST = ("2024-01", "2024-12")
N_PROVINCES = 77


def build_table(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per province for 2019-01..2024-12. Lags come from the full panel."""
    frames = []
    for month in month_range(TRAIN[0], TEST[1]):
        features = build_forecast_features(panel, month)
        features["province_id"] = features["province_id"].astype(str).str.zfill(2)
        actual = panel.loc[panel["year_month"] == month, ["province_id", "guest_arrivals_total"]].copy()
        actual["province_id"] = actual["province_id"].astype(str).str.zfill(2)
        frame = features.merge(actual, on="province_id", how="inner", validate="one_to_one")
        frame["year_month"] = month
        frames.append(frame)
    table = pd.concat(frames, ignore_index=True)
    for column in ("is_covid", "is_covid_lag12"):
        table[column] = table[column].astype(float)
    return table.sort_values(["year_month", "province_id"]).reset_index(drop=True)


def leakage_self_check(panel: pd.DataFrame) -> None:
    month = "2024-06"
    full = build_forecast_features(panel, month).reset_index(drop=True)
    trimmed = panel.loc[panel["year_month"] < month]
    again = build_forecast_features(trimmed, month).reset_index(drop=True)
    pd.testing.assert_frame_equal(full, again)
    assert_no_future_leakage(forecast_source_months(month).values(), month)


def _score(y: np.ndarray, pred: np.ndarray) -> dict:
    return {
        "wape": wape(y, pred),
        "mae": mae(y, pred),
        "rmse": rmse(y, pred),
        "n": int(len(y)),
    }


def _fit_predict(name: str, x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if name == "seasonal_naive":
        # Column 1 is guest_lag_12. There is nothing to fit.
        return x_train[:, 1].copy(), x_test[:, 1].copy()
    if name == "linear":
        scaler = StandardScaler().fit(x_train)
        model = LinearRegression().fit(scaler.transform(x_train), y_train)
        return model.predict(scaler.transform(x_train)), model.predict(scaler.transform(x_test))
    if name == "ridge":
        scaler = StandardScaler().fit(x_train)
        model = Ridge(alpha=1.0, random_state=42).fit(scaler.transform(x_train), y_train)
        return model.predict(scaler.transform(x_train)), model.predict(scaler.transform(x_test))
    if name == "random_forest":
        model = RandomForestRegressor(
            n_estimators=300,
            max_depth=10,
            min_samples_leaf=3,
            random_state=42,
            n_jobs=4,
        ).fit(x_train, y_train)
        model.n_jobs = 1
        return model.predict(x_train), model.predict(x_test)
    if name == "catboost":
        model = CatBoostRegressor(
            iterations=500,
            depth=6,
            learning_rate=0.05,
            loss_function="RMSE",
            random_seed=42,
            verbose=False,
            allow_writing_files=False,
        ).fit(x_train, y_train)
        return model.predict(x_train), model.predict(x_test)
    raise KeyError(name)


def main() -> None:
    panel = pd.read_parquet(PANEL_PATH)
    panel["province_id"] = panel["province_id"].astype(str).str.zfill(2)
    panel["year_month"] = panel["year_month"].astype(str)
    leakage_self_check(panel)
    table = build_table(panel)

    train = table["year_month"].between(TRAIN[0], TRAIN[1])
    test = table["year_month"].between(TEST[0], TEST[1])
    if int(train.sum()) != 60 * N_PROVINCES or int(test.sum()) != 12 * N_PROVINCES:
        raise RuntimeError(f"unexpected row counts: train={int(train.sum())} test={int(test.sum())}")
    if table.loc[train | test, list(FEATURES) + ["guest_arrivals_total"]].isna().any().any():
        raise RuntimeError("training or test rows contain null features or targets")
    if not table.loc[train, "year_month"].max() < table.loc[test, "year_month"].min():
        raise RuntimeError("train months overlap the test year")

    x_train = table.loc[train, list(FEATURES)].to_numpy(dtype=float)
    y_train = table.loc[train, "guest_arrivals_total"].to_numpy(dtype=float)
    x_test = table.loc[test, list(FEATURES)].to_numpy(dtype=float)
    y_test = table.loc[test, "guest_arrivals_total"].to_numpy(dtype=float)

    names = ("seasonal_naive", "linear", "ridge", "random_forest", "catboost")
    rows = []
    for name in names:
        pred_train, pred_test = _fit_predict(name, x_train, y_train, x_test)
        pred_train = np.maximum(pred_train, 0.0)
        pred_test = np.maximum(pred_test, 0.0)
        record = {
            "model": name,
            "train_2019_2023": _score(y_train, pred_train),
            "test_2024": _score(y_test, pred_test),
        }
        rows.append(record)
        print(
            f"{name}\ttrain_wape={record['train_2019_2023']['wape']:.6f}\t"
            f"test_wape={record['test_2024']['wape']:.6f}",
            flush=True,
        )

    payload = {
        "target": "guest_arrivals_total",
        "features": list(FEATURES),
        "train": {"start": TRAIN[0], "end": TRAIN[1], "n": int(train.sum())},
        "test": {"start": TEST[0], "end": TEST[1], "n": int(test.sum())},
        "hyperparameters_fixed": {
            "seasonal_naive": "predict guest_lag_12",
            "linear": "LinearRegression, StandardScaler fit on train only",
            "ridge": "Ridge alpha=1.0, StandardScaler fit on train only",
            "random_forest": "n_estimators=300, max_depth=10, min_samples_leaf=3, random_state=42",
            "catboost": "iterations=500, depth=6, learning_rate=0.05, loss_function=RMSE, random_seed=42",
        },
        "predictions_clipped_at_zero": True,
        "tuned_on_test": False,
        "models": rows,
    }
    path = HERE / "results.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}", flush=True)


if __name__ == "__main__":
    main()
