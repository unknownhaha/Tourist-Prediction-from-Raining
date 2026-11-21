"""The 2023/2024 backtest cannot see the month it forecasts or anything later."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from experiments.backtest.run import build_table, predict, training_window
from experiments.beat_seasonal_naive.run import _rain_history
from experiments.new_features.run import LEVEL, LOGDIFF, _load
from src.common.paths import DATA

pytestmark = pytest.mark.skipif(
    not (DATA / "model" / "province_month_unified.parquet").exists(), reason="modeling table not built"
)


def _small(spec):
    params = dict(spec.params)
    params["n_estimators"] = 20
    return replace(spec, params=tuple(sorted(params.items())))


@pytest.fixture(scope="module")
def tables():
    panel = _load()
    clean = build_table(panel)
    poisoned_panel = panel.copy()
    later = poisoned_panel["year_month"] >= "2023-01"
    for column in ("guest_arrivals_total", "guest_arrivals_thai", "guest_arrivals_foreign", "rain_avg_mm"):
        poisoned_panel.loc[later, column] = poisoned_panel.loc[later, column] * 7.0 + 13.0
    poisoned = build_table(poisoned_panel)
    return (clean, _rain_history(panel)), (poisoned, _rain_history(poisoned_panel))


def test_a_target_month_must_be_after_the_training_cutoff(tables):
    (table, history), _ = tables
    with pytest.raises(AssertionError):
        predict("rf_logdiff", table, history, "2023-01", ["2023-01"])
    assert training_window(table, "2022-12")["year_month"].max() == "2022-12"


@pytest.mark.parametrize("model,spec", [("rf_logdiff", LOGDIFF), ("rf_level", LEVEL), ("rf_logdiff_yoy3", LOGDIFF)])
def test_frozen_2023_never_reads_a_2023_label(tables, model, spec):
    (clean, history), _ = tables
    months = [f"2023-{m:02d}" for m in range(1, 13)]
    hidden = clean.copy()
    hidden.loc[hidden["year_month"] >= "2023-01", "guest_arrivals_total"] = np.nan
    a = predict(model, clean, history, "2022-12", months, _small(spec))
    b = predict(model, hidden, history, "2022-12", months, _small(spec))
    np.testing.assert_allclose(a, b)
    assert len(a) == 12 * 77


@pytest.mark.parametrize("model,spec", [("rf_logdiff", LOGDIFF), ("rf_level", LEVEL), ("rf_logdiff_yoy3", LOGDIFF)])
def test_monthly_refit_ignores_the_target_month_and_later(tables, model, spec):
    (clean, h_clean), (poisoned, h_poisoned) = tables
    # Poison starts at 2023-01, so forecasting 2023-01 with data through 2022-12
    # must match; forecasting 2023-02 must differ because 2023-01 is now history.
    a = predict(model, clean, h_clean, "2022-12", ["2023-01"], _small(spec))
    b = predict(model, poisoned, h_poisoned, "2022-12", ["2023-01"], _small(spec))
    np.testing.assert_allclose(a, b)
    c = predict(model, clean, h_clean, "2023-01", ["2023-02"], _small(spec))
    d = predict(model, poisoned, h_poisoned, "2023-01", ["2023-02"], _small(spec))
    assert not np.allclose(c, d)
