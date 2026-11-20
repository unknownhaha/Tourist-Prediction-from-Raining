"""Hard checks for one replay month. Failures halt; they do not impute."""

from __future__ import annotations

import re

import pandas as pd

from src.common.calendar import is_covid as calendar_is_covid

N_PROVINCES = 77
YEAR_MONTH = re.compile(r"\d{4}-\d{2}$")
PROVINCE_ID = re.compile(r"\d{2}$")

REQUIRED_COLUMNS = (
    "province_id",
    "year_month",
    "guest_arrivals_total",
    "guest_arrivals_thai",
    "guest_arrivals_foreign",
    "rain_avg_mm",
    "rain_min_mm",
    "rain_max_mm",
    "is_covid",
)
FEATURE_COLUMNS = (
    "guest_lag_1",
    "guest_lag_12",
    "rain_lag_1",
    "rain_roll3",
)


class StreamHalt(Exception):
    """Stop the replay. Do not predict, learn, or impute.

    A halted run keeps the DQ report and lineage for the failing month and
    does not write predictions or metrics.
    """

    def __init__(self, message: str, checks: list[dict] | None = None, year_month: str | None = None):
        super().__init__(message)
        self.checks = checks or []
        self.year_month = year_month


def _check(name: str, passed: bool, detail: str = "", *, severity: str = "hard") -> dict:
    return {"name": name, "severity": severity, "passed": bool(passed), "detail": str(detail)}


def _raise_if_hard(checks: list[dict], year_month: str, stage: str) -> list[dict]:
    failed = [item for item in checks if item["severity"] == "hard" and not item["passed"]]
    if failed:
        names = ", ".join(item["name"] for item in failed)
        raise StreamHalt(f"{stage} failed for {year_month}: {names}", checks, year_month=year_month)
    return checks


