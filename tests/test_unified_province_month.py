"""Tests for data/model/province_month_unified (clean 2019+ plus core 2018 totals).

Build first:  python scripts/build_unified_province_month.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.common.config_loader import load_sources
from src.common.paths import CORE, DATA, STAGING

OUT = DATA / "model"
CLEAN = DATA / "clean" / "mots_domestic_province" / "province_month.parquet"
N_PROVINCES = 77
N_MONTHS = 96  # 2018-01 .. 2025-12


@pytest.fixture(scope="module")
def unified() -> pd.DataFrame:
    path = OUT / "province_month_unified.parquet"
    if not path.exists():
        pytest.skip("run python scripts/build_unified_province_month.py first")
    return pd.read_parquet(path)


@pytest.fixture(scope="module")
def clean() -> pd.DataFrame:
    return pd.read_parquet(CLEAN)


@pytest.fixture(scope="module")
def core() -> pd.DataFrame:
    return pd.read_parquet(CORE / "province_month.parquet")


@pytest.fixture(scope="module")
def staging_rain() -> pd.DataFrame:
    return pd.read_parquet(STAGING / "hii" / "fact_rainfall.parquet")


def _pad(series: pd.Series) -> pd.Series:
    return series.map(lambda v: f"{int(v):02d}")


def test_balanced_panel_and_keys(unified):
    cfg = load_sources()["date_window"]
    months = pd.period_range(cfg["start"], cfg["end"], freq="M").astype(str).tolist()
    assert cfg["start"] == "2018-01"
    assert cfg["end"] == "2025-12"
    assert len(months) == N_MONTHS
    assert len(unified) == N_PROVINCES * N_MONTHS
    assert unified["province_id"].nunique() == N_PROVINCES
    assert unified["year_month"].nunique() == N_MONTHS
    assert unified["year_month"].min() == "2018-01"
    assert unified["year_month"].max() == "2025-12"
    assert not unified.duplicated(["province_id", "year_month"]).any()
    assert unified.groupby("province_id")["year_month"].nunique().eq(N_MONTHS).all()
    assert unified["province_id"].str.fullmatch(r"\d{2}").all()
    expected = [
        "province_id",
        "province_th",
        "province_en",
        "region",
        "year_month",
        "year",
        "month",
        "guest_arrivals_total",
        "guest_arrivals_thai",
        "guest_arrivals_foreign",
        "rain_avg_mm",
        "rain_min_mm",
        "rain_max_mm",
        "is_covid",
    ]
    assert list(unified.columns) == expected
    assert "visitors_total" not in unified.columns


def test_no_nulls_in_target_or_rain(unified):
    cols = ["guest_arrivals_total", "rain_avg_mm", "rain_min_mm", "rain_max_mm"]
    assert unified[cols].notna().all().all()
    assert (unified[["rain_min_mm", "rain_avg_mm", "rain_max_mm"]] >= 0).all().all()
    assert (unified["guest_arrivals_total"] >= 0).all()


def test_2019_plus_equals_clean_exactly(unified, clean):
    later = unified[unified["year_month"] >= "2019-01"].copy()
    ref = clean.copy()
    ref["province_id"] = _pad(ref["province_id"])
    merged = later.merge(ref, on=["province_id", "year_month"], suffixes=("", "_clean"), validate="one_to_one")
    assert len(merged) == len(later) == len(ref) == N_PROVINCES * 84
    for col in (
        "guest_arrivals_total",
        "guest_arrivals_thai",
        "guest_arrivals_foreign",
        "rain_avg_mm",
        "rain_min_mm",
        "rain_max_mm",
        "province_th",
        "province_en",
        "region",
    ):
        assert (merged[col] == merged[f"{col}_clean"]).all(), col


def test_2018_equals_core_exactly_and_has_no_thai_foreign_split(unified, core):
    hist = unified[unified["year_month"].between("2018-01", "2018-12")].copy()
    ref = core[core["year_month"].between("2018-01", "2018-12")].copy()
    ref["province_id"] = _pad(ref["province_id"])
    merged = hist.merge(ref, on=["province_id", "year_month"], suffixes=("", "_core"), validate="one_to_one")
    assert len(merged) == len(hist) == len(ref) == N_PROVINCES * 12
    assert (merged["guest_arrivals_total"] == merged["guest_arrivals"]).all()
    assert hist["guest_arrivals_thai"].isna().all()
    assert hist["guest_arrivals_foreign"].isna().all()
    assert hist["province_en"].notna().all()
    assert hist["region"].notna().all()


def test_rain_matches_staging_hii(unified, staging_rain):
    rain = staging_rain.copy()
    rain["province_id"] = _pad(rain["province_id"])
    rain = rain.rename(
        columns={
            "avg_rain_mm": "rain_avg_mm",
            "min_rain_mm": "rain_min_mm",
            "max_rain_mm": "rain_max_mm",
        }
    )
    merged = unified.merge(
        rain[["province_id", "year_month", "rain_avg_mm", "rain_min_mm", "rain_max_mm"]],
        on=["province_id", "year_month"],
        suffixes=("", "_stg"),
        validate="one_to_one",
    )
    assert len(merged) == len(unified)
    for col in ("rain_avg_mm", "rain_min_mm", "rain_max_mm"):
        assert (merged[col] == merged[f"{col}_stg"]).all(), col


def test_is_covid_matches_config_window(unified):
    covid = load_sources()["covid_window"]
    expected = (unified["year_month"] >= covid["start"]) & (unified["year_month"] <= covid["end"])
    assert covid["start"] == "2020-03"
    assert covid["end"] == "2022-12"
    assert (unified["is_covid"] == expected).all()
    assert unified.groupby("province_id")["is_covid"].sum().eq(34).all()


def test_year_month_parts_and_csv_roundtrip(unified):
    assert (unified["year"] == unified["year_month"].str.slice(0, 4).astype(int)).all()
    assert (unified["month"] == unified["year_month"].str.slice(5, 7).astype(int)).all()
    csv_path = OUT / "province_month_unified.csv"
    assert csv_path.exists()
    csv = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"province_id": str}, float_precision="round_trip")
    assert len(csv) == len(unified)
    assert (csv["province_id"] == unified["province_id"]).all()
    # 2018 splits stay empty in the CSV as well.
    hist = csv[csv["year_month"].between("2018-01", "2018-12")]
    assert hist["guest_arrivals_thai"].isna().all()
    assert hist["guest_arrivals_foreign"].isna().all()
    assert np.isclose(
        csv["guest_arrivals_total"].to_numpy(),
        unified["guest_arrivals_total"].to_numpy(),
        rtol=0,
        atol=0,
    ).all()


def test_covid_zeros_are_kept(unified):
    zeros = unified[unified["guest_arrivals_total"] == 0]
    assert len(zeros) == 27
    assert zeros["is_covid"].all()
    assert set(zeros["year_month"]) <= {"2020-04", "2020-05", "2021-01"}
