"""Resolve duplicate MOTS long-format keys across files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common.paths import STAGING

CONFLICT_OUT = STAGING / "mots" / "quarantine" / "conflicts.csv"

GRAIN_TAIL = ["area_level", "year_month", "quarter", "metric", "tourist_type"]


def _with_dedupe_province(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    raw = out["province_th_raw"].astype(str)
    if "province_id" in out.columns:
        pid = out["province_id"].astype("string")
        valid = pid.notna() & (pid.str.strip() != "") & (pid.str.lower() != "nan")
        out["_dedupe_province"] = pid.where(valid, "raw:" + raw)
    else:
        out["_dedupe_province"] = "raw:" + raw
    return out


def _dedupe_key_cols(df: pd.DataFrame) -> list[str]:
    return ["_dedupe_province", *[c for c in GRAIN_TAIL if c in df.columns]]


def _file_rank(source_file: str) -> tuple[int, str]:
    """Prefer non-summary workbooks and newer year folders."""
    s = source_file.replace("\\", "/")
    penalty = 0
    if "256" in Path(s).name and len(Path(s).name) > 40:
        penalty -= 1
    if any(x in s for x in ("/812/", "/796/", "/829/", "/740/", "/741/")):
        penalty += 2
    return (penalty, s)


def _sort_group(grp: pd.DataFrame) -> pd.DataFrame:
    grp = grp.copy()
    grp["_rank"] = grp["source_file"].map(lambda f: _file_rank(str(f)))
    sort_cols = ["_rank"]
    ascending = [False]
    if "from_prior_year_column" in grp.columns:
        sort_cols = ["from_prior_year_column", "_rank"]
        ascending = [True, False]
    return grp.sort_values(sort_cols, ascending=ascending)


def dedupe_long(df: pd.DataFrame, conflict_path: Path | None = None) -> pd.DataFrame:
    if df.empty:
        return df
    work = _with_dedupe_province(df)
    key_cols = _dedupe_key_cols(work)
    conflicts: list[dict] = []
    parts: list[pd.DataFrame] = []
    for key, grp in work.groupby(key_cols, dropna=False):
        if len(grp) == 1:
            parts.append(grp)
            continue
        grp = _sort_group(grp)
        vals = grp["value"].round(3).unique()
        if len(vals) > 1:
            conflicts.append(
                {
                    "key": "|".join(str(k) for k in (key if isinstance(key, tuple) else (key,))),
                    "n_sources": len(grp),
                    "values": ",".join(str(v) for v in vals[:5]),
                    "kept_file": grp.iloc[0]["source_file"],
                }
            )
        parts.append(grp.iloc[[0]].drop(columns="_rank", errors="ignore"))
    out = pd.concat(parts, ignore_index=True).drop(columns=["_dedupe_province"], errors="ignore")
    out_path = conflict_path or CONFLICT_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(conflicts, columns=["key", "n_sources", "values", "kept_file"]).to_csv(
        out_path, index=False
    )
    return out
