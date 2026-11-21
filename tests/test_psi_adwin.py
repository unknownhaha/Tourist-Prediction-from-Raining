"""ADWIN, trigger combinations, and rollback without the full 2025 replay."""

from __future__ import annotations

import numpy as np
import pytest

from experiments.psi_adwin.adwin import ADWIN
from experiments.psi_adwin.policy import replay
from experiments.psi_adwin.triggers import trigger_fires
from src.stream.champion_policy import promotion_rule


def test_adwin_ignores_a_constant_stream_and_flags_a_level_shift():
    quiet = ADWIN(delta=0.002)
    assert quiet.delta == 0.002
    assert not any(quiet.update(0.2) for _ in range(80))
    shifted = ADWIN(delta=0.002)
    for _ in range(80):
        shifted.update(0.0)
    alarmed = False
    for _ in range(80):
        alarmed = shifted.update(1.0) or alarmed
    assert alarmed


def test_adwin_rejects_a_delta_outside_zero_and_one():
    with pytest.raises(ValueError):
        ADWIN(delta=0.0)


def test_trigger_truth_table_and_two_month_gate():
    assert trigger_fires("psi", True, False, [])
    assert not trigger_fires("psi", False, True, [])
    assert trigger_fires("adwin", False, True, [])
    assert trigger_fires("psi_or_adwin", False, True, [])
    assert trigger_fires("psi_and_adwin", True, True, [])
    assert not trigger_fires("psi_and_adwin", True, False, [])
    assert not trigger_fires("psi_gated", True, False, [True], gate_months=2)
    assert trigger_fires("psi_gated", False, True, [True], gate_months=2)
    assert not trigger_fires("psi_gated", False, True, [False], gate_months=2)
    with pytest.raises(KeyError):
        trigger_fires("other", True, True, [])


def test_rollback_restores_the_previous_model_after_two_worse_months():
    months = [f"2025-{n:02d}" for n in range(1, 7)]
    y = {month: np.array([10.0, 10.0]) for month in months}
    naive = {month: np.array([8.0, 8.0]) for month in months}

    def predict(key, month):
        if key == "logdiff_expanding@2025-01" and month == "2025-02":
            return np.array([10.0, 10.0])
        if key == "logdiff_expanding@2025-02" and month == "2025-03":
            return np.array([10.0, 10.0])
        if key == "logdiff_expanding@2025-02":
            return np.array([0.0, 0.0])
        return np.array([6.0, 6.0])

    def psi(month, reference_end):
        hit = month in {"2025-02", "2025-03"}
        return hit, (["guest_lag_1"] if hit else []), False

    rule = promotion_rule("margin_0005_confirm_2")
    result = replay(
        months, y, naive, predict, psi, ("logdiff_expanding",), rule,
        trigger_mode="psi", rollback_months=2,
    )
    promotions = [event for event in result["events"] if event["event"] == "promote"]
    rollbacks = [event for event in result["events"] if event["event"] == "rollback"]
    assert len(promotions) == 1
    assert promotions[0]["effective_from"] == "2025-04"
    assert len(rollbacks) == 1
    assert rollbacks[0]["effective_from"] == "2025-06"
    assert result["registry"][1]["rolled_back"] is True
    served = {}
    for row in result["rows"]:
        served.setdefault(row["year_month"], row["y_pred"])
    assert served["2025-03"] == 6.0
    assert served["2025-04"] == 0.0
    assert served["2025-05"] == 0.0
    assert served["2025-06"] == 6.0
