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


def test_adwin_minimum_window_parameters_default_to_the_approved_values():
    from experiments.psi_adwin.adwin import ADWIN

    default = ADWIN(0.002)
    assert (default.min_window, default.min_side) == (32, 16)
    stream = [0.0] * 12 + [1.0] * 12  # 24 points: below the approved minimum window
    assert not any(default.update(v) for v in stream)
    small = ADWIN(0.002, min_window=16, min_side=8)
    assert any(small.update(v) for v in stream)


def test_relative_adwin_sees_a_4_to_20_percent_step_that_raw_adwin_and_river_miss():
    from experiments.online_replay.drift_demos import RiverADWIN, ScaledADWIN
    from experiments.psi_adwin.adwin import ADWIN

    rng = np.random.default_rng(0)
    seed = list(0.04 + 0.005 * rng.standard_normal(11))
    stream = seed + list(0.20 + 0.05 * rng.standard_normal(12))
    raw, river, rel = ADWIN(0.002, min_window=16, min_side=8), RiverADWIN(0.002), ScaledADWIN(float(np.mean(seed)))
    assert not any(raw.update(v) for v in stream)
    assert not any(river.update(v) for v in stream)
    hits = [i for i, v in enumerate(stream) if rel.update(v)]
    assert hits and hits[0] >= 15  # needs >= 8 points after the step
    # No false alarm on a stable normal stream.
    calm = ScaledADWIN(0.04)
    assert not any(calm.update(v) for v in 0.04 + 0.008 * rng.standard_normal(36))


def _in_year_step(detector=None, preset="margin_0005_confirm_2"):
    """Normal seed (24 months of ~5 % WAPE), then WAPE steps to 100 % from January of the replay year."""
    seed = [0.05] * 24
    errors = {m: 1.0 for m in MONTHS}
    y = {m: np.full(N, 100.0) for m in MONTHS}

    def predict(key, month):
        bump = 0.0 if key.startswith("champion") else 0.5
        return np.full(N, 100.0 * (1 + errors[month] + bump))

    states = []
    res = replay(MONTHS, y, {m: np.full(N, 120.0) for m in MONTHS}, predict, lambda m, e: (True, ["guest_lag_1"], False),
                 ("logdiff_expanding",), promotion_rule(preset), trigger_mode="psi_and_adwin", error_mode="monthly_wape",
                 seed=seed, initial_model="champion@2024-12", log=states.append, adwin_direction="up", detector=detector)
    return res, states


def test_approved_adwin_cannot_fire_on_a_step_that_starts_inside_the_replay_year():
    """Plain statement (Tester H1): with the approved settings (delta 0.002, window >= 32, >= 16 per
    side, monthly WAPE, 24-month normal seed) a 5 % -> 100 % WAPE step from January is never cut, so
    psi_and_adwin never triggers and prepare_retrain is never logged, even with PSI alarming all year."""
    from experiments.online_replay.replay import month_action

    _, states = _in_year_step()
    assert not any(s["adwin_cut"] for s in states)
    assert [month_action(s) for s in states] == ["watch"] * 12


def test_relative_adwin_16_8_fires_in_year_and_logs_prepare_retrain():
    from experiments.online_replay.drift_demos import ScaledADWIN
    from experiments.online_replay.replay import month_action

    res, states = _in_year_step(detector=ScaledADWIN(0.05))
    actions = [month_action(s) for s in states]
    assert "prepare_retrain" in actions
    first = actions.index("prepare_retrain")
    # A 20x relative step is big enough that the 8-point right side may mix seed and step months,
    # so it is caught in January already; smaller steps need more post-step months.
    assert MONTHS[first] == "2025-01"
    assert states[first]["cut"]["direction"] == "up"
    assert any(e["event"] == "challengers" and e["month"] == MONTHS[first] for e in res["events"])


def test_month_action_strings():
    from experiments.online_replay.replay import month_action

    base = {"trigger": False, "psi": False, "adwin_cut": False, "adwin_alarm": False, "events": []}
    assert month_action(base) == "none"
    assert month_action({**base, "psi": True}) == "watch"
    assert month_action({**base, "adwin_cut": True, "adwin_alarm": True}) == "alert"
    assert month_action({**base, "adwin_cut": True}) == "adwin_cut_ignored_down"
    assert month_action({**base, "psi": True, "adwin_cut": True}) == "watch+adwin_cut_ignored_down"
    fired = {**base, "trigger": True, "psi": True, "adwin_cut": True, "adwin_alarm": True,
             "events": [{"event": "challengers"}, {"event": "promote"}]}
    assert month_action(fired) == "prepare_retrain+promote"


def test_gate_runs_before_each_month_is_served():
    calls = []
    y = {m: np.full(N, 100.0) for m in MONTHS}

    def before(month):
        calls.append(("gate", month))
        if month == "2025-03":
            raise RuntimeError("gate hard fail")

    def predict(key, month):
        calls.append(("serve", month))
        return np.full(N, 100.0)

    try:
        replay(MONTHS, y, y, predict, lambda m, e: (False, [], False), ("x",), promotion_rule("margin_0005_confirm_2"),
               trigger_mode="psi_and_adwin", error_mode="monthly_wape", seed=[0.05] * 24, before_month=before)
    except RuntimeError:
        pass
    assert calls == [("gate", "2025-01"), ("serve", "2025-01"), ("gate", "2025-02"), ("serve", "2025-02"), ("gate", "2025-03")]
