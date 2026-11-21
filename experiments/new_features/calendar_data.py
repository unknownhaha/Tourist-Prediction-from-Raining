"""Calendar features known before the month begins.

Public-holiday dates, including substitution ("in lieu") days and bridge
holidays, come from the ``holidays`` package, Thailand calendar, version
0.109. That table cites the Holidays Act and later Royal Gazette amendments
(see ``holidays/countries/thailand.py``).

Loy Krathong is the full-moon night of the 12th Thai lunar month. Dates:
Wikipedia, "Loy Krathong", scheduled-date table (retrieved 2026-11-21).
2019-11-11 and 2020-10-31 also match Tourism Authority of Thailand newsroom
notices (31 Oct 2019 and 19 Oct 2020).

Chinese New Year is the first day of the Chinese lunar year. Dates:
Hong Kong Observatory Gregorian-lunar conversion tables, as listed by
Infoplease (updated 27 Dec 2021) and timeanddate.com for 2021-2025.

Province flags are month-level because local committees announce the exact
days late. Sources are the usual published months, not a day-by-day gazette:

- Chiang Mai Flower Festival, province 50, February
  (Tourism Authority of Thailand: first weekend of February).
- Ubon Ratchathani Candle Festival, province 34, the month of Asarnha Bucha
  (same public-holiday calendar).
- Surin Elephant Round-up, province 32, November
  (Tourism Authority of Thailand: third week of November).
- Phi Ta Khon, Dan Sai, Loei, province 42, June. The date sometimes slips
  into July; June is the usual month and is a coarse flag.

Songkran the festival is flagged for every April, including 2020. The
official public holidays for Songkran 2020 were moved out of April; those
moved days are in the holiday count, not in the April festival flag.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

import holidays
import pandas as pd

LOY_KRATHONG = {
    2018: dt.date(2018, 11, 22),
    2019: dt.date(2019, 11, 11),
    2020: dt.date(2020, 10, 31),
    2021: dt.date(2021, 11, 19),
    2022: dt.date(2022, 11, 8),
    2023: dt.date(2023, 11, 27),
    2024: dt.date(2024, 11, 15),
    2025: dt.date(2025, 11, 5),
}
CHINESE_NEW_YEAR = {
    2018: dt.date(2018, 2, 16),
    2019: dt.date(2019, 2, 5),
    2020: dt.date(2020, 1, 25),
    2021: dt.date(2021, 2, 12),
    2022: dt.date(2022, 2, 1),
    2023: dt.date(2023, 1, 22),
    2024: dt.date(2024, 2, 10),
    2025: dt.date(2025, 1, 29),
}
PROVINCE_FESTIVALS = (
    {"province_id": "50", "month": 2, "name": "chiang_mai_flower"},
    {"province_id": "32", "month": 11, "name": "surin_elephant"},
    {"province_id": "42", "month": 6, "name": "loei_phi_ta_khon"},
)
CANDLE_PROVINCE = "34"
CALENDAR_START = "2018-01"
CALENDAR_END = "2025-12"


@lru_cache(maxsize=1)
def thailand_holidays() -> holidays.HolidayBase:
    return holidays.country_holidays("TH", years=range(2018, 2026))


def _month_key(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def asarnha_bucha_month() -> dict[int, int]:
    """Calendar month of Asarnha Bucha itself, ignoring a later substitute day."""
    found = {}
    for day, name in thailand_holidays().items():
        if name.split(";")[0].strip() == "Asarnha Bucha":
            found[day.year] = day.month
    return found


def long_weekend_counts() -> dict[str, int]:
    """How many 3+ day breaks overlap each month.

    A break is a maximal run of Saturdays, Sundays, and public holidays.
    A run that crosses a month boundary counts in both months.
    """
    start = dt.date(2017, 12, 1)
    end = dt.date(2026, 1, 31)
    public = holidays.country_holidays("TH", years=range(2017, 2027))
    off = set()
    day = start
    while day <= end:
        if day.weekday() >= 5 or day in public:
            off.add(day)
        day += dt.timedelta(days=1)
    runs = []
    for day in sorted(off):
        if runs and day == runs[-1][-1] + dt.timedelta(days=1):
            runs[-1].append(day)
        else:
            runs.append([day])
    counts: dict[str, int] = {}
    for run in runs:
        if len(run) < 3:
            continue
        for key in {_month_key(item) for item in run}:
            if CALENDAR_START <= key <= CALENDAR_END:
                counts[key] = counts.get(key, 0) + 1
    return counts


def calendar_month_table() -> pd.DataFrame:
    """One row per month from 2018-01 through 2025-12."""
    public = thailand_holidays()
    holiday_counts: dict[str, int] = {}
    for day in public:
        key = _month_key(day)
        if CALENDAR_START <= key <= CALENDAR_END:
            holiday_counts[key] = holiday_counts.get(key, 0) + 1
    weekends = long_weekend_counts()
    candle = asarnha_bucha_month()
    rows = []
    year, month = 2018, 1
    while (year, month) <= (2025, 12):
        key = f"{year:04d}-{month:02d}"
        rows.append(
            {
                "year_month": key,
                "holiday_count": int(holiday_counts.get(key, 0)),
                "long_weekend_count": int(weekends.get(key, 0)),
                "festival_songkran": int(month == 4),
                "festival_new_year": int(month in (1, 12)),
                "festival_loy": int(any(_month_key(day) == key for day in LOY_KRATHONG.values())),
                "festival_cny": int(any(_month_key(day) == key for day in CHINESE_NEW_YEAR.values())),
                "candle_month": int(candle.get(year) == month),
            }
        )
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return pd.DataFrame(rows)
