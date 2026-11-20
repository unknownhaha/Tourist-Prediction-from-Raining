"""Reference window 2023-01..2024-12 for input-drift comparison.

COVID months are outside this window on purpose. A drift alarm must not
rewrite these files.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.common.calendar import month_range
from src.model.leakage import TIME_SPLIT, build_forecast_features

REFERENCE_START = TIME_SPLIT["validation"][0]
REFERENCE_END = TIME_SPLIT["test"][1]
REFERENCE_VERSION = "2023-2024-v1"
STAT_COLUMNS = (
    "guest_arrivals_total",
    "rain_avg_mm",
    "guest_lag_1",
    "guest_lag_12",
    "rain_lag_1",
    "rain_roll3",
)


def _quantile_stats(values: pd.Series) -> dict:
    array = values.to_numpy(dtype=float)
    array = array[np.isfinite(array)]
    if len(array) == 0:
        raise ValueError(f"reference column {values.name} has no finite values")
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    return {
        "median": median,
        "mad": mad,
        "q05": float(np.quantile(array, 0.05)),
        "q50": float(np.quantile(array, 0.50)),
        "q95": float(np.quantile(array, 0.95)),
    }


def build_reference(panel: pd.DataFrame, source_hash: str) -> tuple[pd.DataFrame, dict]:
    """Row-level reference plus a versioned stats document."""
    months = month_range(REFERENCE_START, REFERENCE_END)
    pieces: list[pd.DataFrame] = []
    for year_month in months:
        features = build_forecast_features(panel.loc[panel["year_month"] <= year_month], year_month)
        actual = panel.loc[
            panel["year_month"] == year_month,
            ["province_id", "guest_arrivals_total", "rain_avg_mm"],
        ].copy()
        actual["province_id"] = actual["province_id"].astype(str)
        features = features.copy()
        features["province_id"] = features["province_id"].astype(str)
        merged = features.merge(actual, on="province_id", how="inner", validate="one_to_one")
        if len(merged) != actual["province_id"].nunique() or actual.empty:
            raise ValueError(f"reference month {year_month} is missing from the snapshot")
        merged.insert(1, "year_month", year_month)
        pieces.append(merged)
    reference = pd.concat(pieces, ignore_index=True)
    reference = reference.sort_values(["year_month", "province_id"]).reset_index(drop=True)
    if reference["year_month"].min() < "2023-01" or reference["year_month"].max() > "2024-12":
        raise ValueError("reference window left 2023-01..2024-12")
    covid = (reference["year_month"] >= "2020-03") & (reference["year_month"] <= "2022-12")
    if bool(covid.any()):
        raise ValueError("reference set must not include the COVID window")

    per_column = {column: _quantile_stats(reference[column]) for column in STAT_COLUMNS}
    stats = {
        "version": REFERENCE_VERSION,
        "window": {"start": REFERENCE_START, "end": REFERENCE_END},
        "n_rows": int(len(reference)),
        "n_provinces": int(reference["province_id"].nunique()),
        "source_hash": source_hash,
        "excludes_covid_window": True,
        "refreshed_by": "team decision only",
        "columns": per_column,
    }
    return reference, stats
