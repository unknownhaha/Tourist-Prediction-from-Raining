"""Refresh docs/coverage_report.md from core and manifest."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from src.common.config_loader import load_sources
from src.common.manifest import load_manifest
from src.common.paths import CORE, ROOT, STAGING


def main() -> int:
    lines = ["# Coverage report", "", f"_Generated from pipeline state in `{ROOT.name}`._", ""]
    cfg = load_sources()
    window = cfg["date_window"]

    if (CORE / "province_month.parquet").exists():
        pm = pd.read_parquet(CORE / "province_month.parquet")
        lines.append("## province_month")
        lines.append(f"- Rows: {len(pm)} (expected 77 × {len(pm['year_month'].unique())} = {77 * len(pm['year_month'].unique())})")
        for col in ("avg_rain_mm", "visitors_unknown"):
            if col in pm.columns:
                nn = pm[col].notna().sum()
                lines.append(f"- Non-null `{col}`: {nn}")
        lines.append("")

    if (CORE / "fact_rainfall.parquet").exists():
        rain = pd.read_parquet(CORE / "fact_rainfall.parquet")
        months = rain["year_month"].nunique()
        provs = rain["province_id"].nunique()
        lines.append("## fact_rainfall")
        lines.append(f"- Provinces: {provs}, months: {months}")
        lines.append("")

    manifest = load_manifest()
    if manifest:
        mdf = pd.DataFrame(manifest)
        lines.append("## Manifest")
        lines.append(mdf.groupby(["source", "status"]).size().to_string())
        lines.append("")

    q = STAGING / "mots" / "quarantine" / "quarantine.csv"
    if q.exists():
        qc = pd.read_csv(q)
        lines.append(f"## MOTS quarantine: {len(qc)} files")
        lines.append("")

    lines.append("## Date window")
    lines.append(f"- {window['start']} … {window['end']} (CE)")
    lines.append("")
    lines.append("## Known gaps")
    lines.append("- NSO MD BE 2567 (2024) survey round not published on portal")
    lines.append("- MOTS BE 2568 may be incomplete through 2025-08")

    out = ROOT / "docs" / "coverage_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Updated {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
