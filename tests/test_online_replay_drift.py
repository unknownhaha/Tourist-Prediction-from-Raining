"""Drift loop: ADWIN step change -> prepare_retrain, PSI-only -> watch, direction filter."""

from __future__ import annotations

import numpy as np

from experiments.psi_adwin.policy import replay
from src.stream.champion_policy import promotion_rule

MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]
N = 10


def _run(errors: dict, psi_months: set, seed: list[float], **kw):
    y = {m: np.full(N, 100.0) for m in MONTHS}
    naive = {m: np.full(N, 120.0) for m in MONTHS}

    def predict(key, month):
        base = key.split("@")[0]
        if base == "champion":
            return np.full(N, 100.0 * (1 + errors[month]))
        return np.full(N, 100.0 * (1 + errors[month] + 0.5))  # challengers are worse: no promotion

    states = []
    res = replay(MONTHS, y, naive, predict, lambda m, e: (m in psi_months, ["guest_lag_1"] if m in psi_months else [], False),
                 ("logdiff_expanding",), promotion_rule("margin_0005_confirm_2"), trigger_mode="psi_and_adwin",
                 error_mode="monthly_wape", seed=seed, initial_model="champion@2024-12", log=states.append, **kw)
    return res, states


def test_step_change_cuts_adwin_and_logs_prepare_retrain():
    seed = [0.05] * 24
    # The local ADWIN uses a Hoeffding bound for values in [0, 1] and needs >= 16 points on each
    # side of a cut, so a cut needs a mean shift of about 0.8 over 16 points: the synthetic step
    # starts 8 months before the replay year and goes 0.0 -> 1.0.
    seed = [0.0] * 16 + [1.0] * 8
    errors = {m: 1.0 for m in MONTHS}
    res, states = _run(errors, set(MONTHS), seed)
    fired = [s["month"] for s in states if s["trigger"]]
    assert fired, "a step change in WAPE with PSI on must trigger"
    first = next(s for s in states if s["adwin_cut"])
    assert first["cut"]["direction"] == "up" and first["cut"]["mean_after"] > first["cut"]["mean_before"]
    assert any(e["event"] == "challengers" for e in res["events"])
    assert len(states) == 12


def test_psi_alone_is_a_watch_without_retrain():
    seed = [0.05] * 24
    errors = {m: 0.05 for m in MONTHS}
    res, states = _run(errors, {"2025-03"}, seed)
    assert not any(s["trigger"] for s in states)
    assert [e["event"] for e in res["events"]] == ["watch"]
    assert not any(e["event"] == "challengers" for e in res["events"])


def test_adwin_direction_up_ignores_a_downward_cut():
    seed = [1.0] * 16 + [0.0] * 8
    errors = {m: 0.0 for m in MONTHS}
    _, both = _run(errors, set(MONTHS), seed, adwin_direction="both")
    _, up = _run(errors, set(MONTHS), seed, adwin_direction="up")
    assert any(s["adwin_cut"] and s["cut"]["direction"] == "down" for s in both)
    assert any(s["trigger"] for s in both)
    assert any(s["adwin_cut"] for s in up)
    assert not any(s["trigger"] or s["adwin_alarm"] for s in up)


def test_defaults_keep_the_old_registry_start():
    seed = [0.05] * 24
    errors = {m: 0.05 for m in MONTHS}
    y = {m: np.full(N, 100.0) for m in MONTHS}
    res = replay(MONTHS, y, y, lambda k, m: np.full(N, 105.0), lambda m, e: (False, [], False), ("x",),
                 promotion_rule("margin_0005_confirm_2"), trigger_mode="psi_and_adwin", error_mode="monthly_wape", seed=seed)
    assert res["registry"][0]["model"] == "logdiff_expanding@2024-12"
    assert res["registry"][0]["trained_through"] == "2024-12"
