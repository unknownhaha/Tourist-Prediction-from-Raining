"""Forecast features that are allowed to read only months strictly before t.

The modeling table is one row per province × month. A forecast of month t may
use lags and rolling windows that end at t-1. Same-month values, and any
aggregate that includes t, are leakage. `docs/feature_rules_for_modeling.md`
is the rule sheet this module enforces.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

import pandas as pd

from src.common.calendar import month_range
from src.common.calendar import year_month as format_year_month

DEFAULT_LAGS: tuple[int, ...] = (1, 3, 12)
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

    History that does not exist yet (lag 12 of 2018-01, for example) stays
    null. The value at `target_month` is never copied into a feature.
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
