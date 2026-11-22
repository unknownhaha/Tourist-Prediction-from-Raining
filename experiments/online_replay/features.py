"""Leak-free seasonal-adjusted features for the 2025 online replay.

Port of the data-review prototype (``ab.py::add_features``/``prof_for``) as tested code.

* ``normal_years``: complete calendar years with no COVID-window month and all months
  present for every province (derived from the panel and the configured COVID window).
* The seasonal profile and the recovery baseline for a row in year Y use only the last
  two normal years strictly before Y (2023 -> 2018+2019, 2024 -> 2019+2023,
  2025 -> 2023+2024). No row ever sees its own year.
* Lags (t-1, t-2, t-3, t-12) come from the backtest table, so month t's label is never read.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.calendar import COVID_END, COVID_START
from src.model.leakage import shift_year_month

HERE = Path(__file__).resolve().parent
EXP_COLUMNS = ("exp_lr_recent", "exp_lr3_recent")
REC_COLUMNS = ("rec_lag1", "rec3", "rec_lag12", "nat_rec_lag1", "nat_rec_lag12")
EVENT_COLUMNS = ("event_now", "event_lag1")


def covid_window() -> tuple[str, str]:
    """The project COVID window (config/sources.yaml, mirrored in src.common.calendar)."""
    return COVID_START, COVID_END


def guest_wide(panel: pd.DataFrame) -> pd.DataFrame:
    work = panel.copy()
    work["province_id"] = work["province_id"].astype(str).str.zfill(2)
    work["year_month"] = work["year_month"].astype(str)
    return work.pivot(index="province_id", columns="year_month", values="guest_arrivals_total")


def normal_years(panel: pd.DataFrame, window: tuple[str, str] | None = None) -> list[int]:
    start, end = window or covid_window()
    wide = guest_wide(panel)
    years = sorted({int(c[:4]) for c in wide.columns})
    out = []
    for year in years:
        months = [f"{year}-{m:02d}" for m in range(1, 13)]
        if any(start <= m <= end for m in months):
            continue
        if not all(m in wide.columns for m in months):
            continue
        if not np.isfinite(wide[months].to_numpy(dtype=float)).all():
            continue
        out.append(year)
    return out


def profile_years(target_year: int, normal: list[int]) -> list[int]:
    """Last two normal years strictly before target_year."""
    return [y for y in normal if y < target_year][-2:]


def seasonal_levels(wide: pd.DataFrame, years: list[int]) -> np.ndarray:
    """(n_province, 12) mean arrivals per calendar month over ``years``."""
    if not years:
        return np.full((len(wide), 12), np.nan)
    stack = [wide[[f"{y}-{m:02d}" for m in range(1, 13)]].to_numpy(dtype=float) for y in years]
    return np.mean(stack, axis=0)


def load_events(path: Path | None = None) -> list[dict]:
    return json.loads((path or HERE / "events.json").read_text(encoding="utf-8"))["events"]


def _in_event(province_ids: np.ndarray, months: np.ndarray, events: list[dict]) -> np.ndarray:
    out = np.zeros(len(months))
    for event in events:
        hit = (province_ids == str(event["province_id"]).zfill(2)) & (months >= event["start"]) & (months <= event["end"])
        out[hit] = 1.0
    return out


def add_features(table: pd.DataFrame, wide: pd.DataFrame, normal: list[int], events: list[dict] | None = None) -> pd.DataFrame:
    """Add exp_lr_recent, exp_lr3_recent, seasonal_last_month_forecast, rec_*, nat_rec_*, event_*.

    ``wide`` is read only for the seasonal profile of years < row year; every other input is a
    lag column of ``table``.
    """
    t = table.copy()
    ids = t["province_id"].astype(str).str.zfill(2).to_numpy()
    c = t["calendar_month"].to_numpy(dtype=int)
    c1 = np.where(c == 1, 12, c - 1)
    c2 = np.where(c1 == 1, 12, c1 - 1)
    c3 = np.where(c2 == 1, 12, c2 - 1)
    yrs = t["year_month"].str[:4].astype(int).to_numpy()
    l1, l2, l3, l12 = (t[f"guest_lag_{k}"].to_numpy(dtype=float) for k in (1, 2, 3, 12))
    # National lags from the table's own lag columns (sum over provinces of t-1 / t-12).
    nat1 = t.groupby("year_month")["guest_lag_1"].transform("sum").to_numpy(dtype=float)
    nat12 = t.groupby("year_month")["guest_lag_12"].transform("sum").to_numpy(dtype=float)
    gi = wide.index.get_indexer(ids)
    if (gi < 0).any():
        raise KeyError("province missing from the guest panel")
    cols = {name: np.full(len(t), np.nan) for name in
            ("exp_lr_recent", "exp_lr3_recent", "seasonal_last_month_forecast", *REC_COLUMNS)}
    used = {}
    with np.errstate(all="ignore"):
        for year in np.unique(yrs):
            ys = profile_years(int(year), normal)
            used[int(year)] = ys
            L = seasonal_levels(wide, ys)
            NL = np.nansum(L, axis=0) if ys else np.full(12, np.nan)
            m = yrs == year
            g = gi[m]
            s, s1, s2, s3 = (L[g, cc[m] - 1] for cc in (c, c1, c2, c3))
            cols["seasonal_last_month_forecast"][m] = l1[m] * s / s1
            cols["exp_lr_recent"][m] = np.log(np.clip(l1[m] * s / s1, 1, None)) - np.log(np.clip(l12[m], 1, None))
            cols["exp_lr3_recent"][m] = (np.log(np.clip((l1[m] + l2[m] + l3[m]) * s / (s1 + s2 + s3), 1, None))
                                         - np.log(np.clip(l12[m], 1, None)))
            cols["rec_lag1"][m] = l1[m] / s1
            cols["rec3"][m] = (l1[m] + l2[m] + l3[m]) / (s1 + s2 + s3)
            cols["rec_lag12"][m] = l12[m] / s
            cols["nat_rec_lag1"][m] = nat1[m] / NL[c1[m] - 1]
            cols["nat_rec_lag12"][m] = nat12[m] / NL[c[m] - 1]
    for name, values in cols.items():
        t[name] = values
    months = t["year_month"].astype(str).to_numpy()
    events = load_events() if events is None else events
    t["event_now"] = _in_event(ids, months, events)
    prev = np.array([shift_year_month(m, 1) for m in months])
    t["event_lag1"] = _in_event(ids, prev, events)
    t.attrs["profile_years"] = used
    return t
