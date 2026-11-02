"""Automated QA against data contracts and project rules."""

from __future__ import annotations

import sys

import pandas as pd
import yaml

from src.common.calendar import month_range
from src.common.config_loader import load_sources
from src.common.paths import CONFIG, CORE, STAGING
from src.qa.consistency import check_guest_months_sum_to_quarter, check_thai_foreign_total
from src.qa.manifest_summary import latest_manifest_df


def _load_contracts() -> dict:
    with (CONFIG / "data_contracts.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_unique(df: pd.DataFrame, keys: list[str], name: str, errors: list[str]) -> None:
    if df.empty:
        errors.append(f"{name}: empty table")
        return
    dup = df.duplicated(subset=keys, keep=False)
    if dup.any():
        errors.append(f"{name}: {dup.sum()} duplicate rows on {keys}")


def check_columns(df: pd.DataFrame, required: list[str], name: str, errors: list[str]) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        errors.append(f"{name}: missing columns {missing}")


def _invalid_province_ids(series: pd.Series) -> int:
    s = series.astype("string")
    bad = s.isna() | (s.str.strip() == "") | (s.str.lower() == "nan")
    return int(bad.sum())


def main() -> int:
    errors: list[str] = []
    contracts = _load_contracts()
    cfg = load_sources()

    dim_p = pd.read_parquet(CORE / "dim_province.parquet") if (CORE / "dim_province.parquet").exists() else pd.DataFrame()
    dim_m = pd.read_parquet(CORE / "dim_month.parquet") if (CORE / "dim_month.parquet").exists() else pd.DataFrame()
    rain = pd.read_parquet(CORE / "fact_rainfall.parquet") if (CORE / "fact_rainfall.parquet").exists() else pd.DataFrame()
    tour = (
        pd.read_parquet(CORE / "fact_tourism_province_month.parquet")
        if (CORE / "fact_tourism_province_month.parquet").exists()
        else pd.DataFrame()
    )
    pm = pd.read_parquet(CORE / "province_month.parquet") if (CORE / "province_month.parquet").exists() else pd.DataFrame()

    for name, df in [
        ("dim_province", dim_p),
        ("dim_month", dim_m),
        ("fact_rainfall", rain),
        ("fact_tourism_province_month", tour),
        ("province_month", pm),
    ]:
        if name not in contracts:
            continue
        spec = contracts[name]
        if name == "fact_tourism_province_month" and df.empty:
            errors.append(f"{name}: empty table")
            continue
        check_columns(df, spec["required_columns"], name, errors)
        check_unique(df, spec["unique"], name, errors)

    if not dim_p.empty and len(dim_p) != 77:
        errors.append(f"dim_province: expected 77 rows, got {len(dim_p)}")

    if not dim_m.empty:
        expected = month_range(cfg["date_window"]["start"], cfg["date_window"]["end"])
        if len(dim_m) != len(expected):
            errors.append(f"dim_month: expected {len(expected)} months, got {len(dim_m)}")
        covid_true = dim_m["is_covid"].sum() if "is_covid" in dim_m.columns else 0
        if covid_true != 34:
            errors.append(f"dim_month: expected 34 COVID months, got {covid_true}")

    if not rain.empty:
        window = cfg["date_window"]
        bad = rain[(rain["year_month"] < window["start"]) | (rain["year_month"] > window["end"])]
        if len(bad):
            errors.append(f"fact_rainfall: {len(bad)} rows outside date window")

    long_path = CORE / "fact_tourism_long.parquet"
    if long_path.exists():
        long_df = pd.read_parquet(long_path)
        if "province_id" in long_df.columns:
            prov_rows = long_df[long_df.get("area_level", "province") == "province"]
            invalid = _invalid_province_ids(prov_rows["province_id"])
            if invalid:
                errors.append(f"fact_tourism_long: {invalid} province-level rows missing province_id")
            raw_col = "province_th_raw" if "province_th_raw" in long_df.columns else "province_th"
            unmapped = prov_rows[prov_rows["province_id"].isna() & prov_rows[raw_col].notna()]
            if len(unmapped):
                errors.append(f"fact_tourism_long: {len(unmapped)} unmapped province names")
            if (long_df["value"] < 0).any():
                errors.append("fact_tourism_long: negative values present")

    if not tour.empty:
        errors.extend(check_thai_foreign_total(tour))

    staging_long = STAGING / "mots" / "fact_tourism_long.parquet"
    if staging_long.exists():
        s_long = pd.read_parquet(staging_long)
        errors.extend(check_guest_months_sum_to_quarter(s_long))

    if not pm.empty:
        spec = contracts.get("province_month", {})
        cov = spec.get("coverage", {})
        gmin = cov.get("guest_arrivals_min_non_null")
        vmin = cov.get("visitors_total_min_non_null")
        if gmin and pm["guest_arrivals"].notna().sum() < gmin:
            errors.append(
                f"province_month: guest_arrivals non-null {pm['guest_arrivals'].notna().sum()} < {gmin}"
            )
        if vmin and pm["visitors_total"].notna().sum() < vmin:
            errors.append(
                f"province_month: visitors_total non-null {pm['visitors_total'].notna().sum()} < {vmin}"
            )
        late = pm[pm["year_month"] > cfg["date_window"]["end"]]
        if len(late):
            errors.append(f"province_month: {len(late)} rows after window end")

    latest = latest_manifest_df()
    if not latest.empty:
        file_rows = latest[latest["file_url"].notna()]
        bad_files = file_rows[file_rows["status"] == "failed"]
        bad_news = latest[latest["file_url"].isna() & latest["status"].isin(("failed", "error"))]
        print(
            f"Manifest (latest per URL): {len(bad_files)} failed file downloads, "
            f"{len(bad_news)} failed/error news rows"
        )
        if len(bad_files):
            errors.append(f"manifest: {len(bad_files)} file URLs with latest status failed")

    quarantine = STAGING / "mots" / "quarantine" / "quarantine.csv"
    if quarantine.exists():
        q = pd.read_csv(quarantine)
        if len(q):
            print(f"Quarantined MOTS files: {len(q)}")

    if errors:
        print("QA FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("QA passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
