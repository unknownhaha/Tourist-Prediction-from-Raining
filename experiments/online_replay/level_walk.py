"""Six-feature level models for a click-through 2024-01..2025-12 walk.

Linear is the champion. Ridge, CatBoost, and Random Forest are challengers.
Seasonal naive is the baseline. Every forecast for month t is fit on
2019-01..t-1. The target month is not in that fit and is not a feature source.
"""

from __future__ import annotations

from pathlib import Path

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
    shift_year_month,
)
from src.stream.metrics import wape
from src.stream.studio_models import _proportions, psi_score

PANEL_PATH = ROOT / "data" / "model" / "province_month_unified.parquet"
FEATURES = (
    "guest_lag_1",
    "guest_lag_12",
    "rain_lag_1",
    "rain_roll3",
    "is_covid",
    "is_covid_lag12",
)
PSI_FEATURES = ("guest_lag_1", "guest_lag_12", "rain_lag_1", "rain_roll3")
MODEL_KEYS = ("linear", "ridge", "catboost", "random_forest")
REFERENCE_END = "2023-12"
N_PROVINCES = 77


def load_panel(path: Path | None = None) -> pd.DataFrame:
    frame = pd.read_parquet(path or PANEL_PATH)
    frame["province_id"] = frame["province_id"].astype(str).str.zfill(2)
    frame["year_month"] = frame["year_month"].astype(str)
    return frame.sort_values(["year_month", "province_id"]).reset_index(drop=True)


class LevelWalk:
    def __init__(self, panel: pd.DataFrame | None = None):
        self.panel = load_panel() if panel is None else panel
        self._rows: dict[str, pd.DataFrame] = {}

    def month_rows(self, month: str) -> pd.DataFrame:
        """Features from months before `month`, plus that month's label for scoring after predict."""
        if month not in self._rows:
            history = self.panel.loc[self.panel["year_month"] < month]
            if history.empty:
                raise ValueError(f"no history before {month}")
            assert_no_future_leakage(forecast_source_months(month).values(), month)
            features = build_forecast_features(history, month)
            features["province_id"] = features["province_id"].astype(str).str.zfill(2)
            labels = self.panel.loc[
                self.panel["year_month"] == month, ["province_id", "province_th", "guest_arrivals_total"]
            ].copy()
            labels["province_id"] = labels["province_id"].astype(str).str.zfill(2)
            frame = features.merge(labels, on="province_id", how="inner", validate="one_to_one")
            frame = frame.sort_values("province_id").reset_index(drop=True)
            if len(frame) != N_PROVINCES or frame.duplicated("province_id").any():
                raise ValueError(f"{month} does not have {N_PROVINCES} provinces")
            if frame.loc[:, list(FEATURES)].isna().any().any() or frame["guest_arrivals_total"].isna().any():
                raise ValueError(f"{month} has null features or targets")
            for column in ("is_covid", "is_covid_lag12"):
                frame[column] = frame[column].astype(float)
            self._rows[month] = frame
        return self._rows[month]

    def table_through(self, end: str) -> pd.DataFrame:
        frames = []
        for month in month_range("2019-01", end):
            row = self.month_rows(month).copy()
            row["year_month"] = month
            frames.append(row)
        table = pd.concat(frames, ignore_index=True)
        if table["year_month"].max() != end or (table["year_month"] > end).any():
            raise ValueError(f"training table does not end at {end}")
        return table

    def fit_through(self, end: str) -> dict:
        table = self.table_through(end)
        x = table.loc[:, list(FEATURES)].to_numpy(dtype=float)
        y = table["guest_arrivals_total"].to_numpy(dtype=float)
        return fit_level_models(x, y)

    def predict(self, models: dict, month: str) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
        frame = self.month_rows(month)
        x = frame.loc[:, list(FEATURES)].to_numpy(dtype=float)
        pred = {key: _predict(models[key], x) for key in models}
        pred["seasonal"] = frame["guest_lag_12"].to_numpy(dtype=float)
        return frame, pred

    def seed_wapes(self, start: str) -> list[float]:
        """Twelve one-step Linear WAPEs from the year of `start`. Each fit stops the month before."""
        year = start[:4]
        values = []
        for month in month_range(f"{year}-01", f"{year}-12"):
            trained = fit_level_models(
                *_xy(self.table_through(shift_year_month(month, 1))),
                linear_only=True,
            )
            frame, pred = self.predict(trained, month)
            y = frame["guest_arrivals_total"].to_numpy(dtype=float)
            values.append(wape(y, pred["linear"]))
        if len(values) != 12 or not np.isfinite(values).all():
            raise ValueError("Page-Hinkley seed must be 12 finite monthly WAPEs")
        return values

    def psi(self, month: str) -> dict[str, float]:
        start = shift_year_month(REFERENCE_END, 23)
        months = [item for item in month_range(start, REFERENCE_END) if item[5:7] == month[5:7]]
        frames = []
        for item in [*months, month]:
            row = self.month_rows(item).copy()
            row["year_month"] = item
            frames.append(row)
        table = pd.concat(frames, ignore_index=True)
        return {
            name: _psi(table, month, REFERENCE_END, name)
            for name in PSI_FEATURES
        }


def _psi(table: pd.DataFrame, month: str, reference_end: str, name: str) -> float:
    start = shift_year_month(reference_end, 23)
    ref = table.loc[table["year_month"].between(start, reference_end) & (table["year_month"].str[-2:] == month[-2:])]
    current = table.loc[table["year_month"] == month, name].to_numpy(dtype=float)
    edges = np.quantile(ref[name].to_numpy(dtype=float), [0.25, 0.5, 0.75])
    return psi_score(_proportions(ref[name], edges), _proportions(current, edges))


def _xy(table: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    return (
        table.loc[:, list(FEATURES)].to_numpy(dtype=float),
        table["guest_arrivals_total"].to_numpy(dtype=float),
    )


def fit_level_models(x: np.ndarray, y: np.ndarray, *, linear_only: bool = False) -> dict:
    scaler = StandardScaler().fit(x)
    scaled = scaler.transform(x)
    models = {
        "linear": {"kind": "scaled", "scaler": scaler, "model": LinearRegression().fit(scaled, y)},
    }
    if linear_only:
        return models
    ridge_scaler = StandardScaler().fit(x)
    models["ridge"] = {
        "kind": "scaled",
        "scaler": ridge_scaler,
        "model": Ridge(alpha=1.0, random_state=42).fit(ridge_scaler.transform(x), y),
    }
    forest = RandomForestRegressor(
        n_estimators=300, max_depth=10, min_samples_leaf=3, random_state=42, n_jobs=4,
    ).fit(x, y)
    forest.n_jobs = 1
    models["random_forest"] = {"kind": "tree", "model": forest}
    boosting = CatBoostRegressor(
        iterations=500,
        depth=6,
        learning_rate=0.05,
        loss_function="RMSE",
        random_seed=42,
        verbose=False,
        allow_writing_files=False,
    ).fit(x, y)
    models["catboost"] = {"kind": "tree", "model": boosting}
    return models


def _predict(spec: dict, x: np.ndarray) -> np.ndarray:
    model = spec["model"]
    raw = model.predict(spec["scaler"].transform(x) if spec["kind"] == "scaled" else x)
    return np.maximum(np.asarray(raw, dtype=float), 0.0)
