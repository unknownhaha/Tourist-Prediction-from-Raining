"""Tests for data/clean/mots_domestic_province (MOTS tourism + HII rain, province level only).

Build first:  python scripts/clean_domestic_province.py
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.common.paths import CORE, DATA, STAGING

OUT = DATA / "clean" / "mots_domestic_province"
N_PROVINCES = 77
N_QUARTERS = 28  # 2019-Q1 .. 2025-Q4
N_MONTHS = 84  # 2019-01 .. 2025-12

# Names that only exist below province level. None of them may appear in the province tables.
SUB_AREA_WORDS = ["พัทยา", "บางแสน", "หัวหิน", "ชะอำ", "ปาย", "ปากช่อง", "วังน้ำเขียว", "เกาะ",
                  "เมือง", "อำเภอ", "รวม", "Pattaya", "Hua Hin", "Samui", "Pai"]


@pytest.fixture(scope="module")
def quarter() -> pd.DataFrame:
    path = OUT / "province_quarter.parquet"
    if not path.exists():
        pytest.skip("run python scripts/clean_domestic_province.py first")
    return pd.read_parquet(path)


@pytest.fixture(scope="module")
def month() -> pd.DataFrame:
    return pd.read_parquet(OUT / "province_month.parquet")


@pytest.fixture(scope="module")
def sheets() -> pd.DataFrame:
    return pd.read_parquet(OUT / "tourism_sheets_long.parquet")


@pytest.fixture(scope="module")
def dim() -> pd.DataFrame:
    d = pd.read_parquet(CORE / "dim_province.parquet")
    return d.assign(province_id=d.province_id.astype(int))


# --- one row per province per period --------------------------------------------------------
def test_quarter_one_row_per_province_quarter(quarter):
    assert not quarter.duplicated(["province_id", "year_ce", "quarter"]).any()
    assert len(quarter) == N_PROVINCES * N_QUARTERS


def test_month_one_row_per_province_month(month):
    assert not month.duplicated(["province_id", "year_month"]).any()
    assert len(month) == N_PROVINCES * N_MONTHS


@pytest.mark.parametrize("table", ["quarter", "month"])
def test_province_names_are_one_to_one_with_ids(table, request):
    df = request.getfixturevalue(table)
    pairs = df[["province_id", "province_th", "province_en"]].drop_duplicates()
    assert len(pairs) == N_PROVINCES
    assert pairs.province_id.is_unique and pairs.province_th.is_unique and pairs.province_en.is_unique


# --- only real provinces, no sub-areas ------------------------------------------------------
@pytest.mark.parametrize("table", ["quarter", "month"])
def test_exactly_the_77_provinces(table, request, dim):
    df = request.getfixturevalue(table)
    assert set(df.province_id) == set(dim.province_id)
    assert set(df.province_th) == set(dim.province_th)


@pytest.mark.parametrize("table", ["quarter", "month"])
def test_no_sub_area_names_or_columns(table, request):
    df = request.getfixturevalue(table)
    assert not {"sheet", "sheet_role", "area_level"} & set(df.columns)
    names = pd.concat([df.province_th, df.province_en]).unique()
    hits = [n for n in names for w in SUB_AREA_WORDS if w in n]
    # 'เมือง' and 'Pai' are substrings of no Thai/English province name; any hit is a sub-area leak
    assert hits == [], f"sub-area names found: {hits}"


def test_every_sub_area_sheet_has_a_parent_province(sheets, dim):
    sub = sheets[sheets.sheet_role.isin(["sub_area", "subtotal"])]
    assert not sub.empty
    assert sub.province_id.notna().all()
    assert set(sub.province_id.astype(int)) <= set(dim.province_id)


def test_bangsaen_and_pattaya_belong_to_chonburi(sheets):
    parents = sheets[sheets.sheet.str.contains("บางแสน|พัทยา")].province_th.unique().tolist()
    assert parents == ["ชลบุรี"]


def test_sub_areas_add_up_to_their_province():
    chk = pd.read_csv(OUT / "subarea_rollup_check.csv", encoding="utf-8-sig")
    assert chk.value_province.notna().all(), "a province with sub-areas has no province total"
    tol = (chk.value_province.abs() * 1e-6).clip(lower=0.01)
    bad = chk[chk["diff"].abs() > tol]
    assert bad.empty, bad[["province_th", "year_ce", "quarter", "metric", "tourist_type", "diff"]].head()


def test_province_values_include_their_sub_areas(quarter, sheets):
    # Chonburi visitors = Pattaya + Bang Saen, every quarter
    v = sheets[(sheets.metric == "visitors") & (sheets.tourist_type == "total") & (sheets.province_th == "ชลบุรี")]
    parts = v[v.sheet_role == "sub_area"].groupby(["year_ce", "quarter"]).value.sum()
    prov = quarter[quarter.province_th == "ชลบุรี"].set_index(["year_ce", "quarter"]).visitors_total
    pd.testing.assert_series_equal(parts.sort_index(), prov.sort_index(), check_names=False)


# --- rainfall at province level and matched ---------------------------------------------------
def test_rain_source_is_one_row_per_province_month():
    r = pd.read_parquet(STAGING / "hii" / "fact_rainfall.parquet")
    assert not r.duplicated(["province_id", "year_month"]).any()
    assert r.province_id.nunique() == N_PROVINCES


def test_every_row_has_tourism_and_rain(quarter, month):
    q_cols = ["visitors_total", "guest_arrivals_total", "rain_sum_mm", "rain_mean_monthly_mm"]
    assert quarter[q_cols].notna().all().all()
    assert (quarter.rain_months == 3).all()
    assert month[["guest_arrivals_total", "rain_avg_mm"]].notna().all().all()


def test_rain_matches_source_values(month):
    r = pd.read_parquet(STAGING / "hii" / "fact_rainfall.parquet").assign(
        province_id=lambda d: d.province_id.astype(int))
    m = month.merge(r[["province_id", "year_month", "avg_rain_mm"]], on=["province_id", "year_month"])
    assert len(m) == len(month)
    assert (m.rain_avg_mm - m.avg_rain_mm).abs().max() < 1e-9


def test_quarter_rain_is_sum_of_months(quarter, month):
    s = month.groupby(["province_id", "year_ce", "quarter"]).rain_avg_mm.sum()
    q = quarter.set_index(["province_id", "year_ce", "quarter"]).rain_sum_mm
    assert (s.reindex(q.index) - q).abs().max() < 1e-9


def test_monthly_guests_sum_to_quarter(quarter, month):
    s = month.groupby(["province_id", "year_ce", "quarter"]).guest_arrivals_total.sum()
    q = quarter.set_index(["province_id", "year_ce", "quarter"]).guest_arrivals_total
    assert (s.reindex(q.index) - q).abs().max() <= 1


# --- COVID flag ------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def covid():
    from src.common.config_loader import load_sources

    return load_sources()["covid_window"]


def test_covid_rows_are_kept_and_flagged(month, quarter):
    assert len(month) == N_PROVINCES * N_MONTHS and len(quarter) == N_PROVINCES * N_QUARTERS
    for df in (month, quarter):
        assert df.is_covid.dtype == bool
        assert set(df.is_covid.unique()) == {True, False}


def test_month_is_covid_matches_window(month, covid):
    expected = month.year_month.between(covid["start"], covid["end"])
    assert (month.is_covid == expected).all()
    assert month.groupby("province_id").is_covid.sum().eq(34).all()  # 2020-03..2022-12


def test_quarter_is_covid_when_any_month_in_window(quarter, covid):
    first = quarter.year_ce.astype(str) + "-" + ((quarter.quarter - 1) * 3 + 1).map("{:02d}".format)
    last = quarter.year_ce.astype(str) + "-" + (quarter.quarter * 3).map("{:02d}".format)
    assert (quarter.is_covid == ((last >= covid["start"]) & (first <= covid["end"]))).all()
    assert quarter.groupby("province_id").is_covid.sum().eq(12).all()  # 2020-Q1..2022-Q4


def test_month_flag_agrees_with_core_province_month(month):
    core = pd.read_parquet(CORE / "province_month.parquet").assign(
        province_id=lambda d: d.province_id.astype(int))
    m = month.merge(core[["province_id", "year_month", "is_covid"]], on=["province_id", "year_month"],
                    suffixes=("", "_core"))
    assert len(m) > 0 and (m.is_covid == m.is_covid_core.astype(bool)).all()
