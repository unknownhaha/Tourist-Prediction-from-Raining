"""DQ accuracy v2: seasonal rain rule, log-change guest rule, COVID buckets."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from experiments.new_features.dq import _accuracy_buckets, _flags_v1, _flags_v2, assess_feature_inputs, load_config
from experiments.new_features.features import build_new_features
from experiments.new_features.run import _load

WINDOW = ("2020-03", "2022-12")


def _synthetic(months: list[str]) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for index, month in enumerate(months):
        cal = int(month[-2:])
        dry = cal in (1, 2, 12)
        rows.append({
            "province_id": "10",
            "year_month": month,
            "guest_arrivals_total": 1000.0 * (1 + 0.02 * rng.standard_normal()),
            "guest_arrivals_thai": 700.0 * (1 + 0.02 * rng.standard_normal()),
            "guest_arrivals_foreign": 300.0 * (1 + 0.02 * rng.standard_normal()),
            # Dry-month rain is very variable from year to year, as in the real panel.
            "rain_avg_mm": (10.0 if dry else 200.0) * float(rng.lognormal(0.0, 0.8 if dry else 0.3)),
        })
    return pd.DataFrame(rows)


def _months(start: str, end: str) -> list[str]:
    return list(pd.period_range(start, end, freq="M").strftime("%Y-%m"))


def test_doubled_dry_month_rain_is_not_flagged_but_a_guest_spike_is():
    months = _months("2010-01", "2019-12")
    work = _synthetic(months)
    jan = work["year_month"] == "2019-01"
    work.loc[jan, "rain_avg_mm"] = work.loc[work["year_month"] == "2018-01", "rain_avg_mm"].iloc[0] * 2.0
    work.loc[work["year_month"] == "2019-06", "guest_arrivals_total"] *= 10.0
    flags = _flags_v2(work, {"rain_z_limit": 3.5, "guest_log_jump_limit": np.log(2)}, WINDOW)
    keys = {(f["column"], f["year_month"]) for f in flags}
    assert ("rain_avg_mm", "2019-01") not in keys
    assert ("guest_arrivals_total", "2019-06") in keys
    # The legacy rule does flag the doubled dry month (|x - x12| / x12 > 1 is borderline, so use 2.5x).
    work.loc[jan, "rain_avg_mm"] = work.loc[work["year_month"] == "2018-01", "rain_avg_mm"].iloc[0] * 2.5
    legacy = {(f["column"], f["year_month"]) for f in _flags_v1(work)}
    assert ("rain_avg_mm", "2019-01") in legacy
    assert ("rain_avg_mm", "2019-01") not in {(f["column"], f["year_month"]) for f in _flags_v2(work, {}, WINDOW)}


def test_buckets_split_covid_and_covid_base_rows():
    work = pd.DataFrame({"province_id": "10", "year_month": ["2019-05", "2021-05", "2023-02", "2023-03"]})
    buckets = _accuracy_buckets(work, {("10", "2021-05")}, WINDOW)
    assert buckets["normal"]["rows"] == 1  # 2019-05; 2023-02/03 have a COVID-window base month
    assert buckets["covid_base"]["rows"] == 2
    assert buckets["covid"] == {"rows": 1, "flagged": 1, "accuracy": 0.0}


def test_real_panel_v1_reproduces_legacy_and_v2_reports_buckets():
    panel = _load()
    features = build_new_features(panel)
    config = load_config()
    v1 = assess_feature_inputs(panel, features, {**config, "accuracy_rule": "v1"})
    assert v1["scores"]["accuracy"] == pytest.approx(0.5392316017, abs=1e-10)
    v2 = assess_feature_inputs(panel, features, config)
    acc = v2["accuracy"]
    assert v2["scores"]["accuracy"] == acc["accuracy_normal"]
    assert acc["accuracy_legacy_v1"] == pytest.approx(0.5392316017, abs=1e-10)
    assert acc["flags_by_check"]["rain_seasonal_z|rain_avg_mm"] == 114
    assert acc["accuracy_covid"] is not None and acc["accuracy_covid_base"] is not None
    assert v2["scores"]["overall"] >= 0.98
    assert not v2["hard_fail"]
    duplicate = pd.concat([panel, panel.iloc[[0]]], ignore_index=True)
    from experiments.new_features.dq import DataQualityError
    with pytest.raises(DataQualityError, match="uniqueness"):
        assess_feature_inputs(duplicate, features, config)
