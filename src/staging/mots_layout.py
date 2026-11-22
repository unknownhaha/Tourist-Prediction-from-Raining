"""Detect MOTS workbook layout from sheet names and sample cells."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

MONTH_SHEET_RE = re.compile(
    r"^(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)\d{4}\(R\)$"
)

OUT_OF_SCOPE_CATEGORY_IDS = {
    500,
    524,
    529,
    530,
    585,
    615,
    616,
    742,
    743,
    745,
    746,
    761,
    762,
    797,
    798,
    810,
    811,
    836,
    837,
}


def _category_id_from_path(path: Path) -> int | None:
    m = re.search(r"mots[/\\]\d{4}[/\\](\d+)[/\\]", str(path).replace("\\", "/"))
    return int(m.group(1)) if m else None


def _open_excel(path: Path) -> pd.ExcelFile | None:
    try:
        return pd.ExcelFile(path)
    except Exception:
        return None


def has_monthly_r_sheets(path: Path, xl: pd.ExcelFile | None = None) -> bool:
    book = xl if xl is not None else _open_excel(path)
    if book is None:
        return False
    try:
        return any(MONTH_SHEET_RE.match(str(s).strip()) for s in book.sheet_names)
    finally:
        if xl is None:
            book.close()


def has_quarterly_guest_grid(path: Path, xl: pd.ExcelFile | None = None) -> bool:
    book = xl if xl is not None else _open_excel(path)
    if book is None:
        return False
    try:
        # Month sheets are not the English guest grid. Skipping them avoids a second
        # full read of every monthly workbook during layout detection.
        for sheet in book.sheet_names[:25]:
            name = str(sheet).strip()
            if len(name) < 2 or MONTH_SHEET_RE.match(name):
                continue
            try:
                df = book.parse(sheet, header=None, nrows=45)
            except Exception:
                continue
            flat = " ".join(str(v) for v in df.values.ravel() if str(v) != "nan")
            if "Grand Total" in flat and ("Jan" in flat or "January" in flat):
                return True
        return False
    finally:
        if xl is None:
            book.close()


def workbook_layouts(path: Path, xl: pd.ExcelFile | None = None) -> list[str]:
    cid = _category_id_from_path(path)
    if cid in OUT_OF_SCOPE_CATEGORY_IDS:
        return ["out_of_scope_national"]
    owns = xl is None
    book = xl if xl is not None else _open_excel(path)
    if book is None:
        return ["unrecognized_layout"]
    try:
        layouts: list[str] = []
        if has_monthly_r_sheets(path, book):
            layouts.append("monthly_province")
        if has_quarterly_guest_grid(path, book):
            layouts.append("quarterly_domestic")
        if layouts:
            return layouts
        return ["unrecognized_layout"]
    finally:
        if owns:
            book.close()


def classify_workbook(path: Path) -> str:
    layouts = workbook_layouts(path)
    return layouts[0]
