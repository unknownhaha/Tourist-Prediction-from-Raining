"""End-to-end label-poison tests for the 2025 online replay (adapted from the QA probes).

Each test poisons labels (and rain) from month T on and checks that nothing decided or served
before T, or at T before T's label is seen, changes. These are slow (cold RF fits on a poisoned
panel), so they are marked ``slow``; they still run in the default ``pytest`` invocation.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from experiments.online_replay import experts as X
from experiments.online_replay import replay as R
from experiments.online_replay import selection as S
from experiments.online_replay.gate import GateError

pytestmark = pytest.mark.slow
# RF fits with n_jobs > 1 sum trees in a nondeterministic order, so clean-vs-poisoned values can
# differ by ~1e-16 even without leakage. Poison effects are many orders of magnitude larger.
TOL = dict(rtol=1e-9, atol=1e-12)
LABELS = ["guest_arrivals_total", "guest_arrivals_thai", "guest_arrivals_foreign"]


def poison(panel: pd.DataFrame, T: str, mode: str = "big") -> pd.DataFrame:
    p = panel.copy()
    later = p["year_month"].astype(str) >= T
    if mode == "big":
        p.loc[later, LABELS] = 1e12
        p.loc[later, "rain_avg_mm"] = 1e6
    else:
        p.loc[later, LABELS] = np.nan
        p.loc[later, "rain_avg_mm"] = np.nan
    return p


@pytest.fixture(scope="module")
def clean():
    return X.Context.build()


@pytest.fixture(scope="module")
def cfg():
    return S.load_config()


@pytest.fixture(scope="module")
def sel(cfg):
    return S.frozen_choice(cfg)


@pytest.mark.parametrize("mode", ["big", "nan"])
@pytest.mark.parametrize("T,ref_end", [("2024-06", "2024-05"), ("2025-07", "2024-12"), ("2023-04", "2022-12")])
def test_psi_reference_and_current_month_ignore_labels_from_t_on(clean, cfg, mode, T, ref_end):
    feats = cfg["drift"]["psi_features"]
    poisoned = X.Context.build(poison(clean.panel, T, mode))
    assert R.psi_values(clean.table, T, ref_end, feats) == R.psi_values(poisoned.table, T, ref_end, feats)


def _seed(ctx, sel, years=(2023, 2024)):
    out = []
    for year in years:
        part, pred, _, _ = R._champion_inputs(ctx, sel, year)
        out += R.monthly_wape_series(part, pred)
    return out


@pytest.fixture(scope="module")
def poisoned_2025_07(clean):
    return X.Context.build(poison(clean.panel, "2025-07"))


def _track(ctx, cfg, sel, gate):
    seed = _seed(ctx, sel)
    part, pred, weights, experts = R._champion_inputs(ctx, sel, 2025)
    served = {"champion": R._by_month(part, pred)}
    track = R.run_track(ctx, cfg, X.months_of(2025), served, "champion@2024-12", "2024-12", "2024-12", seed,
                        sel["covid_variant"], weights=weights, experts=experts, gate=gate)
    return seed, served, track


def test_full_2025_loop_ignores_labels_from_july_on(clean, poisoned_2025_07, cfg, sel):
    T = "2025-07"
    # With the gate on, the poisoned stream is stopped by the input gate before 2025-08 is served
    # (2025-08 is the first month whose visible history contains a poisoned label).
    with pytest.raises(GateError):
        _track(poisoned_2025_07, cfg, sel, gate=True)
    seed_c, served_c, tr_c = _track(clean, cfg, sel, gate=False)
    seed_p, served_p, tr_p = _track(poisoned_2025_07, cfg, sel, gate=False)
    np.testing.assert_allclose(seed_c, seed_p, **TOL)
    for m in X.months_of(2025):
        if m <= T:  # month T's forecast is made before its label is seen
            np.testing.assert_allclose(served_c["champion"][m], served_p["champion"][m], err_msg=m, **TOL)
    lc, lp = tr_c["log"].set_index("month"), tr_p["log"].set_index("month")
    before = [m for m in X.months_of(2025) if m < T]
    pd.testing.assert_frame_equal(lc.loc[before], lp.loc[before], check_exact=False, rtol=1e-9, atol=1e-12)
    decision = [c for c in lc.columns if c.startswith("psi_") or c.startswith("w_") or c == "served_model"]
    assert decision
    pd.testing.assert_series_equal(lc.loc[T, decision], lp.loc[T, decision], check_exact=False, rtol=1e-9, atol=1e-12)
    # The poison has teeth: month T's observed error differs.
    assert abs(lc.loc[T, "served_wape"] - lp.loc[T, "served_wape"]) > 0.5


def test_adwin_seed_ignores_labels_from_t_on(clean, sel):
    seed_c = _seed(clean, sel, years=(2024,))
    poisoned = X.Context.build(poison(clean.panel, "2024-07"))
    seed_p = _seed(poisoned, sel, years=(2024,))
    np.testing.assert_allclose(seed_c[:6], seed_p[:6], **TOL)  # 2024-01..06: fully before T
    assert abs(seed_c[6] - seed_p[6]) > 0.5  # 2024-07: its own (poisoned) label is the error being scored


def test_2025_labels_cannot_change_the_2024_selection(clean, tmp_path, monkeypatch):
    from experiments.new_features.run import _load
    from experiments.online_replay import run as RUN

    panel = _load().copy()
    in_2025 = panel["year_month"].astype(str) >= "2025-01"
    panel.loc[in_2025, LABELS] *= 3
    panel.loc[in_2025 & (panel["province_id"].astype(str).str.zfill(2) == "10"), "guest_arrivals_total"] = 1e9
    panel.loc[in_2025, "rain_avg_mm"] *= 5
    poisoned = X.Context.build(panel)
    committed = S.CONFIG_PATH.read_text(encoding="utf-8")
    tmp = tmp_path / "config.json"
    tmp.write_text(committed, encoding="utf-8")
    monkeypatch.setattr(S, "CONFIG_PATH", tmp)
    RUN.stage_select(poisoned)
    assert json.loads(tmp.read_text())["selection"] == json.loads(committed)["selection"]
    assert tmp.read_text(encoding="utf-8") == committed  # byte-stable thanks to rounding in freeze


def test_refactored_policy_reproduces_the_approved_2025_result(clean):
    out = R.reproduce_old_2025(clean)
    assert out["match"] and out["promotions"] == 0 and out["rollbacks"] == 0
