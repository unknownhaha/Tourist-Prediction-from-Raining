"""Leakage tests for every new piece of the 2025 online replay."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from experiments.online_replay import experts as X
from experiments.online_replay.features import REC_COLUMNS, add_features, guest_wide, profile_years
from experiments.online_trials.models import ewa

NEW = ("exp_lr_recent", "exp_lr3_recent", "seasonal_last_month_forecast", *REC_COLUMNS, "event_lag1")
T = "2024-06"


@pytest.fixture(scope="module")
def ctx():
    return X.Context.build()


@pytest.fixture(scope="module")
def poisoned(ctx):
    panel = ctx.panel.copy()
    later = panel["year_month"] >= T
    panel.loc[later, ["guest_arrivals_total", "guest_arrivals_thai", "guest_arrivals_foreign"]] = 1e12
    panel.loc[later, "rain_avg_mm"] = 1e6
    return X.Context.build(panel)


def test_profile_years_are_the_last_two_normal_years_before_the_row(ctx):
    assert ctx.normal == [2018, 2019, 2023, 2024, 2025]
    assert profile_years(2023, ctx.normal) == [2018, 2019]
    assert profile_years(2024, ctx.normal) == [2019, 2023]
    assert profile_years(2025, ctx.normal) == [2023, 2024]


@pytest.mark.parametrize("year", [2023, 2024, 2025])
def test_profile_never_reads_its_own_year_or_later(ctx, year):
    wide = guest_wide(ctx.panel)
    poisoned = wide.copy()
    poisoned.loc[:, [c for c in wide.columns if c >= f"{year}-01"]] = np.nan
    rows = ctx.table.loc[ctx.table["year_month"].str.startswith(str(year))]
    a = add_features(rows, wide, ctx.normal)
    b = add_features(rows, poisoned, ctx.normal)
    for col in NEW:
        np.testing.assert_array_equal(a[col].to_numpy(), b[col].to_numpy(), err_msg=col)


def test_month_t_features_ignore_labels_from_t_on(ctx, poisoned):
    a = ctx.table.loc[ctx.table["year_month"] == T].set_index("province_id")
    b = poisoned.table.loc[poisoned.table["year_month"] == T].set_index("province_id")
    for col in NEW + ("guest_lag_1", "guest_lag_12", "rain_lag_1", "rain_roll3"):
        np.testing.assert_allclose(a[col].to_numpy(), b[col].to_numpy(), err_msg=col)
    # But the label itself was poisoned, so the test has teeth.
    assert (b["guest_arrivals_total"] == 1e12).all()


@pytest.mark.parametrize("target,variant", [("logdiff", "C0"), ("level", "C3"), ("logdiff", "C3")])
def test_forecast_of_t_ignores_labels_from_t_on(ctx, poisoned, target, variant):
    spec = X.VARIANTS[variant]
    a, _ = X.fit_predict(ctx, target, "2024-05", [T], spec["add"], spec["drop"])
    b, _ = X.fit_predict(poisoned, target, "2024-05", [T], spec["add"], spec["drop"])
    np.testing.assert_allclose(a, b, rtol=1e-12)


def test_seasonal_last_month_forecast_uses_only_lag1_and_profile(ctx):
    rows = ctx.table.loc[ctx.table["year_month"] == "2025-03"]
    wide = guest_wide(ctx.panel)
    L = (wide[["2023-03", "2024-03"]].mean(axis=1) / wide[["2023-02", "2024-02"]].mean(axis=1)).reindex(rows["province_id"])
    np.testing.assert_allclose(rows["seasonal_last_month_forecast"].to_numpy(), rows["guest_lag_1"].to_numpy() * L.to_numpy())


def test_event_flags_are_lagged_and_event_now_never_enters_a_model(ctx):
    cr = ctx.table.loc[ctx.table["province_id"] == "57"].set_index("year_month")
    assert cr.loc["2024-09", "event_now"] == 1 and cr.loc["2024-09", "event_lag1"] == 0
    assert cr.loc["2024-10", "event_lag1"] == 1 and cr.loc["2024-11", "event_lag1"] == 1
    assert cr.loc["2024-12", "event_lag1"] == 0
    assert "event_now" not in X.VARIANTS["C3"]["add"]
    with pytest.raises(AssertionError, match="oracle"):
        X.fit_predict(ctx, "logdiff", "2024-05", [T], ("event_now",))


def test_fit_refuses_target_months_inside_training(ctx):
    with pytest.raises(AssertionError):
        X.fit_predict(ctx, "logdiff", T, [T])


def test_ewa_weight_and_forecast_for_t_use_only_losses_before_t(ctx):
    part = ctx.table.loc[ctx.table["year_month"].between("2024-01", "2024-04")].copy()
    rng = np.random.default_rng(0)
    y = part["guest_arrivals_total"].to_numpy(dtype=float)
    experts = {"a": y * (1 + 0.1 * rng.standard_normal(len(y))), "b": y * 1.2, "c": part["guest_lag_12"].to_numpy(dtype=float)}
    pred, trace = ewa(part, experts, 10.0)
    poisoned = part.copy()
    poisoned.loc[poisoned["year_month"] >= "2024-03", "guest_arrivals_total"] = 1e12
    pred2, trace2 = ewa(poisoned, experts, 10.0)
    keep = (part["year_month"] <= "2024-03").to_numpy()
    np.testing.assert_allclose(pred[keep], pred2[keep])
    assert trace[:3] == trace2[:3]
    assert trace[3] != trace2[3]
    for t in trace:
        assert sum(v for k, v in t.items() if k.startswith("w_")) == pytest.approx(1.0)
