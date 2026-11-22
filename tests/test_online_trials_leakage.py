"""Every model in experiments/online_trials respects the rolling-origin contract:
a frozen forecast never reads a target-year label, and an online/monthly forecast for
month t never reads month t's label or anything later."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from experiments.beat_seasonal_naive.run import _rain_history
from experiments.new_features.run import LOGDIFF, _load
from experiments.online_trials.models import (
    add_trial_features,
    build_trial_table,
    ewa,
    lgbm_spec,
    predict_basefix,
    predict_spec,
    ridge_spec,
    small,
    switch,
)
from src.common.paths import DATA

pytestmark = pytest.mark.skipif(
    not (DATA / "model" / "province_month_unified.parquet").exists(), reason="modeling table not built"
)

MONTHS_2023 = [f"2023-{m:02d}" for m in range(1, 13)]
LABEL = "guest_arrivals_total"


@pytest.fixture(scope="module")
def tables():
    panel = _load()
    clean = build_trial_table(panel)
    poisoned_panel = panel.copy()
    later = poisoned_panel["year_month"] >= "2023-01"
    for column in ("guest_arrivals_total", "guest_arrivals_thai", "guest_arrivals_foreign", "rain_avg_mm"):
        poisoned_panel.loc[later, column] = poisoned_panel.loc[later, column] * 7.0 + 13.0
    poisoned = build_trial_table(poisoned_panel)
    return panel, (clean, _rain_history(panel)), (poisoned, _rain_history(poisoned_panel))


def _hidden(table: pd.DataFrame, start: str) -> pd.DataFrame:
    out = table.copy()
    out.loc[out["year_month"] >= start, LABEL] = np.nan
    return out


BATCH = {
    "basefix": lambda t, h, te, ms: predict_basefix(t, h, te, ms, spec=small(LOGDIFF)),
    "basefix_momentum_gap": lambda t, h, te, ms: predict_basefix(
        t, h, te, ms, momentum=True, gap_feature=True, spec=small(LOGDIFF)),
    "momentum_only": lambda t, h, te, ms: predict_basefix(
        t, h, te, ms, switch_base=False, momentum=True, spec=small(LOGDIFF)),
    "lgbm": lambda t, h, te, ms: predict_spec(t, h, te, ms, lgbm_spec("t", n_estimators=20, learning_rate=0.1, num_leaves=7)),
    "ridge": lambda t, h, te, ms: predict_spec(t, h, te, ms, ridge_spec(10.0), clip=True, log_features=True),
}


def test_trial_features_for_month_t_only_read_earlier_months(tables):
    panel, (clean, _), _ = tables
    # Poison the panel from 2023-06 on; trial features for months up to 2023-06 must not move.
    poisoned = panel.copy()
    later = poisoned["year_month"] >= "2023-06"
    poisoned.loc[later, LABEL] = poisoned.loc[later, LABEL] * 5.0 + 3.0
    a = add_trial_features(clean, panel)
    b = add_trial_features(clean, poisoned)
    keep = a["year_month"] <= "2023-06"
    for column in ("base_clean", "base_gap", "recovery_log", "yoy_log", "base_log_gap"):
        np.testing.assert_allclose(a.loc[keep, column].to_numpy(float), b.loc[keep, column].to_numpy(float), equal_nan=True)
    # The base for a 2023 month skips the COVID years 2020-03..2022-12.
    row = a.loc[(a["year_month"] == "2023-05")].iloc[0]
    assert row["base_gap"] == 48 and row["base_switched"] == 1.0


@pytest.mark.parametrize("name", list(BATCH))
def test_batch_target_month_must_be_after_cutoff(tables, name):
    _, (table, history), _ = tables
    with pytest.raises(AssertionError):
        BATCH[name](table, history, "2023-01", ["2023-01"])


@pytest.mark.parametrize("name", list(BATCH))
def test_batch_frozen_2023_never_reads_a_2023_label(tables, name):
    _, (clean, history), _ = tables
    a = BATCH[name](clean, history, "2022-12", MONTHS_2023)
    b = BATCH[name](_hidden(clean, "2023-01"), history, "2022-12", MONTHS_2023)
    np.testing.assert_allclose(a, b)
    assert len(a) == 12 * 77


@pytest.mark.parametrize("name", list(BATCH))
def test_batch_monthly_refit_ignores_target_month_and_later(tables, name):
    _, (clean, h_clean), (poisoned, h_poisoned) = tables
    a = BATCH[name](clean, h_clean, "2022-12", ["2023-01"])
    b = BATCH[name](poisoned, h_poisoned, "2022-12", ["2023-01"])
    np.testing.assert_allclose(a, b)
    c = BATCH[name](clean, h_clean, "2023-01", ["2023-02"])
    d = BATCH[name](poisoned, h_poisoned, "2023-01", ["2023-02"])
    assert not np.allclose(c, d)


def test_switch_and_blend_inputs_do_not_include_the_label(tables):
    _, (clean, _), _ = tables
    part = clean.loc[clean["year_month"].isin(MONTHS_2023)]
    rng = np.random.default_rng(0)
    ld, lv = rng.uniform(1, 2, len(part)), rng.uniform(3, 4, len(part))
    a = switch(part, ld, lv, 0.5)
    b = switch(_hidden(part, "2023-01"), ld, lv, 0.5)
    np.testing.assert_allclose(a, b)
    # Every 2023 base month is a COVID month, so the rule picks the level forest.
    np.testing.assert_allclose(a, lv)


def test_ewa_weight_for_month_t_uses_only_earlier_months(tables):
    _, (clean, _), _ = tables
    part = clean.loc[clean["year_month"].isin(MONTHS_2023)]
    rng = np.random.default_rng(1)
    experts = {"a": part[LABEL].to_numpy(float) * rng.uniform(0.8, 1.2, len(part)),
               "b": part[LABEL].to_numpy(float) * rng.uniform(0.5, 1.5, len(part))}
    changed = part.copy()
    later = changed["year_month"] >= "2023-06"
    changed.loc[later, LABEL] = changed.loc[later, LABEL] * 9.0
    a, trace_a = ewa(part, experts, 5.0)
    b, trace_b = ewa(changed, experts, 5.0)
    upto = (part["year_month"] <= "2023-06").to_numpy()
    np.testing.assert_allclose(a[upto], b[upto])
    assert not np.allclose(a[~upto], b[~upto])
    assert trace_a[0]["w_a"] == pytest.approx(0.5)


@pytest.mark.parametrize("kind,params", [
    ("river_linreg", {"lr": 0.001}),
    ("river_pa", {"C": 0.01}),
    ("river_blr", {"smoothing": 0.999}),
])
def test_river_linear_frozen_and_online_never_read_future_labels(tables, kind, params):
    pytest.importorskip("river")
    from experiments.online_trials.models import river_linear_year

    _, (clean, history), _ = tables
    frozen_a, online_a = river_linear_year(clean, history, 2023, kind, params)
    frozen_b, online_b = river_linear_year(_hidden(clean, "2023-06"), history, 2023, kind, params)
    months = clean.loc[clean["year_month"].isin(MONTHS_2023), "year_month"].to_numpy()
    np.testing.assert_allclose(frozen_a, frozen_b)
    upto = months <= "2023-06"
    np.testing.assert_allclose(online_a[upto], online_b[upto])
    assert not np.allclose(online_a[months == "2023-07"], online_b[months == "2023-07"])
    frozen_c, _ = river_linear_year(_hidden(clean, "2023-01"), history, 2023, kind, params)
    np.testing.assert_allclose(frozen_a, frozen_c)


@pytest.mark.parametrize("kind,params", [
    ("snarimax", {"p": 1, "d": 0, "q": 0, "sp": 0, "sd": 1, "sq": 0, "lr": 0.01}),
    ("holtwinters", {"alpha": 0.5, "beta": 0.01, "gamma": 0.1}),
])
def test_time_series_frozen_and_online_never_read_future_labels(tables, kind, params):
    pytest.importorskip("river")
    from experiments.online_trials.models import ts_year

    panel, (clean, _), _ = tables
    hidden_panel = panel.copy()
    hidden_panel.loc[hidden_panel["year_month"] >= "2023-06", LABEL] = np.nan
    frozen_a, online_a = ts_year(panel, clean, 2023, kind, params)
    frozen_b, online_b = ts_year(hidden_panel, clean, 2023, kind, params)
    months = clean.loc[clean["year_month"].isin(MONTHS_2023), "year_month"].to_numpy()
    np.testing.assert_allclose(frozen_a, frozen_b)
    upto = months <= "2023-06"
    np.testing.assert_allclose(online_a[upto], online_b[upto])
    assert not np.allclose(online_a[months == "2023-07"], online_b[months == "2023-07"])
