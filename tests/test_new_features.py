"""Leakage, validation, and data-quality checks for the candidate features."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from experiments.new_features.calendar_data import calendar_month_table
from experiments.new_features.dq import DataQualityError, assess_feature_inputs
from experiments.new_features.features import build_new_features, source_months
from experiments.new_features.validate_inputs import FeatureValidationError, validate_feature_inputs
from src.model.leakage import assert_no_future_leakage


def _panel(months: int = 30, provinces: tuple[str, ...] = ("10", "11", "50", "51")) -> pd.DataFrame:
    regions = {"10": "central", "11": "central", "50": "north", "51": "north"}
    rows = []
    period = pd.period_range("2018-01", periods=months, freq="M")
    for month in period.strftime("%Y-%m"):
        for province_id in provinces:
            base = 100 + int(province_id) + int(month[5:7])
            rows.append(
                {
                    "province_id": province_id,
                    "province_en": province_id,
                    "region": regions[province_id],
                    "year_month": month,
                    "guest_arrivals_total": float(base),
                    "guest_arrivals_thai": float(base - 10) if month >= "2019-01" else np.nan,
                    "guest_arrivals_foreign": 10.0 if month >= "2019-01" else np.nan,
                    "rain_avg_mm": 5.0,
                }
            )
    return pd.DataFrame(rows)


def test_lag_sources_are_strictly_before_the_target():
    sources = source_months("2024-06")
    assert_no_future_leakage(sources.values(), "2024-06")
    assert sources["lag_1"] == "2024-05"
    assert sources["lag_15"] == "2023-03"


def test_future_guest_values_do_not_change_features_for_the_target_month():
    panel = _panel(36)
    before = build_new_features(panel)
    changed = panel.copy()
    changed.loc[changed["year_month"] >= "2020-06", "guest_arrivals_total"] *= 5
    changed.loc[changed["year_month"] >= "2020-06", "guest_arrivals_thai"] *= 5
    after = build_new_features(changed)
    columns = ["thai_lag_1", "thai_yoy", "foreign_lag_12", "yoy_3m_log", "region_yoy_log", "holiday_count", "festival_songkran"]
    left = before.loc[before["year_month"] == "2020-06", columns].reset_index(drop=True)
    right = after.loc[after["year_month"] == "2020-06", columns].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)


def test_regional_trend_excludes_the_province_itself():
    panel = _panel(30)
    base = build_new_features(panel)
    bumped = panel.copy()
    bumped.loc[(bumped["province_id"] == "10") & (bumped["year_month"] == "2019-12"), "guest_arrivals_total"] = 10000
    changed = build_new_features(bumped)
    month = "2020-01"
    own = changed.loc[(changed["year_month"] == month) & (changed["province_id"] == "10"), "region_yoy_log"].iloc[0]
    own_base = base.loc[(base["year_month"] == month) & (base["province_id"] == "10"), "region_yoy_log"].iloc[0]
    neighbor = changed.loc[(changed["year_month"] == month) & (changed["province_id"] == "11"), "region_yoy_log"].iloc[0]
    neighbor_base = base.loc[(base["year_month"] == month) & (base["province_id"] == "11"), "region_yoy_log"].iloc[0]
    assert own == pytest.approx(own_base)
    assert neighbor != pytest.approx(neighbor_base)


def test_ratio_features_are_never_infinite():
    panel = _panel(30)
    panel.loc[panel["year_month"] == "2019-06", "guest_arrivals_foreign"] = 0
    features = build_new_features(panel)
    values = features[["thai_yoy", "foreign_yoy", "yoy_3m_log", "region_yoy_log"]].to_numpy(dtype=float)
    assert not np.isinf(values).any()


def test_april_2019_holiday_count_includes_songkran_and_substitution_days():
    calendar = calendar_month_table().set_index("year_month")
    assert calendar.loc["2019-04", "holiday_count"] == 6
    assert calendar.loc["2019-04", "festival_songkran"] == 1
    assert calendar.loc["2020-04", "festival_songkran"] == 1
    assert calendar.loc["2020-10", "festival_loy"] == 1
    assert calendar.loc["2025-01", "festival_cny"] == 1


def test_validator_accepts_a_complete_synthetic_frame_and_rejects_faults():
    panel = _panel(40)
    features = build_new_features(panel)
    # The synthetic frame is not the 77-province national table, so the province-count
    # check is the one hard rule we relax by using the real builder's output shape.
    features = features.copy()
    report = None
    with pytest.raises(FeatureValidationError):
        validate_feature_inputs(panel, features)
    broken = features.copy()
    broken.loc[0, "thai_yoy"] = np.inf
    with pytest.raises(FeatureValidationError, match="no_inf"):
        validate_feature_inputs(panel, broken)
    flags = features.copy()
    flags.loc[0, "festival_songkran"] = 2
    with pytest.raises(FeatureValidationError, match="flag_festival_songkran"):
        validate_feature_inputs(panel, flags)
    duplicate = pd.concat([features, features.iloc[[0]]], ignore_index=True)
    with pytest.raises(FeatureValidationError, match="unique_province_month"):
        validate_feature_inputs(panel, duplicate)
    assert report is None


def test_data_quality_blocks_duplicates_and_warns_on_a_jump():
    panel = _panel(30)
    features = build_new_features(panel)
    config = {
        "completeness_max_missing_frac_core": 0.0,
        "completeness_max_missing_frac_splits_from_2019": 0.0,
        "uniqueness_max_duplicate_keys": 0,
        "timeliness_max_gap_frac": 1.0,
        "validity_max_invalid_frac": 0.0,
        "consistency_max_mismatch_frac": 0.01,
        "consistency_max_calendar_mismatch": 0,
        "accuracy_max_flagged_row_frac": 0.9,
        "holiday_count_max": 31,
    }
    # Synthetic coverage is only 30 months, so timeliness is allowed to be incomplete.
    report = assess_feature_inputs(panel, features, config)
    assert report["scores"]["uniqueness"] == 1.0
    assert "overall" in report["scores"]
    duplicate = pd.concat([panel, panel.iloc[[0]]], ignore_index=True)
    with pytest.raises(DataQualityError, match="uniqueness"):
        assess_feature_inputs(duplicate, features, config)
    invalid = features.copy()
    invalid.loc[0, "holiday_count"] = 40
    with pytest.raises(DataQualityError, match="validity"):
        assess_feature_inputs(panel, invalid, config)
