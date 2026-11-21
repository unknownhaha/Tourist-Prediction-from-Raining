"""ADWIN, trigger combinations, and rollback without the full 2025 replay."""

from __future__ import annotations

import json
from pathlib import Path

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


def test_smaller_delta_is_stricter_on_the_unit_interval():
    mild = [0.0] * 48 + [0.55] * 48
    loose = ADWIN(delta=0.5)
    tight = ADWIN(delta=1e-6)
    loose_hit = any(loose.update(value) for value in mild)
    tight_hit = any(tight.update(value) for value in mild)
    assert loose_hit
    assert not tight_hit


def test_province_scale_errors_alarm_at_the_compared_deltas():
    shift = [1000.0] * 40 + [8000.0] * 40
    for delta in (0.002, 1e-3, 1e-4, 1e-5, 1e-6):
        detector = ADWIN(delta=delta)
        assert any(detector.update(value) for value in shift)


def test_twelve_monthly_points_never_cut():
    detector = ADWIN(delta=0.5)
    assert not any(detector.update(0.9) for _ in range(12))
    assert len(detector.window) == 12


def test_a_seeded_wape_stream_can_alarm_inside_the_replay():
    months = [f"2025-{n:02d}" for n in range(1, 13)]
    y = {month: np.array([100.0, 100.0]) for month in months}
    naive = {month: np.array([80.0, 80.0]) for month in months}

    def predict(key, month):
        return np.array([20.0, 20.0])

    def psi(month, reference_end):
        return False, [], False

    unseeded = replay(
        months, y, naive, predict, psi, ("logdiff_expanding",),
        promotion_rule("margin_0005_confirm_2"),
        trigger_mode="adwin", error_mode="monthly_wape", delta=0.5,
    )
    seeded = replay(
        months, y, naive, predict, psi, ("logdiff_expanding",),
        promotion_rule("margin_0005_confirm_2"),
        trigger_mode="adwin", error_mode="monthly_wape", delta=0.5,
        seed=[0.05] * 32,
    )
    assert unseeded["adwin_alarm_months"] == []
    assert seeded["seed_length"] == 32
    assert seeded["adwin_alarm_months"]
    assert all(month.startswith("2025") for month in seeded["adwin_alarm_months"])


def test_replay_rejects_an_unknown_error_mode():
    with pytest.raises(KeyError):
        replay(
            ["2025-01"], {"2025-01": np.array([1.0])}, {"2025-01": np.array([1.0])},
            lambda key, month: np.array([1.0]), lambda month, end: (False, [], False),
            ("logdiff_expanding",), promotion_rule("margin_0005_confirm_2"),
            trigger_mode="adwin", error_mode="other",
        )


def test_primary_config_is_the_2026_11_21_decision():
    config = json.loads((Path(__file__).resolve().parents[1] / "experiments" / "psi_adwin" / "config.json").read_text())
    assert config["decision_date"] == "2026-11-21"
    assert config["adwin_input"] == "monthly_wape"
    assert config["adwin_delta"] == 0.002
    assert config["adwin_seed"] == "oos_expanding_2023_2024"
    assert config["challenger_trigger"] == "psi_and_adwin"
    assert config["psi_only"] == "watch"
    assert config["promotion_preset"] == "margin_0005_confirm_2"
    assert config["psi_reference"] == "seasonal_2y"
    assert config["refresh_reference_after_promotion"] is False
    assert config["rollback_months"] == 2
    assert "is_covid" in config["covid_flags"]


def test_psi_alone_is_a_watch_and_does_not_train_challengers():
    months = ["2025-01", "2025-02"]
    y = {
        "2025-01": np.full(40, 1000.0),
        "2025-02": np.full(40, 8000.0),
    }
    naive = {month: np.full(40, 900.0) for month in months}

    def predict(key, month):
        return np.full(40, 1000.0)

    def psi(month, reference_end):
        return True, ["guest_lag_1"], False

    result = replay(
        months, y, naive, predict, psi, ("logdiff_expanding",),
        promotion_rule("margin_0005_confirm_2"),
        trigger_mode="psi_and_adwin", error_mode="province_abs", delta=0.002,
    )
    watches = [event for event in result["events"] if event["event"] == "watch"]
    trained = [event for event in result["events"] if event["event"] == "challengers"]
    assert [event["month"] for event in watches] == ["2025-01"]
    assert watches[0]["retrain"] is False
    assert [event["month"] for event in trained] == ["2025-02"]
    assert result["adwin_alarm_months"] == ["2025-02"]


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
