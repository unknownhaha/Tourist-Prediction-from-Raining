"""Build the unified province x month modeling table.

Source of truth for 2019-01..2025-12: data/clean/mots_domestic_province/province_month
2018-01..2018-12 guest arrivals (total only): data/core/province_month.parquet
Rain for every month: data/staging/hii/fact_rainfall.parquet (verified against
clean for 2019+ and against core for 2018).

The training target is guest_arrivals_total. visitors_total is not included.
For 2018, guest_arrivals_thai and guest_arrivals_foreign are null because the
core extract has only the total.

Output: data/model/province_month_unified.parquet and .csv

Run from the project root (Windows or POSIX):

    python scripts/build_unified_province_month.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.config_loader import load_sources  # noqa: E402
from src.common.paths import CORE, DATA, STAGING  # noqa: E402

CLEAN_MONTH = DATA / "clean" / "mots_domestic_province" / "province_month.parquet"
CORE_MONTH = CORE / "province_month.parquet"
STAGING_RAIN = STAGING / "hii" / "fact_rainfall.parquet"
DIM_PROVINCE = CORE / "dim_province.parquet"
OUT_DIR = DATA / "model"
OUT_PARQUET = OUT_DIR / "province_month_unified.parquet"
OUT_CSV = OUT_DIR / "province_month_unified.csv"

UNIFIED_COLS = [
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

N_PROVINCES = 77
HISTORY_START = "2018-01"
HISTORY_END = "2018-12"


def _pad_province_id(series: pd.Series) -> pd.Series:
    return series.map(lambda v: f"{int(v):02d}")


def _covid_flag(year_month: pd.Series, start: str, end: str) -> pd.Series:
    return (year_month >= start) & (year_month <= end)


def _prepare_rain(raw: pd.DataFrame) -> pd.DataFrame:
    rain = raw.copy()
    rain["province_id"] = _pad_province_id(rain["province_id"])
    keep = ["province_id", "year_month", "avg_rain_mm", "min_rain_mm", "max_rain_mm"]
    missing = [c for c in keep if c not in rain.columns]
    if missing:
        raise SystemExit(f"staging rainfall missing columns: {missing}")
    rain = rain[keep].rename(
        columns={
            "avg_rain_mm": "rain_avg_mm",
            "min_rain_mm": "rain_min_mm",
            "max_rain_mm": "rain_max_mm",
        }
    )
    dups = int(rain.duplicated(["province_id", "year_month"]).sum())
    if dups:
        raise SystemExit(f"staging rainfall has {dups} duplicate province-month keys")
    return rain


def _prepare_dim(raw: pd.DataFrame) -> pd.DataFrame:
    dim = raw.copy()
    dim["province_id"] = _pad_province_id(dim["province_id"])
    return dim[["province_id", "province_th", "province_en", "region"]].drop_duplicates(
        subset=["province_id"]
    )


def _assert_exact(left: pd.Series, right: pd.Series, label: str) -> None:
    if len(left) != len(right):
        raise SystemExit(f"{label}: length mismatch {len(left)} vs {len(right)}")
    if not left.equals(right) and not (left.fillna(-1).eq(right.fillna(-1)).all() and left.isna().equals(right.isna())):
        # Use a strict numeric compare when both are numeric and non-null
        if pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right):
            if left.isna().equals(right.isna()) and left.notna().any():
                ok = (left.isna() | (left.to_numpy() == right.to_numpy())).all()
                if ok:
                    return
        diff = (left.fillna(0) - right.fillna(0)).abs()
        n = int((~left.eq(right) & ~(left.isna() & right.isna())).sum())
        raise SystemExit(f"{label}: {n} values differ (max abs {float(diff.max())})")


def build() -> pd.DataFrame:
    for path, label in (
        (CLEAN_MONTH, "clean province_month"),
        (CORE_MONTH, "core province_month"),
        (STAGING_RAIN, "staging HII rainfall"),
        (DIM_PROVINCE, "dim_province"),
    ):
        if not path.exists():
            raise SystemExit(f"missing {label}: {path.relative_to(ROOT)}")

    cfg = load_sources()
    window = cfg["date_window"]
    covid = cfg["covid_window"]
    if window["start"] != HISTORY_START:
        raise SystemExit(
            f"date_window.start is {window['start']!r}; this builder expects {HISTORY_START}"
        )
    if window["end"] < HISTORY_END:
        raise SystemExit(f"date_window.end {window['end']!r} is before {HISTORY_END}")

    clean = pd.read_parquet(CLEAN_MONTH)
    core = pd.read_parquet(CORE_MONTH)
    rain = _prepare_rain(pd.read_parquet(STAGING_RAIN))
    dim = _prepare_dim(pd.read_parquet(DIM_PROVINCE))

    # --- 2019+ from the clean table -------------------------------------------------------
    later = clean.copy()
    later["province_id"] = _pad_province_id(later["province_id"])
    later = later.rename(columns={"year_ce": "year"})
    later = later.merge(
        rain,
        on=["province_id", "year_month"],
        how="left",
        suffixes=("_clean", ""),
        validate="one_to_one",
    )
    for col in ("rain_avg_mm", "rain_min_mm", "rain_max_mm"):
        _assert_exact(later[f"{col}_clean"], later[col], f"2019+ {col} vs staging HII")
        later = later.drop(columns=[f"{col}_clean"])
    later["is_covid"] = _covid_flag(later["year_month"], covid["start"], covid["end"])
    if "is_covid" in clean.columns:
        # Recompute from config; confirm it still matches the clean flag.
        _assert_exact(
            later["is_covid"].astype(bool),
            clean["is_covid"].astype(bool),
            "2019+ is_covid vs clean",
        )
    later = later.merge(
        dim[["province_id", "province_en", "region"]].rename(
            columns={"province_en": "en_dim", "region": "region_dim"}
        ),
        on="province_id",
        how="left",
        validate="many_to_one",
    )
    if "province_en" in later.columns:
        _assert_exact(later["province_en"], later["en_dim"], "province_en vs dim_province")
    else:
        later["province_en"] = later["en_dim"]
    if "region" in later.columns:
        _assert_exact(later["region"], later["region_dim"], "region vs dim_province")
    else:
        later["region"] = later["region_dim"]
    later = later.drop(columns=["en_dim", "region_dim"])

    # --- 2018 from core (total only) ------------------------------------------------------
    hist = core[(core["year_month"] >= HISTORY_START) & (core["year_month"] <= HISTORY_END)].copy()
    if hist.empty:
        raise SystemExit("core province_month has no 2018-01..2018-12 rows to prepend")
    hist["province_id"] = _pad_province_id(hist["province_id"])
    hist = hist.rename(
        columns={
            "year_ce": "year",
            "guest_arrivals": "guest_arrivals_total",
        }
    )
    hist["guest_arrivals_thai"] = pd.NA
    hist["guest_arrivals_foreign"] = pd.NA
    hist = hist.merge(rain, on=["province_id", "year_month"], how="left", suffixes=("_core", ""), validate="one_to_one")
    # Core uses min/max/avg_rain_mm; compare those to staging-derived rain_* columns.
    rename_core_rain = {
        "avg_rain_mm": "rain_avg_mm",
        "min_rain_mm": "rain_min_mm",
        "max_rain_mm": "rain_max_mm",
    }
    for core_col, uni_col in rename_core_rain.items():
        if core_col in hist.columns:
            _assert_exact(hist[core_col], hist[uni_col], f"2018 {uni_col} vs staging HII/core")
    hist = hist.drop(columns=[c for c in ("avg_rain_mm", "min_rain_mm", "max_rain_mm") if c in hist.columns])
    hist = hist.merge(dim, on="province_id", how="left", suffixes=("", "_dim"), validate="many_to_one")
    if "province_th_dim" in hist.columns:
        _assert_exact(hist["province_th"], hist["province_th_dim"], "2018 province_th vs dim")
        hist = hist.drop(columns=["province_th_dim"])
    hist["province_en"] = hist["province_en"] if "province_en" in hist.columns else hist["province_en_dim"]
    hist["region"] = hist["region"] if "region" in hist.columns else hist.get("region_dim")
    hist = hist.drop(columns=[c for c in hist.columns if c.endswith("_dim")])
    hist["is_covid"] = _covid_flag(hist["year_month"], covid["start"], covid["end"])

    unified = pd.concat([hist, later], ignore_index=True, sort=False)
    unified["province_id"] = _pad_province_id(unified["province_id"])
    unified["year"] = unified["year"].astype(int)
    unified["month"] = unified["month"].astype(int)
    unified["is_covid"] = unified["is_covid"].astype(bool)
    for col in ("guest_arrivals_total", "guest_arrivals_thai", "guest_arrivals_foreign",
                "rain_avg_mm", "rain_min_mm", "rain_max_mm"):
        unified[col] = pd.to_numeric(unified[col], errors="raise")

    # Restrict to the configured date window (official 2018-01..2025-12).
    unified = unified[
        (unified["year_month"] >= window["start"]) & (unified["year_month"] <= window["end"])
    ].copy()
    unified = unified[UNIFIED_COLS].sort_values(["province_id", "year_month"]).reset_index(drop=True)

    _validate(unified, clean, core, rain, window, covid)
    return unified


def _validate(
    unified: pd.DataFrame,
    clean: pd.DataFrame,
    core: pd.DataFrame,
    rain: pd.DataFrame,
    window: dict,
    covid: dict,
) -> None:
    months = pd.period_range(window["start"], window["end"], freq="M").astype(str).tolist()
    n_expected = N_PROVINCES * len(months)
    if len(unified) != n_expected:
        raise SystemExit(
            f"expected {N_PROVINCES} x {len(months)} = {n_expected} rows, got {len(unified)}"
        )
    if unified.duplicated(["province_id", "year_month"]).any():
        raise SystemExit("duplicate province_id + year_month keys")
    if unified["province_id"].nunique() != N_PROVINCES:
        raise SystemExit(f"expected {N_PROVINCES} provinces, got {unified['province_id'].nunique()}")
    if sorted(unified["year_month"].unique()) != months:
        raise SystemExit("year_month coverage does not match the date window")
    counts = unified.groupby("province_id")["year_month"].nunique()
    if not counts.eq(len(months)).all():
        raise SystemExit("panel is not balanced")
    if not unified["province_id"].str.fullmatch(r"\d{2}").all():
        raise SystemExit("province_id must be a 2-digit zero-padded string")

    target_rain = ["guest_arrivals_total", "rain_avg_mm", "rain_min_mm", "rain_max_mm"]
    nulls = unified[target_rain].isna().sum()
    if int(nulls.sum()) != 0:
        raise SystemExit(f"nulls in target/rain columns: {nulls.to_dict()}")

    expected_covid = _covid_flag(unified["year_month"], covid["start"], covid["end"])
    if not unified["is_covid"].eq(expected_covid).all():
        raise SystemExit("is_covid does not match covid_window")

    # 2019+ equals clean exactly.
    clean_k = clean.copy()
    clean_k["province_id"] = _pad_province_id(clean_k["province_id"])
    later = unified[unified["year_month"] >= "2019-01"]
    merged = later.merge(
        clean_k,
        on=["province_id", "year_month"],
        how="inner",
        suffixes=("", "_clean"),
        validate="one_to_one",
    )
    if len(merged) != len(later) or len(merged) != len(clean_k):
        raise SystemExit(
            f"2019+ join size {len(merged)} vs unified {len(later)} vs clean {len(clean_k)}"
        )
    for left, right in (
        ("guest_arrivals_total", "guest_arrivals_total_clean"),
        ("guest_arrivals_thai", "guest_arrivals_thai_clean"),
        ("guest_arrivals_foreign", "guest_arrivals_foreign_clean"),
        ("rain_avg_mm", "rain_avg_mm_clean"),
        ("rain_min_mm", "rain_min_mm_clean"),
        ("rain_max_mm", "rain_max_mm_clean"),
        ("province_th", "province_th_clean"),
    ):
        _assert_exact(merged[left], merged[right], f"2019+ {left} vs clean")

    # 2018 equals core exactly (total only).
    core_k = core.copy()
    core_k["province_id"] = _pad_province_id(core_k["province_id"])
    hist = unified[unified["year_month"] <= HISTORY_END]
    core_2018 = core_k[(core_k["year_month"] >= HISTORY_START) & (core_k["year_month"] <= HISTORY_END)]
    merged_h = hist.merge(
        core_2018,
        on=["province_id", "year_month"],
        how="inner",
        suffixes=("", "_core"),
        validate="one_to_one",
    )
    if len(merged_h) != len(hist) or len(merged_h) != len(core_2018):
        raise SystemExit(
            f"2018 join size {len(merged_h)} vs unified {len(hist)} vs core {len(core_2018)}"
        )
    _assert_exact(merged_h["guest_arrivals_total"], merged_h["guest_arrivals"], "2018 guest_arrivals vs core")
    if not hist["guest_arrivals_thai"].isna().all() or not hist["guest_arrivals_foreign"].isna().all():
        raise SystemExit("2018 thai/foreign must be null (core has total only)")

    # Rain equals staging for every unified row.
    merged_r = unified.merge(
        rain,
        on=["province_id", "year_month"],
        how="left",
        suffixes=("", "_stg"),
        validate="one_to_one",
    )
    if merged_r[["rain_avg_mm_stg", "rain_min_mm_stg", "rain_max_mm_stg"]].isna().any().any():
        raise SystemExit("staging rainfall is missing some unified province-months")
    for col in ("rain_avg_mm", "rain_min_mm", "rain_max_mm"):
        _assert_exact(merged_r[col], merged_r[f"{col}_stg"], f"{col} vs staging HII")


def main() -> int:
    unified = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    unified.to_parquet(OUT_PARQUET, index=False)
    unified.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    n_prov = unified["province_id"].nunique()
    n_months = unified["year_month"].nunique()
    print(
        f"Wrote {len(unified):,} rows ({n_prov} provinces x {n_months} months) to "
        f"{OUT_PARQUET.relative_to(ROOT)} and {OUT_CSV.relative_to(ROOT)}"
    )
    n_2018_null = int(
        unified.loc[
            unified["year_month"] <= HISTORY_END, ["guest_arrivals_thai", "guest_arrivals_foreign"]
        ]
        .isna()
        .all(axis=None)
    )
    print(
        "Checks passed: balanced panel, unique keys, no nulls in target/rain, "
        "2019+ equals clean, 2018 total equals core, rain matches staging HII."
    )
    if n_2018_null:
        print("Note: 2018 guest_arrivals_thai and guest_arrivals_foreign are null.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
