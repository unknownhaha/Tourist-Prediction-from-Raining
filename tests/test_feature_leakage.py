"""Leakage rules for a forecast of month t.

The allowed-lag builder may read only months strictly before t. Quarterly
guest arrivals in the clean table equal the sum of those three months, so a
quarter that contains t is not a feature of t.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.common.calendar import month_range
from src.common.paths import DATA, STAGING
from src.model.leakage import (
    assert_no_future_leakage,
    build_allowed_lag_frame,
    quarter_allowed_as_of,
    quarter_label,
    rolling_source_months,
    shift_year_month,
    source_months_for_lags,
)

CLEAN = DATA / "clean" / "mots_domestic_province"


def test_assert_no_future_leakage_rejects_same_month_and_later():
    assert_no_future_leakage(["2024-04", "2024-02", "2023-05"], "2024-05")
    with pytest.raises(AssertionError, match="2024-05"):
        assert_no_future_leakage(["2024-04", "2024-05"], "2024-05")
    with pytest.raises(AssertionError, match="2024-06"):
        assert_no_future_leakage(["2024-06"], "2024-05")


def test_allowed_lags_and_rolling_windows_stay_before_every_target_month():
    for target in month_range("2018-01", "2025-12"):
        sources = source_months_for_lags(target)
        assert sources == {
            "lag_1": shift_year_month(target, 1),
            "lag_3": shift_year_month(target, 3),
            "lag_12": shift_year_month(target, 12),
        }
        assert all(month < target for month in sources.values())
        assert_no_future_leakage(sources.values(), target)

        window = rolling_source_months(target, 3)
        assert window[-1] == shift_year_month(target, 1)
        assert target not in window
        assert all(month < target for month in window)

        containing = quarter_label(target)
        assert not quarter_allowed_as_of(containing, target)


def test_completed_quarter_is_allowed_only_after_it_ends():
    assert quarter_label("2024-05") == "2024-Q2"
    assert quarter_allowed_as_of("2024-Q1", "2024-05")
    assert not quarter_allowed_as_of("2024-Q2", "2024-04")
    assert not quarter_allowed_as_of("2024-Q2", "2024-06")
    assert quarter_allowed_as_of("2024-Q2", "2024-07")
    with pytest.raises(AssertionError):
        assert_no_future_leakage(["2024-04", "2024-05", "2024-06"], "2024-05")


def test_builder_does_not_copy_the_target_month_or_the_future():
    rows = []
    for province_id, base in (("10", 1000.0), ("50", 50.0)):
        for year_month, value in (
            ("2023-05", base),
            ("2024-02", base + 3),
            ("2024-04", base + 1),
            ("2024-05", 999_999.0),
            ("2024-06", 888_888.0),
        ):
            rows.append(
                {
                    "province_id": province_id,
                    "year_month": year_month,
                    "guest_arrivals_total": value,
                }
            )
    out = build_allowed_lag_frame(pd.DataFrame(rows), "2024-05")
    assert list(out.columns) == ["province_id", "lag_1", "lag_3", "lag_12"]
    bangkok = out.set_index("province_id").loc["10"]
    assert bangkok["lag_1"] == 1001.0
    assert bangkok["lag_3"] == 1003.0
    assert bangkok["lag_12"] == 1000.0
    values = out[["lag_1", "lag_3", "lag_12"]].to_numpy()
    assert 999_999.0 not in values
    assert 888_888.0 not in values


def test_builder_leaves_missing_history_null_and_rejects_lag_zero():
    panel = pd.DataFrame(
        {
            "province_id": ["10"],
            "year_month": ["2018-01"],
            "guest_arrivals_total": [5.0],
        }
    )
    out = build_allowed_lag_frame(panel, "2018-01")
    assert out["lag_1"].isna().all()
    assert out["lag_3"].isna().all()
    assert out["lag_12"].isna().all()
    with pytest.raises(ValueError):
        shift_year_month("2018-01", 0)


def test_builder_rejects_duplicate_keys():
    panel = pd.DataFrame(
        {
            "province_id": ["10", "10"],
            "year_month": ["2024-04", "2024-04"],
            "guest_arrivals_total": [1.0, 2.0],
        }
    )
    with pytest.raises(ValueError, match="duplicate"):
        build_allowed_lag_frame(panel, "2024-05")


def _pad(series: pd.Series) -> pd.Series:
    return series.map(lambda value: f"{int(value):02d}")


def test_clean_quarter_guest_arrivals_equal_sum_of_three_months():
    quarter = pd.read_parquet(CLEAN / "province_quarter.parquet")
    month = pd.read_parquet(CLEAN / "province_month.parquet")
    quarter = quarter.copy()
    month = month.copy()
    quarter["province_id"] = _pad(quarter["province_id"])
    month["province_id"] = _pad(month["province_id"])
    quarter["qtr"] = quarter["year_ce"].astype(int).astype(str) + "-Q" + quarter["quarter"].astype(int).astype(str)
    month["qtr"] = month["year_ce"].astype(int).astype(str) + "-Q" + month["quarter"].astype(int).astype(str)
    assert month.groupby(["province_id", "qtr"]).size().eq(3).all()

    quoted = quarter.set_index(["province_id", "qtr"])
    for column in ("guest_arrivals_total", "guest_arrivals_thai", "guest_arrivals_foreign"):
        summed = month.groupby(["province_id", "qtr"])[column].sum()
        both = quoted[column].to_frame("quoted").join(summed.rename("summed"), how="left")
        assert both["summed"].notna().all()
        assert (both["quoted"] == both["summed"]).all()

    rain = month.groupby(["province_id", "qtr"])["rain_avg_mm"].sum()
    both_rain = quoted["rain_sum_mm"].to_frame("quoted").join(rain.rename("summed"), how="left")
    assert (both_rain["quoted"] == both_rain["summed"]).all()


def test_staging_quarter_guest_arrivals_equal_sum_of_three_months():
    path = STAGING / "mots" / "fact_tourism_long.parquet"
    staged = pd.read_parquet(path)
    rows = staged[
        (staged["layout"] == "quarterly_domestic")
        & (staged["metric"] == "guest_arrivals")
        & (staged["area_level"] == "province")
    ].copy()
    monthly = rows[rows["year_month"].notna()].copy()
    quarterly = rows[rows["year_month"].isna() & rows["quarter"].notna()].copy()
    monthly["quarter"] = (
        monthly["year_ce"].astype(int).astype(str)
        + "-Q"
        + (((monthly["month"] - 1) // 3 + 1).astype(int)).astype(str)
    )
    keys = ["source_file", "sheet", "year_ce", "tourist_type", "quarter"]
    summed = monthly.groupby(keys, dropna=False)["value"].sum()
    quoted = quarterly.groupby(keys, dropna=False)["value"].first()
    both = pd.concat([quoted.rename("quoted"), summed.rename("summed")], axis=1).dropna()
    assert len(both) == len(quoted) == len(summed)
    assert (both["quoted"] == both["summed"]).all()
    exposed = monthly.merge(quarterly[keys].drop_duplicates(), on=keys, how="inner")
    assert len(exposed) == len(monthly)
