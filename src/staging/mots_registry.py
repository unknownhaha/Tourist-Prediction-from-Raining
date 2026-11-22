"""MOTS Excel layout dispatcher -> long-format tourism facts."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from src.common.path_rel import path_relative_to_root
from src.common.paths import RAW, STAGING
from src.common.provinces import load_crosswalk
from src.staging.mots_dedupe import dedupe_long
from src.staging.mots_layout import OUT_OF_SCOPE_CATEGORY_IDS, _category_id_from_path, workbook_layouts
from src.staging.mots_monthly_province import parse_workbook as parse_monthly
from src.staging.mots_quarterly_province import parse_workbook as parse_quarterly

QUARANTINE = STAGING / "mots" / "quarantine"
LONG_OUT = STAGING / "mots" / "fact_tourism_long.parquet"

_WORKER_CW: pd.DataFrame | None = None


def _init_worker() -> None:
    global _WORKER_CW
    _WORKER_CW = load_crosswalk()


def _parse_one(path_str: str) -> tuple[pd.DataFrame | None, dict | None]:
    """Classify and parse one workbook on a single Excel handle."""
    path = Path(path_str)
    rel = path_relative_to_root(path)
    if _category_id_from_path(path) in OUT_OF_SCOPE_CATEGORY_IDS:
        return None, {"file": rel, "reason": "out_of_scope_national"}
    cw = _WORKER_CW if _WORKER_CW is not None else load_crosswalk()
    try:
        xl = pd.ExcelFile(path)
    except Exception:
        return None, {"file": rel, "reason": "unrecognized_layout"}
    try:
        layouts = workbook_layouts(path, xl)
        if layouts == ["unrecognized_layout"]:
            return None, {"file": rel, "reason": "unrecognized_layout"}
        frames: list[pd.DataFrame] = []
        if "monthly_province" in layouts:
            df = parse_monthly(path, cw=cw, xl=xl)
            if df is not None and not df.empty:
                frames.append(df)
        if "quarterly_domestic" in layouts:
            df = parse_quarterly(path, cw=cw, xl=xl)
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return None, {"file": rel, "reason": "parse_empty"}
        return pd.concat(frames, ignore_index=True), None
    finally:
        xl.close()


def _parse_files(files: list[Path]) -> tuple[list[pd.DataFrame], list[dict]]:
    if not files:
        return [], []
    workers = min(8, os.cpu_count() or 1, len(files))
    paths = [str(f) for f in files]
    if workers == 1:
        results = [_parse_one(p) for p in paths]
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as pool:
            results = list(pool.map(_parse_one, paths, chunksize=1))
    parsed: list[pd.DataFrame] = []
    quarantine: list[dict] = []
    for df, row in results:
        if df is not None and not df.empty:
            parsed.append(df)
        if row is not None:
            quarantine.append(row)
    return parsed, quarantine


def run_all() -> Path:
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    STAGING.mkdir(parents=True, exist_ok=True)
    mots_root = RAW / "mots"
    files: list[Path] = []
    if mots_root.exists():
        files = [
            f
            for f in sorted(mots_root.rglob("*"))
            if f.suffix.lower() in (".xlsx", ".xls")
        ]
    parsed, quarantine = _parse_files(files)
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
