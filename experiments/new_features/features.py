"""Leakage-safe columns added to the log-diff forest.

Lag features read months strictly before t. Holiday and festival columns are
the calendar of t, which is known before the month begins.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from experiments.new_features.calendar_data import CANDLE_PROVINCE, PROVINCE_FESTIVALS, calendar_month_table
from src.model.leakage import assert_no_future_leakage, shift_year_month

REGIONS = ("central", "north", "northeast", "east", "west", "south")
SPLIT_COLUMNS = (
    "thai_lag_1",
    "thai_lag_12",
    "thai_yoy",
    "foreign_lag_1",
    "foreign_lag_12",
    "foreign_yoy",
)
YOY3_COLUMNS = ("yoy_3m_log",)
REGION_COLUMNS = ("region_yoy_log",)
HOLIDAY_COLUMNS = ("holiday_count", "long_weekend_count")
FESTIVAL_COLUMNS = (
    "festival_songkran",
    "festival_new_year",
    "festival_loy",
    "festival_cny",
    "festival_province",
)
FEATURE_GROUPS = {
    "split": SPLIT_COLUMNS,
    "yoy3": YOY3_COLUMNS,
    "region": REGION_COLUMNS,
    "holidays": HOLIDAY_COLUMNS,
    "festivals": FESTIVAL_COLUMNS,
}
# First target month whose lag-15 source is inside 2018-01.., and whose
# Thai/foreign lag-13 source is inside 2019-01.. (2018 splits were not published).
YOY3_READY = "2019-04"
SPLIT_READY = "2020-02"
REGION_READY = "2019-02"


def _pivot(panel: pd.DataFrame, column: str) -> pd.DataFrame:
    return panel.pivot(index="province_id", columns="year_month", values=column)


def _lookup(wide: pd.DataFrame, ids: pd.Series, month: str) -> np.ndarray:
    if month not in wide.columns:
        return np.full(len(ids), np.nan)
    return wide.reindex(ids)[month].to_numpy(dtype=float)


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    out = np.full(len(numerator), np.nan)
    ok = np.isfinite(numerator) & np.isfinite(denominator) & (denominator > 0)
    out[ok] = numerator[ok] / denominator[ok]
    return out


def _safe_log_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    out = np.full(len(numerator), np.nan)
    ok = np.isfinite(numerator) & np.isfinite(denominator) & (numerator > 0) & (denominator > 0)
    out[ok] = np.log(numerator[ok]) - np.log(denominator[ok])
    return out


def source_months(target: str) -> dict[str, str]:
    lags = (1, 2, 3, 12, 13, 14, 15)
    sources = {f"lag_{lag}": shift_year_month(target, lag) for lag in lags}
    assert_no_future_leakage(sources.values(), target)
    return sources


def build_new_features(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per province for 2019-01..2025-12."""
    work = panel.copy()
    work["province_id"] = work["province_id"].astype(str).str.zfill(2)
    work["year_month"] = work["year_month"].astype(str)
    identity = (
        work[["province_id", "province_en", "region"]]
        .drop_duplicates("province_id")
        .set_index("province_id")
    )
    thai = _pivot(work, "guest_arrivals_thai")
    foreign = _pivot(work, "guest_arrivals_foreign")
    total = _pivot(work, "guest_arrivals_total")
    calendar = calendar_month_table().set_index("year_month")
    frames = []
    months = pd.period_range("2019-01", "2025-12", freq="M").strftime("%Y-%m")
    for month in months:
        sources = source_months(month)
        ids = pd.Index(sorted(identity.index.unique()), name="province_id")
        id_series = pd.Series(ids, index=ids)
        thai_1 = _lookup(thai, id_series, sources["lag_1"])
        thai_12 = _lookup(thai, id_series, sources["lag_12"])
        thai_13 = _lookup(thai, id_series, sources["lag_13"])
        foreign_1 = _lookup(foreign, id_series, sources["lag_1"])
        foreign_12 = _lookup(foreign, id_series, sources["lag_12"])
        foreign_13 = _lookup(foreign, id_series, sources["lag_13"])
        recent = np.vstack([_lookup(total, id_series, sources[f"lag_{lag}"]) for lag in (1, 2, 3)])
        older = np.vstack([_lookup(total, id_series, sources[f"lag_{lag}"]) for lag in (13, 14, 15)])
        recent_sum = np.where(np.isfinite(recent).all(axis=0), recent.sum(axis=0), np.nan)
        older_sum = np.where(np.isfinite(older).all(axis=0), older.sum(axis=0), np.nan)
        total_1 = _lookup(total, id_series, sources["lag_1"])
        total_13 = _lookup(total, id_series, sources["lag_13"])
        region = identity.loc[ids, "region"].to_numpy()
        region_yoy = np.full(len(ids), np.nan)
        for name in REGIONS:
            members = np.flatnonzero(region == name)
            if len(members) < 2:
                continue
            own_1 = total_1[members]
            own_13 = total_13[members]
            sum_1 = np.nansum(own_1)
            sum_13 = np.nansum(own_13)
            for index, position in enumerate(members):
                other_1 = sum_1 - (own_1[index] if np.isfinite(own_1[index]) else 0.0)
                other_13 = sum_13 - (own_13[index] if np.isfinite(own_13[index]) else 0.0)
                # If the province itself was missing, the regional sum is not a full leave-one-out.
                if not np.isfinite(own_1[index]) or not np.isfinite(own_13[index]):
                    continue
                if other_1 > 0 and other_13 > 0:
                    region_yoy[position] = np.log(other_1) - np.log(other_13)
        cal = calendar.loc[month]
        province_flag = np.zeros(len(ids), dtype=float)
        calendar_month = int(month[5:7])
        for festival in PROVINCE_FESTIVALS:
            if festival["month"] == calendar_month:
                province_flag[ids == festival["province_id"]] = 1.0
        if int(cal["candle_month"]) == 1:
            province_flag[ids == CANDLE_PROVINCE] = 1.0
        frames.append(
            pd.DataFrame(
                {
                    "province_id": ids.to_numpy(),
                    "year_month": month,
                    "region": region,
                    "thai_lag_1": thai_1,
                    "thai_lag_12": thai_12,
                    "thai_yoy": _safe_ratio(thai_1, thai_13),
                    "foreign_lag_1": foreign_1,
                    "foreign_lag_12": foreign_12,
                    "foreign_yoy": _safe_ratio(foreign_1, foreign_13),
                    "yoy_3m_log": _safe_log_ratio(recent_sum, older_sum),
                    "region_yoy_log": region_yoy,
                    "holiday_count": int(cal["holiday_count"]),
                    "long_weekend_count": int(cal["long_weekend_count"]),
                    "festival_songkran": int(cal["festival_songkran"]),
                    "festival_new_year": int(cal["festival_new_year"]),
                    "festival_loy": int(cal["festival_loy"]),
                    "festival_cny": int(cal["festival_cny"]),
                    "festival_province": province_flag,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)
