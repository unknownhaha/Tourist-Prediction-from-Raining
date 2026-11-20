"""Drift hooks. Data engineering delivers the interface. DS/MLE choose the detector."""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd


class DriftHook(Protocol):
    name: str

    def update_errors(self, errors: pd.Series) -> bool:
        """Return True when the error stream raises an alarm."""

    def update_features(self, features: pd.DataFrame) -> bool:
        """Return True when input features raise an alarm against the reference set."""


class NullDrift:
    """Record nothing. Default, so a replay does not retrain."""

    name = "none"

    def update_errors(self, errors: pd.Series) -> bool:
        return False

    def update_features(self, features: pd.DataFrame) -> bool:
        return False


class PageHinkley:
    """Page-Hinkley on a scalar stream. Deterministic. No extra dependency."""

    def __init__(self, delta: float = 0.01, threshold: float = 0.5) -> None:
        self.delta = delta
        self.threshold = threshold
        self._n = 0
        self._mean = 0.0
        self._cumsum = 0.0
        self._min_cumsum = 0.0

    def update(self, value: float) -> bool:
        self._n += 1
        self._mean += (value - self._mean) / self._n
        self._cumsum += value - self._mean - self.delta
        self._min_cumsum = min(self._min_cumsum, self._cumsum)
        return (self._cumsum - self._min_cumsum) > self.threshold


class PageHinkleyOnError:
    """EXAMPLE hook only. DS/MLE choose the real detector and its thresholds.

    Watches monthly WAPE. Does not retrain by itself. The delta and threshold
    below are wiring defaults, not a team decision.
    """

    name = "page-hinkley"

    def __init__(self, delta: float = 0.01, threshold: float = 0.5) -> None:
        self._detector = PageHinkley(delta=delta, threshold=threshold)

    def update_errors(self, errors: pd.Series) -> bool:
        actual = errors.attrs.get("y_true")
        pred = errors.attrs.get("y_pred")
        if actual is None or pred is None:
            value = float(np.mean(np.abs(errors.to_numpy(dtype=float))))
        else:
            y = np.asarray(actual, dtype=float)
            p = np.asarray(pred, dtype=float)
            denom = float(np.abs(y).sum())
            value = float(np.abs(y - p).sum() / denom) if denom else 0.0
        return self._detector.update(value)

    def update_features(self, features: pd.DataFrame) -> bool:
        return False


def hook_from_name(name: str) -> DriftHook:
    if name in ("", "none"):
        return NullDrift()
    if name == "page-hinkley":
        return PageHinkleyOnError()
    raise ValueError(f"unknown drift hook: {name}")
