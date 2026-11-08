"""Report unmapped province names from staged MOTS data."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common.paths import STAGING
from src.common.provinces import alias_to_province_id, load_crosswalk, normalize_name


UNMAPPED_COLUMNS = ["province_th_raw", "n_rows"]


def unmapped_province_rows(df: pd.DataFrame, cw: pd.DataFrame) -> pd.DataFrame:
    """Province-level names that do not match the crosswalk.

    Region totals and sub-area sheets (Pattaya, Koh Chang, and similar) are omitted.
    """
    if df.empty or "province_th_raw" not in df.columns or "area_level" not in df.columns:
        return pd.DataFrame(columns=UNMAPPED_COLUMNS)
    rows = []
    for name in sorted(df["province_th_raw"].dropna().unique(), key=str):
        n = str(name).strip()
        if not n:
            continue
        province_rows = df[(df["province_th_raw"] == name) & (df["area_level"] == "province")]
        if province_rows.empty:
            continue
        if alias_to_province_id(n, cw) is None and normalize_name(n):
            rows.append({"province_th_raw": n, "n_rows": int(len(province_rows))})
    return pd.DataFrame(rows, columns=UNMAPPED_COLUMNS)


def report_unmapped_mots():
    path = STAGING / "mots" / "fact_tourism_long.parquet"
    out = STAGING / "mots" / "quarantine" / "unmapped_provinces.csv"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if df.empty or "province_th_raw" not in df.columns:
        return None
    cw = load_crosswalk()
    out.parent.mkdir(parents=True, exist_ok=True)
    unmapped_province_rows(df, cw).to_csv(out, index=False)
    return out


def extend_from_mots() -> int:
    """Backward-compatible hook: write unmapped report, do not mutate crosswalk."""
    p = report_unmapped_mots()
    if p is None or not p.exists():
        return 0
    df = pd.read_csv(p)
    return len(df)
