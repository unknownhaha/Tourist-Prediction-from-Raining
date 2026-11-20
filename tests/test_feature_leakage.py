"""Leakage rules for a forecast of month t.

The allowed-lag builder may read only months strictly before t. Quarterly
guest arrivals in the clean table equal the sum of those three months, so a
quarter that contains t is not a feature of t.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.common.calendar import COVID_END, month_range
from src.common.paths import DATA, ROOT, STAGING
from src.model.leakage import (
    TIME_SPLIT,
    assert_no_future_leakage,
    build_allowed_lag_frame,
    build_forecast_features,
    forecast_source_months,
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
            "lag_12": shift_year_month(target, 12),
        }
        assert all(month < target for month in sources.values())
        assert_no_future_leakage(sources.values(), target)

        forecast = forecast_source_months(target)
        assert set(forecast) == {
            "guest_lag_1",
            "guest_lag_12",
            "rain_lag_1",
            "is_covid_lag12",
            "rain_roll3_m3",
            "rain_roll3_m2",
            "rain_roll3_m1",
        }
        assert all(month < target for month in forecast.values())
        assert forecast["rain_roll3_m1"] == shift_year_month(target, 1)
        assert target not in forecast.values()
        assert_no_future_leakage(forecast.values(), target)

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
    assert list(out.columns) == ["province_id", "lag_1", "lag_12"]
    bangkok = out.set_index("province_id").loc["10"]
    assert bangkok["lag_1"] == 1001.0
    assert bangkok["lag_12"] == 1000.0
    values = out[["lag_1", "lag_12"]].to_numpy()
    assert 1003.0 not in values
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


def test_forecast_features_use_only_months_before_t():
    rows = []
    for province_id in ("10", "50"):
        for year_month, guests, rain in (
            ("2024-02", 10.0, 1.0),
            ("2024-03", 20.0, 2.0),
            ("2024-04", 30.0, 3.0),
            ("2023-05", 40.0, 4.0),
            ("2024-05", 999_999.0, 999.0),
        ):
            rows.append(
                {
                    "province_id": province_id,
                    "year_month": year_month,
                    "guest_arrivals_total": guests,
                    "rain_avg_mm": rain,
                }
            )
    out = build_forecast_features(pd.DataFrame(rows), "2024-05")
    assert list(out.columns) == [
        "province_id",
        "guest_lag_1",
        "guest_lag_12",
        "rain_lag_1",
        "rain_roll3",
        "is_covid",
        "is_covid_lag12",
    ]
    bangkok = out.set_index("province_id").loc["10"]
    assert bangkok["guest_lag_1"] == 30.0
    assert bangkok["guest_lag_12"] == 40.0
    assert bangkok["rain_lag_1"] == 3.0
    assert bangkok["rain_roll3"] == 2.0  # mean of 1, 2, 3; not the 999 at t
    assert bool(bangkok["is_covid"]) is False
    assert bool(bangkok["is_covid_lag12"]) is False
    assert 999_999.0 not in out[["guest_lag_1", "guest_lag_12"]].to_numpy()
    assert 999.0 not in out[["rain_lag_1", "rain_roll3"]].to_numpy()


def test_lags_are_computed_before_rows_are_excluded():
    """A shift after dropping COVID rows makes 2023-01 read 2020-02."""
    full = pd.DataFrame(
        {
            "province_id": ["10", "10", "10"],
            "year_month": ["2020-02", "2022-12", "2023-01"],
            "guest_arrivals_total": [5.0, 80.0, 100.0],
            "rain_avg_mm": [1.0, 2.0, 3.0],
            "is_covid": [False, True, False],
        }
    )
    built = build_forecast_features(full, "2023-01")
    assert built.loc[0, "guest_lag_1"] == 80.0
    assert bool(built.loc[0, "is_covid_lag12"]) is True

    dropped = full.loc[~full["is_covid"]].sort_values("year_month")
    shifted = dropped.groupby("province_id")["guest_arrivals_total"].shift(1)
    leaked = dropped.assign(lag_1=shifted).loc[dropped["year_month"] == "2023-01", "lag_1"].item()
    assert leaked == 5.0
    assert leaked != built.loc[0, "guest_lag_1"]


def test_time_split_boundaries_match_the_doc():
    doc = (ROOT / "docs" / "feature_rules_for_modeling.md").read_text(encoding="utf-8")
    for start, end in TIME_SPLIT.values():
        assert start in doc
        assert end in doc
    assert "build_forecast_features" in doc
    assert "is_covid_lag12" in doc

    history = month_range(*TIME_SPLIT["lag_history"])
    train = month_range(*TIME_SPLIT["train"])
    validation = month_range(*TIME_SPLIT["validation"])
    stream = month_range(*TIME_SPLIT["online_stream"])
    assert history == month_range("2018-01", "2018-12")
    assert train == month_range("2019-01", "2022-12")
    assert validation == month_range("2023-01", "2023-12")
    assert stream == month_range("2024-01", "2025-12")
    assert len(stream) == 24
    assert train[-1] == COVID_END
    assert validation[0] > COVID_END
    covered = history + train + validation + stream
    assert covered == month_range("2018-01", "2025-12")
    assert len(covered) == len(set(covered))

    covid = set(month_range("2020-03", "2022-12"))
    assert covid <= set(train)
    assert covid.isdisjoint(validation)
    assert covid.isdisjoint(history)
    for month in month_range("2019-01", "2019-12"):
        assert shift_year_month(month, 12) in history
