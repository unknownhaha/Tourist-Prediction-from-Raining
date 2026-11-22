"""The 2024-2025 level walk reads only months before the forecast month."""

import numpy as np

from experiments.online_replay.level_walk import FEATURES, LevelWalk


def test_future_arrivals_and_rain_do_not_change_the_forecast_row():
    walk = LevelWalk()
    before = walk.month_rows("2024-06").loc[:, list(FEATURES)].to_numpy(dtype=float)
    poisoned = walk.panel.copy()
    poisoned.loc[poisoned["year_month"] >= "2024-06", "guest_arrivals_total"] = 1.0e12
    poisoned.loc[poisoned["year_month"] >= "2024-06", "rain_avg_mm"] = 1.0e6
    after = LevelWalk(poisoned).month_rows("2024-06").loc[:, list(FEATURES)].to_numpy(dtype=float)
    np.testing.assert_allclose(before, after)


def test_training_table_stops_at_the_requested_month():
    table = LevelWalk().table_through("2023-12")
    assert table["year_month"].min() == "2019-01"
    assert table["year_month"].max() == "2023-12"
    assert len(table) == 60 * 77
