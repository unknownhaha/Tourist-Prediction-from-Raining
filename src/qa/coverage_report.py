"""Refresh docs/coverage_report.md from core and manifest."""

from __future__ import annotations

import sys

import pandas as pd

from src.common.config_loader import load_sources
from src.common.paths import CORE, ROOT, STAGING
from src.qa.manifest_summary import latest_manifest_df


def main() -> int:
    lines = ["# Coverage report", "", f"_Generated from pipeline state in `{ROOT.name}`._", ""]
    cfg = load_sources()
    window = cfg["date_window"]

    if (CORE / "province_month.parquet").exists():
        pm = pd.read_parquet(CORE / "province_month.parquet")
        n_months = pm["year_month"].nunique()
        lines.append("## province_month")
        lines.append(f"- Rows: {len(pm)} (expected 77 × {n_months} = {77 * n_months})")
        for col in ("avg_rain_mm", "guest_arrivals", "visitors_total"):
            if col in pm.columns:
                nn = pm[col].notna().sum()
                lines.append(f"- Non-null `{col}`: {nn}")
        by_year = pm.assign(y=pm["year_month"].str.slice(0, 4)).groupby("y").agg(
            guest=("guest_arrivals", lambda s: s.notna().sum()),
            visitors=("visitors_total", lambda s: s.notna().sum()),
        )
        lines.append("- By year (non-null counts):")
        lines.append(by_year.to_string())
        if "guest_arrivals" in pm.columns:
            miss = pm[pm["guest_arrivals"].isna()].merge(
                pd.read_parquet(CORE / "dim_province.parquet")[["province_id", "province_th"]],
                on="province_id",
                how="left",
            )
            if len(miss):
                lines.append("- Missing `guest_arrivals` by province:")
                lines.append(
                    miss.groupby(["province_id", "province_th"]).size().sort_values(ascending=False).to_string()
                )
                lines.append("- Missing `guest_arrivals` by year:")
                lines.append(miss.assign(y=miss["year_month"].str.slice(0, 4)).groupby("y").size().to_string())
        lines.append("")

    if (CORE / "fact_rainfall.parquet").exists():
        rain = pd.read_parquet(CORE / "fact_rainfall.parquet")
        lines.append("## fact_rainfall")
        lines.append(f"- Provinces: {rain['province_id'].nunique()}, months: {rain['year_month'].nunique()}")
        lines.append("")

    latest = latest_manifest_df()
    if not latest.empty:
        lines.append("## Manifest (latest status per URL)")
        lines.append(latest.groupby(["source", "status"]).size().to_string())
        lines.append("")

    q = STAGING / "mots" / "quarantine" / "quarantine.csv"
    if q.exists():
        qc = pd.read_csv(q)
        lines.append(f"## MOTS quarantine: {len(qc)} files")
        if len(qc):
            lines.append(qc.groupby("reason").size().to_string())
        lines.append("")

    lines.append("## Date window")
    lines.append(f"- {window['start']} … {window['end']} (CE)")

    out = ROOT / "docs" / "coverage_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Updated {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
