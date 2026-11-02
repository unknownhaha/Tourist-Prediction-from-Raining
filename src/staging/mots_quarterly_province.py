"""Parse MOTS domestic tourism workbooks (province sheets, Jan-Dec guest grid)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.common.path_rel import path_relative_to_root
from src.common.provinces import alias_to_province_id, classify_sheet, load_crosswalk, normalize_name

MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def _year_ce_from_workbook(path: Path, df: pd.DataFrame) -> int | None:
    m = re.search(r"mots[/\\](\d{4})[/\\]", str(path).replace("\\", "/"))
    if m:
        return int(m.group(1))
    flat = " ".join(str(v) for v in df.iloc[:5].values.ravel())
    m2 = re.search(r"\b(20\d{2})\b", flat)
    return int(m2.group(1)) if m2 else None


def _month_block(df: pd.DataFrame) -> tuple[int | None, dict[int, int]]:
    """Return (label_col, {month_num: value_col}) for all Jan-Dec headers on one row."""
    header_row = None
    for i in range(min(6, len(df))):
        row = df.iloc[i]
        if sum(str(v).strip() in MONTHS for v in row if pd.notna(v)) >= 3:
            header_row = i
            break
    if header_row is None:
        return None, {}
    row = df.iloc[header_row]
    month_map: dict[int, int] = {}
    for j, val in enumerate(row):
        s = str(val).strip()
        if s in MONTHS:
            month_map[MONTHS[s]] = j
    if not month_map:
        return None, {}
    label_col = min(month_map.values()) - 1
    return label_col, month_map


QUARTER_HEADER = {
    "January-March": 1,
    "April-June": 2,
    "July-September": 3,
    "October-December": 4,
}

VISITOR_ROWS = (3, 4, 5)
REVENUE_ROWS = (26, 27, 28)
VISITOR_TYPES = ("total", "thai", "foreign")


def _quarter_start_columns(df: pd.DataFrame) -> list[tuple[int, int]]:
    if df.shape[0] < 2:
        return []
    row = df.iloc[1]
    found: list[tuple[int, int]] = []
    for j, val in enumerate(row):
        if pd.isna(val):
            continue
        s = str(val).strip()
        for label, qn in QUARTER_HEADER.items():
            if label in s:
                found.append((j, qn))
                break
    return found


def _guest_quarter_columns(df: pd.DataFrame) -> dict[int, int]:
    if df.shape[0] < 2:
        return {}
    row = df.iloc[1]
    out: dict[int, int] = {}
    for j, val in enumerate(row):
        if pd.isna(val):
            continue
        s = str(val).strip()
        if len(s) == 2 and s[0] == "Q" and s[1].isdigit():
            out[int(s[1])] = j
    return out


def _parse_guest_quarter_totals(
    df: pd.DataFrame,
    *,
    rel: str,
    sheet_stripped: str,
    pid: str | None,
    area_level: str,
    year_ce: int,
    grand_row: int | None,
    thai_row: int | None,
    foreign_row: int | None,
) -> list[dict]:
    rows: list[dict] = []
    qcols = _guest_quarter_columns(df)
    for tourist_type, ri in (
        ("total", grand_row),
        ("thai", thai_row),
        ("foreign", foreign_row),
    ):
        if ri is None:
            continue
        for qn, col in qcols.items():
            if col >= df.shape[1]:
                continue
            val = df.iloc[ri, col]
            if pd.isna(val):
                continue
            try:
                num = float(val)
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "province_th_raw": sheet_stripped,
                    "province_id": pid if area_level == "province" else None,
                    "area_level": area_level,
                    "year_ce": year_ce,
                    "year_month": None,
                    "month": None,
                    "quarter": f"{year_ce}-Q{qn}",
                    "metric": "guest_arrivals",
                    "tourist_type": tourist_type,
                    "value": num,
                    "unit": "count",
                    "source_file": rel,
                    "source": "mots",
                    "sheet": sheet_stripped,
                    "layout": "quarterly_domestic",
                    "from_prior_year_column": False,
                }
            )
    return rows


def _parse_quarterly_left_block(
    df: pd.DataFrame,
    *,
    rel: str,
    sheet_stripped: str,
    pid: str | None,
    area_level: str,
    year_ce: int,
) -> list[dict]:
    rows: list[dict] = []
    for col, qn in _quarter_start_columns(df):
        for metric, row_indices, unit in (
            ("visitors", VISITOR_ROWS, "count"),
            ("revenue_million_baht", REVENUE_ROWS, "million_baht"),
        ):
            for tourist_type, ri in zip(VISITOR_TYPES, row_indices, strict=True):
                if ri >= len(df) or col >= df.shape[1]:
                    continue
                val = df.iloc[ri, col]
                if pd.isna(val):
                    continue
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    continue
                rows.append(
                    {
                        "province_th_raw": sheet_stripped,
                        "province_id": pid if area_level == "province" else None,
                        "area_level": area_level,
                        "year_ce": year_ce,
                        "year_month": None,
                        "month": None,
                        "quarter": f"{year_ce}-Q{qn}",
                        "metric": metric,
                        "tourist_type": tourist_type,
                        "value": num,
                        "unit": unit,
                        "source_file": rel,
                        "source": "mots",
                        "sheet": sheet_stripped,
                        "layout": "quarterly_domestic",
                        "from_prior_year_column": False,
                    }
                )
    return rows


def _row_label(df: pd.DataFrame, row_idx: int, label_col: int) -> str:
    for c in range(max(0, label_col - 2), label_col + 1):
        if c < df.shape[1]:
            v = df.iloc[row_idx, c]
            if pd.notna(v) and str(v).strip():
                return str(v).strip()
    return ""


def parse_workbook(path: Path, cw: pd.DataFrame | None = None) -> pd.DataFrame:
    if cw is None:
        cw = load_crosswalk()
    rel = path_relative_to_root(path)
    frames: list[pd.DataFrame] = []
    xl = pd.ExcelFile(path)
    for sheet in xl.sheet_names:
        sheet_stripped = str(sheet).strip()
        level = classify_sheet(sheet_stripped, cw)
        if level == "region":
            continue
        norm_sheet = normalize_name(sheet_stripped)
        pid = alias_to_province_id(norm_sheet, cw) or alias_to_province_id(sheet_stripped, cw)
        if level == "sub_area":
            area_level = "sub_area"
        elif level == "province" and pid:
            area_level = "province"
        else:
            continue
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        if df.shape[0] < 40:
            continue
        year_ce = _year_ce_from_workbook(path, df)
        if year_ce is None:
            continue
        label_col, month_map = _month_block(df)
        if not month_map:
            continue
        rows: list[dict] = []
        grand_row = None
        thai_row = None
        foreign_row = None
        for ri in range(len(df)):
            lab = _row_label(df, ri, label_col).lower()
            if lab == "grand total":
                grand_row = ri
            elif lab == "thai":
                thai_row = ri
            elif lab == "foreigners":
                foreign_row = ri
        for tourist_type, ri in (
            ("total", grand_row),
            ("thai", thai_row),
            ("foreign", foreign_row),
        ):
            if ri is None:
                continue
            for month_num, col in month_map.items():
                val = df.iloc[ri, col]
                if pd.isna(val):
                    continue
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    continue
                ym = f"{year_ce:04d}-{month_num:02d}"
                rows.append(
                    {
                        "province_th_raw": sheet_stripped,
                        "province_id": pid if area_level == "province" else None,
                        "area_level": area_level,
                        "year_ce": year_ce,
                        "year_month": ym,
                        "month": month_num,
                        "quarter": None,
                        "metric": "guest_arrivals",
                        "tourist_type": tourist_type,
                        "value": num,
                        "unit": "count",
                        "source_file": rel,
                        "source": "mots",
                        "sheet": sheet_stripped,
                        "layout": "quarterly_domestic",
                        "from_prior_year_column": False,
                    }
                )
        rows.extend(
            _parse_quarterly_left_block(
                df,
                rel=rel,
                sheet_stripped=sheet_stripped,
                pid=pid,
                area_level=area_level,
                year_ce=year_ce,
            )
        )
        rows.extend(
            _parse_guest_quarter_totals(
                df,
                rel=rel,
                sheet_stripped=sheet_stripped,
                pid=pid,
                area_level=area_level,
                year_ce=year_ce,
                grand_row=grand_row,
                thai_row=thai_row,
                foreign_row=foreign_row,
            )
        )
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
