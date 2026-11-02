"""Aggregate province_month to national monthly series for the handbook chart."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import CORE

PROVINCE_MONTH = CORE / "province_month.parquet"


def aggregate_national_monthly(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Sum tourism, mean rain, one row per year_month in calendar order."""
    if df.empty or "year_month" not in df.columns:
        return []

    rows: list[dict[str, Any]] = []
    for year_month, part in df.groupby("year_month", sort=True):
        guests = part["guest_arrivals"] if "guest_arrivals" in part.columns else pd.Series(dtype=float)
        visitors = part["visitors_total"] if "visitors_total" in part.columns else pd.Series(dtype=float)
        rain = part["avg_rain_mm"] if "avg_rain_mm" in part.columns else pd.Series(dtype=float)
        covid_col = part["is_covid"] if "is_covid" in part.columns else pd.Series([False])

        guest_sum = float(guests.sum(skipna=True)) if guests.notna().any() else None
        if visitors.notna().any():
            visitor_sum: float | None = float(visitors.sum(skipna=True))
        else:
            visitor_sum = None
        rain_mean = float(rain.mean(skipna=True)) if rain.notna().any() else None
        is_covid = bool(covid_col.fillna(False).iloc[0])

        rows.append(
            {
                "year_month": str(year_month),
                "guest_arrivals": guest_sum,
                "visitors_total": visitor_sum,
                "avg_rain_mm": rain_mean,
                "is_covid": is_covid,
            }
        )
    return rows


def load_national_series(path: Path | None = None) -> dict[str, Any]:
    parquet = path or PROVINCE_MONTH
    if not parquet.is_file():
        return {"available": False, "months": []}
    df = pd.read_parquet(parquet)
    months = aggregate_national_monthly(df)
    if not months:
        return {"available": False, "months": []}
    return {"available": True, "months": months}
