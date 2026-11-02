"""Report unmapped province names from staged MOTS data."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common.paths import STAGING
from src.common.provinces import alias_to_province_id, load_crosswalk, normalize_name


def report_unmapped_mots():
    path = STAGING / "mots" / "fact_tourism_long.parquet"
    out = STAGING / "mots" / "quarantine" / "unmapped_provinces.csv"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if df.empty or "province_th_raw" not in df.columns:
        return None
    cw = load_crosswalk()
    rows = []
    for name in sorted(df["province_th_raw"].dropna().unique()):
        n = str(name).strip()
        if not n:
            continue
        if df.loc[df["province_th_raw"] == name, "area_level"].eq("region").all():
            continue
        pid = alias_to_province_id(n, cw)
        if pid is None and normalize_name(n):
            rows.append({"province_th_raw": n, "n_rows": int((df["province_th_raw"] == name).sum())})
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    return out if rows else out


def extend_from_mots() -> int:
    """Backward-compatible hook: write unmapped report, do not mutate crosswalk."""
    p = report_unmapped_mots()
    if p is None or not p.exists():
        return 0
    df = pd.read_csv(p)
    return len(df)
