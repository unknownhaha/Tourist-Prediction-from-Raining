"""Heuristic MOTS Excel parser registry -> long-format tourism facts."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.common.paths import RAW, STAGING
from src.staging.mots_internal_quarter import try_parse as try_parse_quarter

QUARANTINE = STAGING / "mots" / "quarantine"
LONG_OUT = STAGING / "mots" / "fact_tourism_long.parquet"

THAI_MONTHS = {
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


def _parse_year_ce_from_path(path: Path) -> int | None:
    m = re.search(r"mots[/\\](\d{4})[/\\]", str(path).replace("\\", "/"))
    return int(m.group(1)) if m else None


def try_parse_workbook(path: Path) -> pd.DataFrame | None:
    try:
        xl = pd.ExcelFile(path)
    except Exception:
        return None
    frames = []
    for sheet in xl.sheet_names[:10]:
        try:
            df = pd.read_excel(path, sheet_name=sheet, header=None)
        except Exception:
            continue
        if df.shape[0] < 5 or df.shape[1] < 4:
            continue
        header_row = None
        for i in range(min(15, len(df))):
            row = df.iloc[i].astype(str).str.lower()
            if row.str.contains("จังหวัด|province", regex=True).any():
                header_row = i
                break
        if header_row is None:
            continue
        body = pd.read_excel(path, sheet_name=sheet, header=header_row)
        body.columns = [str(c).strip() for c in body.columns]
        prov_col = next(
            (c for c in body.columns if "จังหวัด" in c or "province" in c.lower()),
            None,
        )
        if not prov_col:
            continue
        month_cols = [c for c in body.columns if str(c) in THAI_MONTHS or re.match(r"^\d{1,2}$", str(c).strip())]
        if not month_cols:
            continue
        year_ce = _parse_year_ce_from_path(path)
        long_rows = []
        for _, row in body.iterrows():
            prov = row[prov_col]
            if isinstance(prov, pd.Series):
                prov = prov.iloc[0]
            if pd.isna(prov) or str(prov).strip() in ("", "รวม", "Total"):
                continue
            for col in month_cols:
                val = row.get(col)
                if pd.isna(val):
                    continue
                if str(col) in THAI_MONTHS:
                    month = THAI_MONTHS[str(col)]
                else:
                    try:
                        month = int(str(col).strip())
                    except ValueError:
                        continue
                if month < 1 or month > 12 or year_ce is None:
                    continue
                ym = f"{year_ce:04d}-{month:02d}"
                long_rows.append(
                    {
                        "province_th": str(prov).strip(),
                        "year_month": ym,
                        "year_ce": year_ce,
                        "month": month,
                        "metric": "visitors",
                        "tourist_type": "unknown",
                        "value": float(val) if isinstance(val, (int, float)) else None,
                        "unit": "count",
                        "source_file": str(path.relative_to(RAW.parent.parent)),
                        "source": "mots",
                        "sheet": sheet,
                    }
                )
        if long_rows:
            frames.append(pd.DataFrame(long_rows))
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def run_all() -> Path:
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    STAGING.mkdir(parents=True, exist_ok=True)
    mots_root = RAW / "mots"
    parsed = []
    quarantine = []
    if mots_root.exists():
        for f in mots_root.rglob("*"):
            if f.suffix.lower() not in (".xlsx", ".xls"):
                continue
            df = try_parse_quarter(f)
            if df is None or df.empty:
                df = try_parse_workbook(f)
            if df is None or df.empty:
                quarantine.append({"file": str(f.relative_to(RAW.parent.parent)), "reason": "unrecognized_schema"})
            else:
                parsed.append(df)
    out_path = LONG_OUT
    if parsed:
        pd.concat(parsed, ignore_index=True).to_parquet(out_path, index=False)
    else:
        pd.DataFrame(
            columns=[
                "province_th",
                "year_month",
                "metric",
                "tourist_type",
                "value",
                "unit",
                "source_file",
            ]
        ).to_parquet(out_path, index=False)
    pd.DataFrame(quarantine).to_csv(QUARANTINE / "quarantine.csv", index=False)
    return out_path
