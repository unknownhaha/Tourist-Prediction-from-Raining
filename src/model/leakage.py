"""Forecast features that read only months strictly before t.

The documented set is guest-arrival lags 1 and 12, rain lag 1, and a
3-month rain mean ending at t-1, plus the calendar flags `is_covid` and
`is_covid_lag12`. Same-month guest arrivals, rain, and visitors are leakage.
`docs/feature_rules_for_modeling.md` is the rule sheet this module enforces.

Lags are computed from the unified panel at feature-build time. They are not
stored as a separate table. Pass the full panel, including 2018 and COVID
rows, and drop rows only after the lags exist.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

import pandas as pd

from src.common.calendar import is_covid as calendar_is_covid
from src.common.calendar import month_range
from src.common.calendar import year_month as format_year_month

GUEST_LAGS: tuple[int, ...] = (1, 12)
RAIN_ROLLING_MONTHS = 3
DEFAULT_LAGS = GUEST_LAGS

# Single time split. 2018 is lag history only and is not a training row.
TIME_SPLIT: dict[str, tuple[str, str]] = {
    "lag_history": ("2018-01", "2018-12"),
    "train": ("2019-01", "2022-12"),
    "validation": ("2023-01", "2023-12"),
    "online_stream": ("2024-01", "2025-12"),
}

_YEAR_MONTH = re.compile(r"\d{4}-\d{2}$")
_QUARTER = re.compile(r"\d{4}-Q[1-4]$")


def _require_year_month(value: str) -> str:
    if not isinstance(value, str) or _YEAR_MONTH.fullmatch(value) is None:
        raise ValueError(f"expected YYYY-MM, got {value!r}")
    month = int(value[5:7])
    if not 1 <= month <= 12:
        raise ValueError(f"expected YYYY-MM, got {value!r}")
    return value


def shift_year_month(year_month: str, lag: int) -> str:
    """Return the month `lag` steps before `year_month`. `lag` must be >= 1."""
    _require_year_month(year_month)
    if lag < 1:
        raise ValueError("lag must be >= 1 so the source month is before the target")
    year, month = map(int, year_month.split("-"))
    index = year * 12 + (month - 1) - lag
    return format_year_month(index // 12, index % 12 + 1)


def assert_no_future_leakage(feature_months: Iterable[str], target_month: str) -> None:
    """Raise if any feature month is `target_month` or later.

    Same-month rain, visitors, and the Thai/foreign split of t count as
    leakage. Pass the YYYY-MM months a forecast of `target_month` would read.
    `is_covid` of t is a calendar flag and is not one of these months.
    """
    target = _require_year_month(target_month)
    months = [_require_year_month(month) for month in feature_months]
    leaked = sorted({month for month in months if month >= target})
    if leaked:
        raise AssertionError(
            f"leakage into {target}: feature months {leaked} are not strictly before the target"
        )


def source_months_for_lags(
    target_month: str,
    lags: Sequence[int] = DEFAULT_LAGS,
) -> dict[str, str]:
    """Map `lag_<k>` to the month a forecast of `target_month` may read."""
    sources = {f"lag_{lag}": shift_year_month(target_month, lag) for lag in lags}
    assert_no_future_leakage(sources.values(), target_month)
    return sources


def rolling_source_months(target_month: str, window: int) -> list[str]:
    """The `window` months ending at t-1, oldest first. Never includes t."""
    if window < 1:
        raise ValueError("window must be >= 1")
    start = shift_year_month(target_month, window)
    end = shift_year_month(target_month, 1)
    months = month_range(start, end)
    assert_no_future_leakage(months, target_month)
    return months


def forecast_source_months(target_month: str) -> dict[str, str]:
    """Observed months read by the documented forecast features. All are < t."""
    roll = rolling_source_months(target_month, RAIN_ROLLING_MONTHS)
    sources = {
        "guest_lag_1": shift_year_month(target_month, 1),
        "guest_lag_12": shift_year_month(target_month, 12),
        "rain_lag_1": shift_year_month(target_month, 1),
        "is_covid_lag12": shift_year_month(target_month, 12),
        "rain_roll3_m3": roll[0],
        "rain_roll3_m2": roll[1],
        "rain_roll3_m1": roll[2],
    }
    assert_no_future_leakage(sources.values(), target_month)
    return sources


def quarter_label(year_month: str) -> str:
    """Calendar quarter that contains `year_month`, such as `2024-Q2`."""
    year, month = _require_year_month(year_month).split("-")
    quarter = (int(month) - 1) // 3 + 1
    return f"{year}-Q{quarter}"


def quarter_last_month(quarter: str) -> str:
    if not isinstance(quarter, str) or _QUARTER.fullmatch(quarter) is None:
        raise ValueError(f"expected YYYY-Qq, got {quarter!r}")
    year, q = quarter.split("-Q")
    return f"{int(year):04d}-{int(q) * 3:02d}"


def quarter_allowed_as_of(quarter: str, target_month: str) -> bool:
    """True only when the quarter ends strictly before `target_month`.

    The quarter that contains t is never allowed: its total includes t, and
    any later month in that quarter is still in the future.
    """
    return quarter_last_month(quarter) < _require_year_month(target_month)


def build_allowed_lag_frame(
    panel: pd.DataFrame,
    target_month: str,
    *,
    value_col: str = "guest_arrivals_total",
    lags: Sequence[int] = DEFAULT_LAGS,
    province_col: str = "province_id",
    month_col: str = "year_month",
) -> pd.DataFrame:
    """One row per province with lag columns taken only from months before t.

    History that does not exist yet stays null. The value at `target_month`
    is never copied into a feature. Default lags are the guest lags 1 and 12.
    """
    sources = source_months_for_lags(target_month, lags)
    assert_no_future_leakage(sources.values(), target_month)
    if panel.duplicated([province_col, month_col]).any():
        raise ValueError("panel has duplicate province-month keys")

    provinces = pd.Index(panel[province_col].drop_duplicates(), name=province_col)
    needed = list(dict.fromkeys(sources.values()))
    hist = panel.loc[panel[month_col].isin(needed), [province_col, month_col, value_col]]
    wide = hist.pivot(index=province_col, columns=month_col, values=value_col).reindex(provinces)

    out = pd.DataFrame({province_col: provinces.astype(panel[province_col].dtype)})
    for name, month in sources.items():
        if month in wide.columns:
            out[name] = wide[month].to_numpy()
        else:
            out[name] = pd.NA
    return out.reset_index(drop=True)


def build_forecast_features(
    panel: pd.DataFrame,
    target_month: str,
    *,
    province_col: str = "province_id",
    month_col: str = "year_month",
) -> pd.DataFrame:
    """Documented forecast features for month t, built from the full panel.

    Columns: `guest_lag_1`, `guest_lag_12`, `rain_lag_1`, `rain_roll3`,
    `is_covid`, `is_covid_lag12`. `rain_roll3` is the mean of rain at t-3, t-2,
    and t-1, and is null if any of those months is missing. `is_covid` is the
    calendar flag of t. `is_covid_lag12` is the calendar flag of t-12.

    Compute this on the unified table before dropping any rows. Dropping
    COVID rows first makes lag 1 of 2023-01 skip back to 2020-02.
    """
    target = _require_year_month(target_month)
    required = ["guest_arrivals_total", "rain_avg_mm"]
    missing = [column for column in required if column not in panel.columns]
    if missing:
        raise ValueError(f"panel missing columns: {missing}")
    if panel.duplicated([province_col, month_col]).any():
        raise ValueError("panel has duplicate province-month keys")

    sources = forecast_source_months(target)
    roll_months = [sources["rain_roll3_m3"], sources["rain_roll3_m2"], sources["rain_roll3_m1"]]
    assert_no_future_leakage(sources.values(), target)

    provinces = pd.Index(panel[province_col].drop_duplicates(), name=province_col)
    needed = set(sources.values())
    hist = panel.loc[
        panel[month_col].isin(needed),
        [province_col, month_col, "guest_arrivals_total", "rain_avg_mm"],
    ]
    guest_wide = (
        hist.pivot(index=province_col, columns=month_col, values="guest_arrivals_total").reindex(provinces)
    )
    rain_wide = hist.pivot(index=province_col, columns=month_col, values="rain_avg_mm").reindex(provinces)

    def _at(wide: pd.DataFrame, month: str) -> pd.Series:
        if month in wide.columns:
            return wide[month]
        return pd.Series(pd.NA, index=wide.index, dtype="Float64")

    rain_roll = rain_wide.reindex(columns=roll_months).mean(axis=1, skipna=False)
    out = pd.DataFrame(
        {
            province_col: provinces.astype(panel[province_col].dtype),
            "guest_lag_1": _at(guest_wide, sources["guest_lag_1"]).to_numpy(),
            "guest_lag_12": _at(guest_wide, sources["guest_lag_12"]).to_numpy(),
            "rain_lag_1": _at(rain_wide, sources["rain_lag_1"]).to_numpy(),
            "rain_roll3": rain_roll.to_numpy(),
            "is_covid": calendar_is_covid(target),
            "is_covid_lag12": calendar_is_covid(sources["is_covid_lag12"]),
        }
    )
    return out.reset_index(drop=True)
