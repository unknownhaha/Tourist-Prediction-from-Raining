"""MOTS Excel layout dispatcher -> long-format tourism facts."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common.path_rel import path_relative_to_root
from src.common.paths import RAW, STAGING
from src.staging.mots_dedupe import dedupe_long
from src.staging.mots_layout import workbook_layouts
from src.staging.mots_monthly_province import parse_workbook as parse_monthly
from src.staging.mots_quarterly_province import parse_workbook as parse_quarterly

QUARANTINE = STAGING / "mots" / "quarantine"
LONG_OUT = STAGING / "mots" / "fact_tourism_long.parquet"


def run_all() -> Path:
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    STAGING.mkdir(parents=True, exist_ok=True)
    mots_root = RAW / "mots"
    parsed: list[pd.DataFrame] = []
    quarantine: list[dict] = []
    if mots_root.exists():
        for f in sorted(mots_root.rglob("*")):
            if f.suffix.lower() not in (".xlsx", ".xls"):
                continue
            layouts = workbook_layouts(f)
            if layouts == ["out_of_scope_national"]:
                quarantine.append(
                    {
                        "file": path_relative_to_root(f),
                        "reason": "out_of_scope_national",
                    }
                )
                continue
            if layouts == ["unrecognized_layout"]:
                quarantine.append(
                    {
                        "file": path_relative_to_root(f),
                        "reason": "unrecognized_layout",
                    }
                )
                continue
            frames: list[pd.DataFrame] = []
            if "monthly_province" in layouts:
                df = parse_monthly(f)
                if df is not None and not df.empty:
                    frames.append(df)
            if "quarterly_domestic" in layouts:
                df = parse_quarterly(f)
                if df is not None and not df.empty:
                    frames.append(df)
            if not frames:
                quarantine.append(
                    {
                        "file": path_relative_to_root(f),
                        "reason": "parse_empty",
                    }
                )
            else:
                parsed.append(pd.concat(frames, ignore_index=True))
    out_path = LONG_OUT
    if parsed:
        combined = dedupe_long(pd.concat(parsed, ignore_index=True))
        combined.to_parquet(out_path, index=False)
    else:
        pd.DataFrame(
            columns=[
                "province_th_raw",
                "province_id",
                "area_level",
                "year_month",
                "metric",
                "tourist_type",
                "value",
                "unit",
                "source_file",
                "layout",
                "from_prior_year_column",
            ]
        ).to_parquet(out_path, index=False)
    pd.DataFrame(quarantine).to_csv(QUARANTINE / "quarantine.csv", index=False)
    return out_path
