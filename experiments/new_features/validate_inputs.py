"""Schema and leakage checks before new features are allowed into a model.

Hard failures raise ``FeatureValidationError``. Warm-up gaps and zero
denominators are warnings. The model may median-fill those warnings from the
training rows only; it must not see an inf.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from experiments.new_features.features import (
    FEATURE_GROUPS,
    REGION_READY,
    REGIONS,
    SPLIT_READY,
    YOY3_READY,
)
from experiments.new_features.calendar_data import CALENDAR_END, CALENDAR_START, calendar_month_table

FLAG_COLUMNS = (
    "festival_songkran",
    "festival_new_year",
    "festival_loy",
    "festival_cny",
    "festival_province",
)


class FeatureValidationError(RuntimeError):
    def __init__(self, message: str, report: dict):
        super().__init__(message)
        self.report = report


def _check(name: str, passed: bool, detail: str, *, severity: str = "hard") -> dict:
    return {"name": name, "severity": severity, "passed": bool(passed), "detail": str(detail)}


def _months(start: str, end: str) -> list[str]:
    return list(pd.period_range(start, end, freq="M").strftime("%Y-%m"))


def _finite_rate(frame: pd.DataFrame, columns: tuple[str, ...], start: str) -> tuple[int, int]:
    part = frame.loc[frame["year_month"] >= start, list(columns)]
    values = part.to_numpy(dtype=float)
    return int(np.size(values)), int(np.isfinite(values).sum())


def validate_feature_inputs(panel: pd.DataFrame, features: pd.DataFrame) -> dict:
    checks = []
    work = panel.copy()
    work["province_id"] = work["province_id"].astype(str).str.zfill(2)
    work["year_month"] = work["year_month"].astype(str)
    features = features.copy()
    features["province_id"] = features["province_id"].astype(str).str.zfill(2)
    features["year_month"] = features["year_month"].astype(str)

    required_feature = ["province_id", "year_month", "region", *sum(FEATURE_GROUPS.values(), ())]
    missing_cols = [column for column in required_feature if column not in features.columns]
    checks.append(_check("schema_columns", not missing_cols, ",".join(missing_cols) or "ok"))
    if missing_cols:
        return _finish(checks, {})

    numeric = [column for column in required_feature if column not in ("province_id", "year_month", "region")]
    bad_dtype = [column for column in numeric if not np.issubdtype(features[column].dtype, np.number)]
    checks.append(_check("schema_dtypes", not bad_dtype, ",".join(bad_dtype) or "ok"))

    duplicate = int(features.duplicated(["province_id", "year_month"]).sum())
    checks.append(_check("unique_province_month", duplicate == 0, f"duplicates={duplicate}"))

    regions = set(features["region"].dropna().unique())
    bad_region = sorted(regions - set(REGIONS))
    unmapped = int(features["region"].isna().sum() + features["province_id"].nunique() - 77)
    # province coverage is checked separately; region labels must be the six tourism regions.
    checks.append(_check("region_labels", not bad_region, ",".join(bad_region) or "ok"))
    provinces = set(features["province_id"].unique())
    checks.append(_check("province_count", len(provinces) == 77, f"n={len(provinces)}"))
    missing_region = sorted(features.loc[features["region"].isna(), "province_id"].unique())
    checks.append(_check("all_provinces_mapped", not missing_region, ",".join(missing_region) or "ok"))

    expected = _months("2019-01", "2025-12")
    have = set(features["year_month"])
    missing_months = [month for month in expected if month not in have]
    checks.append(_check("feature_month_coverage", not missing_months, ",".join(missing_months) or "ok"))

    calendar = calendar_month_table()
    calendar_expected = _months(CALENDAR_START, CALENDAR_END)
    calendar_have = set(calendar["year_month"])
    calendar_missing = [month for month in calendar_expected if month not in calendar_have]
    checks.append(_check("calendar_coverage_2018_2025", not calendar_missing, ",".join(calendar_missing) or "ok"))

    values = features[numeric].to_numpy(dtype=float)
    n_inf = int(np.isinf(values).sum())
    checks.append(_check("no_inf", n_inf == 0, f"inf={n_inf}"))

    for column in ("holiday_count", "long_weekend_count"):
        series = features[column].to_numpy(dtype=float)
        ok = np.isfinite(series) & (series >= 0) & (series <= 31)
        checks.append(_check(f"range_{column}", bool(ok.all()), f"outside={int((~ok).sum())}"))
    for column in FLAG_COLUMNS:
        series = features[column].to_numpy(dtype=float)
        ok = np.isfinite(series) & np.isin(series, (0.0, 1.0))
        checks.append(_check(f"flag_{column}", bool(ok.all()), f"outside={int((~ok).sum())}"))

    warmup_notes = {
        "yoy_3m_log": f"NaN allowed before {YOY3_READY} because lag 15 starts at 2018-01",
        "split_lags": f"NaN allowed before {SPLIT_READY} because Thai/foreign arrivals start in 2019-01",
        "region_yoy_log": f"NaN allowed before {REGION_READY} because lag 13 starts at 2018-01",
        "ratio_fill": "After warm-up, a NaN ratio is a zero denominator. It is not inf. Training median-fills it from rows in the fit window.",
    }
    yoy_part = features.loc[features["year_month"] >= YOY3_READY, "yoy_3m_log"].to_numpy(dtype=float)
    # A missing 3-month log after warm-up is only legal when a component sum is not positive.
    # The builder already returns NaN in that case and never inf. Count it as a warning.
    yoy_nan = int((~np.isfinite(yoy_part)).sum())
    checks.append(_check("yoy3_defined_after_warmup", yoy_nan == 0, f"nan={yoy_nan}", severity="warn"))
    for column in ("thai_yoy", "foreign_yoy", "region_yoy_log"):
        ready = SPLIT_READY if column.endswith("yoy") and column != "region_yoy_log" else REGION_READY if column == "region_yoy_log" else SPLIT_READY
        if column == "region_yoy_log":
            ready = REGION_READY
        part = features.loc[features["year_month"] >= ready, column].to_numpy(dtype=float)
        n_nan = int((~np.isfinite(part)).sum())
        checks.append(_check(f"{column}_after_warmup", n_nan == 0, f"nan={n_nan}", severity="warn"))

    published = work.loc[work["year_month"] >= "2019-01"]
    both = published["guest_arrivals_thai"].notna() & published["guest_arrivals_foreign"].notna()
    gap = (
        published.loc[both, "guest_arrivals_thai"]
        + published.loc[both, "guest_arrivals_foreign"]
        - published.loc[both, "guest_arrivals_total"]
    ).abs()
    limit = np.maximum(1.0, 0.001 * published.loc[both, "guest_arrivals_total"].abs())
    mismatch = gap > limit
    examples = []
    if bool(mismatch.any()):
        sample = published.loc[both].loc[mismatch].head(5)
        for row in sample.itertuples(index=False):
            examples.append(
                {
                    "province_id": str(row.province_id),
                    "year_month": str(row.year_month),
                    "total": float(row.guest_arrivals_total),
                    "thai": float(row.guest_arrivals_thai),
                    "foreign": float(row.guest_arrivals_foreign),
                }
            )
    checks.append(
        _check(
            "thai_plus_foreign_vs_total",
            int(mismatch.sum()) == 0,
            f"mismatches={int(mismatch.sum())} of {int(both.sum())}",
            severity="warn",
        )
    )
    n_cells, n_finite = _finite_rate(features, ("thai_lag_1", "foreign_lag_1"), SPLIT_READY)
    return _finish(
        checks,
        {
            "warmup": warmup_notes,
            "thai_foreign_mismatches": int(mismatch.sum()),
            "thai_foreign_examples": examples,
            "split_lag1_finite_after_warmup": {"cells": n_cells, "finite": n_finite},
        },
    )


def _finish(checks: list[dict], extra: dict) -> dict:
    hard = [item for item in checks if item["severity"] == "hard" and not item["passed"]]
    warnings = [item for item in checks if item["severity"] == "warn" and not item["passed"]]
    report = {
        "hard_fail": bool(hard),
        "n_hard": len(hard),
        "n_warnings": len(warnings),
        "checks": checks,
        **extra,
    }
    if hard:
        names = ", ".join(item["name"] for item in hard)
        raise FeatureValidationError(f"feature validation failed: {names}", report)
    return report


def write_validation_report(report: dict, path: Path) -> None:
    import json

    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
