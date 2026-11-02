"""Parse MOTS monthly province reports (sheets named e.g. ม.ค.2567(R))."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.common.calendar import be_to_ce
from src.common.path_rel import path_relative_to_root
from src.common.provinces import alias_to_province_id, classify_sheet, load_crosswalk, normalize_name

SHEET_MONTH_RE = re.compile(
    r"^(ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)(\d{4})\(R\)$"
)
THAI_MONTH = {
    "ม.ค.": 1,
    "ก.พ.": 2,
    "มี.ค.": 3,
    "เม.ย.": 4,
    "พ.ค.": 5,
    "มิ.ย.": 6,
    "ก.ค.": 7,
    "ส.ค.": 8,
    "ก.ย.": 9,
    "ต.ค.": 10,
    "พ.ย.": 11,
    "ธ.ค.": 12,
}

SKIP_PROVINCE = (
    "ภาค",
    "รวม",
    "ที่มา",
    "R/Update",
    "nan",
)


def _metric_kind(label: str) -> tuple[str, str] | None:
    s = str(label)
    if "อัตราการเข้าพัก" in s:
        return ("occupancy_pct", "total")
    if "จำนวนผู้เข้าพัก" in s:
        return ("guests", "total")
    if "รายได้จากผู้เยี่ยมเยือนคนไทย" in s:
        return ("revenue_million_baht", "thai")
    if "รายได้จากผู้เยี่ยมเยือนชาวต่างชาติ" in s:
        return ("revenue_million_baht", "foreign")
    if "รายได้จากผู้เยี่ยมเยือน" in s and "รายได้" in s:
        return ("revenue_million_baht", "total")
    if "รายได้" in s:
        return None
    if "เยี่ยมเยือนทั้งหมด" in s or "ผู้เยี่ยมเยือนทั้งหมด" in s:
        return ("visitors", "total")
    if "เยี่ยมเยือนคนไทย" in s or ("คนไทย" in s and "เยี่ยม" in s):
        return ("visitors", "thai")
    if "ชาวต่างชาติ" in s:
        return ("visitors", "foreign")
    return None


def _year_be_from_cell(val) -> int | None:
    if pd.isna(val):
        return None
    m = re.search(r"(25\d{2})", str(val))
    return int(m.group(1)) if m else None


def _find_metric_groups(row1: pd.Series, row3: pd.Series) -> list[tuple[int, str, str]]:
    groups: list[tuple[int, str, str]] = []
    c = 0
    while c < len(row1):
        label = row1.iloc[c] if c < len(row1) else None
        if pd.notna(label) and str(label).strip():
            mk = _metric_kind(str(label))
            if mk and c + 2 < len(row3):
                groups.append((c, mk[0], mk[1]))
                c += 3
                continue
        c += 1
    return groups


def parse_workbook(path: Path, cw: pd.DataFrame | None = None) -> pd.DataFrame:
    if cw is None:
        cw = load_crosswalk()
    rel = path_relative_to_root(path)
    frames: list[pd.DataFrame] = []
    xl = pd.ExcelFile(path)
    for sheet in xl.sheet_names:
        m = SHEET_MONTH_RE.match(str(sheet).strip())
        if not m:
            continue
        month = THAI_MONTH[m.group(1)]
        sheet_be = int(m.group(2))
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        if df.shape[0] < 6 or df.shape[1] < 8:
            continue
        row1 = df.iloc[1]
        row3 = df.iloc[3]
        groups = _find_metric_groups(row1, row3)
        rows: list[dict] = []
        for idx in range(4, len(df)):
            prov_raw = df.iloc[idx, 1]
            if pd.isna(prov_raw):
                continue
            prov_s = str(prov_raw).strip()
            if not prov_s or any(prov_s.startswith(p) for p in SKIP_PROVINCE):
                continue
            if classify_sheet(prov_s, cw) != "province":
                if alias_to_province_id(prov_s, cw) is None:
                    continue
            pid = alias_to_province_id(prov_s, cw)
            for start_col, metric, tourist_type in groups:
                cur_be = _year_be_from_cell(row3.iloc[start_col])
                prior_be = _year_be_from_cell(row3.iloc[start_col + 1])
                for offset, from_prior in ((0, False), (1, True)):
                    ybe = prior_be if from_prior else cur_be
                    if ybe is None:
                        continue
                    val = df.iloc[idx, start_col + offset]
                    if pd.isna(val):
                        continue
                    try:
                        num = float(val)
                    except (TypeError, ValueError):
                        continue
                    yce = be_to_ce(ybe)
                    ym = f"{yce:04d}-{month:02d}"
                    unit = "percent" if metric == "occupancy_pct" else "count"
                    if metric.startswith("revenue"):
                        unit = "million_baht"
                    rows.append(
                        {
                            "province_th_raw": prov_s,
                            "province_id": pid,
                            "area_level": "province",
                            "year_ce": yce,
                            "year_month": ym,
                            "month": month,
                            "quarter": None,
                            "metric": metric,
                            "tourist_type": tourist_type,
                            "value": num,
                            "unit": unit,
                            "source_file": rel,
                            "source": "mots",
                            "sheet": sheet,
                            "layout": "monthly_province",
                            "from_prior_year_column": from_prior,
                        }
                    )
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