def _pad_candidate(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if PROVINCE_ID.fullmatch(text):
            return text
        if text.isdigit() and 1 <= len(text) <= 2:
            return f"{int(text):02d}"
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if float(value).is_integer() and 0 <= int(value) <= 99:
            return f"{int(value):02d}"
    return None


def _is_bool_like(value: object) -> bool:
    if isinstance(value, (bool, int)) and not isinstance(value, bool):
        return int(value) in (0, 1)
    return isinstance(value, (bool, int)) and (isinstance(value, bool) or int(value) in (0, 1))


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return bool(int(value))


def validate_working_set(frame: pd.DataFrame, year_month: str) -> list[dict]:
    """Reject rows after `year_month` in the frame used to build this month."""
    if "year_month" not in frame.columns:
        raise StreamHalt(
            f"working set for {year_month} has no year_month column",
            year_month=year_month,
        )
    future = frame.loc[frame["year_month"].astype(str) > year_month]
    checks = [
        _check(
            "no_future_months",
            future.empty,
            f"{len(future)} rows after {year_month}",
        )
    ]
    return _raise_if_hard(checks, year_month, "validate")


def validate_month(frame: pd.DataFrame, year_month: str, *, stage: str) -> list[dict]:
    """Hard-fail the month slice. Call again after cleaning."""
    checks: list[dict] = []
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    checks.append(_check("columns", not missing, ",".join(missing) or "ok"))
    if missing:
        return _raise_if_hard(checks, year_month, stage)

    month = frame.loc[frame["year_month"].astype(str) == year_month].copy()
    bad_labels = [
        value
        for value in month["year_month"].astype(str).unique()
        if YEAR_MONTH.fullmatch(value) is None or not 1 <= int(value[5:7]) <= 12
    ]
    checks.append(_check("year_month_format", not bad_labels, ",".join(map(str, bad_labels)) or "ok"))

    padded = month["province_id"].map(_pad_candidate)
    bad_ids = int(padded.isna().sum())
    checks.append(_check("province_id", bad_ids == 0, f"{bad_ids} values cannot be a 2-digit id"))
    strict = stage in {"re-validate", "history"}
    if strict:
        as_text = month["province_id"].astype(str)
        not_padded = int((~as_text.map(lambda value: PROVINCE_ID.fullmatch(value) is not None)).sum())
        checks.append(_check("province_id_padded", not_padded == 0, f"{not_padded} ids are not \\d{{2}}"))

    keys = month.assign(_pid=padded)
    conflict = False
    if keys["_pid"].notna().all() and not keys.empty:
        comparable = [column for column in REQUIRED_COLUMNS if column != "province_id"]
        grouped = keys.groupby("_pid", dropna=False)
        for _, part in grouped:
            distinct = part.drop_duplicates(subset=comparable)
            if len(distinct) > 1:
                conflict = True
                break
    checks.append(_check("duplicate_keys", not conflict, "conflicting values for one province-month"))

    unique_ready = int(padded.dropna().nunique()) if not month.empty else 0
    one_month = int(month["year_month"].nunique()) == 1 if not month.empty else False
    if strict:
        count_ok = (not conflict) and unique_ready == N_PROVINCES and len(month) == N_PROVINCES and one_month
    else:
        count_ok = (not conflict) and unique_ready == N_PROVINCES and one_month
    checks.append(
        _check(
            "row_count",
            count_ok,
            f"rows={len(month)} unique_provinces={unique_ready}",
        )
    )

    for column in ("guest_arrivals_total", "rain_avg_mm", "rain_min_mm", "rain_max_mm"):
        numeric = pd.to_numeric(month[column], errors="coerce")
        became_null = int((month[column].notna() & numeric.isna()).sum())
        checks.append(_check(f"{column}_numeric", became_null == 0, f"{became_null} non-numeric"))
        negative = int((numeric.dropna() < 0).sum())
        checks.append(_check(f"{column}_non_negative", negative == 0, f"{negative} negative"))
        if strict:
            nulls = int(numeric.isna().sum())
            checks.append(_check(f"{column}_not_null", nulls == 0, f"{nulls} null"))

    rain = month[["rain_min_mm", "rain_avg_mm", "rain_max_mm"]].apply(pd.to_numeric, errors="coerce")
    present = rain.notna().all(axis=1)
    ordered = present & (rain["rain_min_mm"] <= rain["rain_avg_mm"]) & (
        rain["rain_avg_mm"] <= rain["rain_max_mm"]
    )
    bad_order = int((present & ~ordered).sum())
    checks.append(_check("rain_order", bad_order == 0, f"{bad_order} rows with rain_min > avg or avg > max"))

    expected = calendar_is_covid(year_month)
    mismatched = 0
    for value in month["is_covid"]:
        if pd.isna(value) or not _is_bool_like(value) or _as_bool(value) is not expected:
            mismatched += 1
    checks.append(_check("is_covid_calendar", mismatched == 0, f"{mismatched} rows disagree with the calendar"))

    return _raise_if_hard(checks, year_month, stage)


def validate_features(features: pd.DataFrame, year_month: str) -> list[dict]:
    """The frame passed to predict has 77 provinces and no null lags."""
    checks = [
        _check("feature_rows", len(features) == N_PROVINCES, f"rows={len(features)}"),
        _check(
            "feature_provinces",
            features["province_id"].astype(str).nunique() == N_PROVINCES,
            "province_id not unique",
        ),
    ]
    nulls = int(features.loc[:, list(FEATURE_COLUMNS)].isna().sum().sum())
    checks.append(_check("feature_lags_not_null", nulls == 0, f"{nulls} null lag cells"))
    expected = calendar_is_covid(year_month)
    covid_ok = bool((features["is_covid"].map(lambda value: bool(value) is expected)).all())
    checks.append(_check("feature_is_covid", covid_ok, f"expected {expected}"))
    if "is_covid_lag12" in features.columns and year_month >= "2025-01":
        lag_ok = bool((~features["is_covid_lag12"].map(bool)).all())
        checks.append(_check("feature_is_covid_lag12", lag_ok, "2025 lag-12 must be outside COVID"))
    return _raise_if_hard(checks, year_month, "features")


def history_source_months(stream_months: list[str]) -> list[str]:
    """Lag sources that are never themselves a replay month.

    For 2025-01..2025-12 this is 2024-01..2024-12: every month
    `forecast_source_months` reads that falls before the stream.
    """
    from src.model.leakage import forecast_source_months

    start = min(stream_months)
    found: set[str] = set()
    for month in stream_months:
        found.update(source for source in forecast_source_months(month).values() if source < start)
    return sorted(found)


def validate_history(panel: pd.DataFrame, stream_months: list[str]) -> list[str]:
    """Apply the month-t value rules to every lag-source history month."""
    months = history_source_months(stream_months)
    for month in months:
        sliver = panel.loc[panel["year_month"].astype(str) == month]
        if sliver.empty:
            raise StreamHalt(
                f"history failed for {month}: row_count",
                [_check("row_count", False, "0 rows")],
                year_month=month,
            )
        validate_month(sliver, month, stage="history")
    return months
