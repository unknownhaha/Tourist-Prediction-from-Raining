"""Parse MOTS internal-tourism workbooks with province rows and Q1–Q4 columns."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.common.calendar import be_to_ce
from src.common.paths import RAW

SKIP_NAMES = {"Visitor", "รวม", "Total", "nan", ""}
REGION_PREFIX = ("รวม", "Internal", "Guest", "Average", "1.")


def _year_be_from_path(path: Path) -> int | None:
    m = re.search(r"mots[/\\](\d{4})[/\\]", str(path).replace("\\", "/"))
    if m:
        return int(m.group(1)) + 543
    m = re.search(r"256[0-9]", path.name)
    if m:
        return int(m.group(0))
    return None


def _is_province_row(name: str) -> bool:
    name = str(name).strip()
    if not name or name in SKIP_NAMES:
        return False
    if any(name.startswith(p) for p in REGION_PREFIX):
        return False
    if "ภาค" in name and "รวม" in name:
        return False
    return True


def _find_quarter_blocks(df: pd.DataFrame) -> list[tuple[int, str]]:
    """Return list of (start_col, quarter_label) e.g. (1, 'Q1')."""
    for i in range(min(5, len(df))):
        row = df.iloc[i]
        blocks: list[tuple[int, str]] = []
        for j, val in enumerate(row):
            s = str(val).strip().upper()
            if s in ("Q1", "Q2", "Q3", "Q4"):
                blocks.append((j, s))
        if blocks:
            return blocks
    return []


def try_parse(path: Path) -> pd.DataFrame | None:
    year_be = _year_be_from_path(path)
    if year_be is None:
        return None
    year_ce = be_to_ce(year_be)
    try:
        xl = pd.ExcelFile(path)
    except Exception:
        return None

    frames = []
    for sheet in xl.sheet_names:
        if "สรุป" not in sheet and not sheet.strip().endswith("61"):
            continue
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        if df.shape[0] < 6:
            continue
        blocks = _find_quarter_blocks(df)
        if not blocks:
            continue
        rows = []
        for idx in range(4, len(df)):
            prov = df.iloc[idx, 0]
            if not _is_province_row(prov):
                continue
            for start_col, q in blocks:
                val = df.iloc[idx, start_col]
                if pd.isna(val):
                    continue
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    continue
                rows.append(
                    {
                        "province_th": str(prov).strip(),
                        "year_ce": year_ce,
                        "year_be": year_be,
                        "quarter": q,
                        "year_month": None,
                        "metric": "visitors",
                        "tourist_type": "thai_quarter",
                        "value": num,
                        "unit": "count",
                        "source_file": str(path.relative_to(RAW.parent.parent)),
                        "source": "mots",
                        "sheet": sheet,
                        "file_type": "internal_tourism_quarter",
                    }
                )
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)
