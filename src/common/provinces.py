"""Province name crosswalk lookups."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common.paths import CONFIG


def load_crosswalk() -> pd.DataFrame:
    path = CONFIG / "province_crosswalk.csv"
    if not path.exists():
        raise FileNotFoundError("Run python -m src.ingest.hii to build province_crosswalk.csv")
    return pd.read_csv(path)


def alias_to_province_id(alias: str, crosswalk: pd.DataFrame | None = None) -> str | None:
    cw = crosswalk if crosswalk is not None else load_crosswalk()
    alias = str(alias).strip()
    hit = cw[cw["alias"].astype(str).str.strip() == alias]
    if hit.empty:
        hit = cw[cw["province_th"].astype(str).str.strip() == alias]
    if hit.empty:
        # Substring match against canonical Thai names (MOTS variants)
        for _, row in cw.drop_duplicates("province_id").iterrows():
            th = str(row.get("province_th", "")).strip()
            if th and (th in alias or alias in th):
                return str(row["province_id"])
    if hit.empty:
        return None
    return str(hit.iloc[0]["province_id"])
