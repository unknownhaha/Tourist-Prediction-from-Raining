"""Online model protocol and the shipped seasonal-naive baseline.

The team's model replaces `SeasonalNaive`. `learn` is part of the contract
even when a baseline ignores it. No river or scikit-learn dependency.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd


class OnlineModel(Protocol):
    model_version: str

    def predict(self, features: pd.DataFrame) -> pd.Series:
        """One prediction per row. Must not read the target."""

    def learn(self, features: pd.DataFrame, y_true: pd.Series) -> None:
        """Update from the month that was just revealed."""


class SeasonalNaive:
    """Predict `guest_lag_12`. `learn` keeps the model unchanged."""

    model_version = "seasonal-naive-lag12"

    def predict(self, features: pd.DataFrame) -> pd.Series:
        return features["guest_lag_12"].astype(float).reset_index(drop=True)

    def learn(self, features: pd.DataFrame, y_true: pd.Series) -> None:
        return None


class OnlineLinear:
    """Per-province linear step, deterministic, numpy only.

    The first prediction for a province is `guest_lag_12`. Later months take
    one gradient step on the revealed target. Not the default baseline.
    """

    model_version = "online-linear-numpy"

    def __init__(self, seed: int = 2025, step: float = 1e-4) -> None:
        self.seed = seed
        self.step = step
        self._weights: dict[str, np.ndarray] = {}

    def _design(self, frame: pd.DataFrame) -> np.ndarray:
        return np.column_stack(
            [
                frame["guest_lag_1"].to_numpy(dtype=float),
                frame["guest_lag_12"].to_numpy(dtype=float),
                frame["rain_lag_1"].to_numpy(dtype=float),
                frame["rain_roll3"].to_numpy(dtype=float),
                frame["is_covid"].map(float).to_numpy(dtype=float),
                frame["is_covid_lag12"].map(float).to_numpy(dtype=float),
                np.ones(len(frame), dtype=float),
            ]
        )

    def predict(self, features: pd.DataFrame) -> pd.Series:
        design = self._design(features)
        preds = np.empty(len(features), dtype=float)
        for index, province_id in enumerate(features["province_id"].astype(str)):
            weights = self._weights.get(province_id)
            if weights is None:
                preds[index] = design[index, 1]
            else:
                preds[index] = float(weights @ design[index])
        return pd.Series(preds)

    def learn(self, features: pd.DataFrame, y_true: pd.Series) -> None:
        design = self._design(features)
        target = y_true.to_numpy(dtype=float)
        for index, province_id in enumerate(features["province_id"].astype(str)):
            weights = self._weights.get(province_id)
            if weights is None:
                weights = np.zeros(design.shape[1], dtype=float)
                weights[1] = 1.0
            row = design[index]
            error = float(weights @ row) - float(target[index])
            scale = float(row @ row) + 1.0
            self._weights[province_id] = weights - self.step * error * row / scale


def model_from_name(name: str, seed: int) -> OnlineModel:
    if name in ("seasonal-naive", "lag-12"):
        return SeasonalNaive()
    if name == "online-linear":
        return OnlineLinear(seed=seed)
    raise ValueError(f"unknown model: {name}")
