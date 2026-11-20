"""Per-month data-quality report. Warnings keep the row. Hard fails are recorded too."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.stream.validate import N_PROVINCES

ROBUST_Z_LIMIT = 3.5
MOM_LIMIT = 1.0
FLAG_COLUMNS = ("province_id", "year_month", "check", "severity", "value", "detail")


def _mad_z(value: float, history: np.ndarray) -> float | None:
    history = history[np.isfinite(history)]
    if len(history) < 6:
        return None
    median = float(np.median(history))
    mad = float(np.median(np.abs(history - median)))
    if mad == 0:
        return None
    return (value - median) / (1.4826 * mad)


def assess_month(month: pd.DataFrame, history: pd.DataFrame, year_month: str) -> tuple[dict, pd.DataFrame]:
    """Score the cleaned month against history strictly before `year_month`."""
    flags: list[dict] = []
    checks: list[dict] = []
    checks.append(
        {
            "name": "row_count",
            "severity": "hard",
            "passed": len(month) == N_PROVINCES,
            "detail": str(len(month)),
        }
    )
    history = history.loc[history["year_month"].astype(str) < year_month]
    needed = _required_history_months(year_month)
    have = set(history["year_month"].astype(str))
    missing_months = sorted(month_ for month_ in needed if month_ not in have)
    checks.append(
        {
            "name": "history_complete",
            "severity": "hard",
            "passed": not missing_months,
            "detail": ",".join(missing_months) or "ok",
        }
    )

    for row in month.itertuples(index=False):
        province_id = str(row.province_id)
        past = history.loc[history["province_id"].astype(str) == province_id, "guest_arrivals_total"]
        value = float(row.guest_arrivals_total)
        score = _mad_z(value, past.to_numpy(dtype=float))
        if score is not None and abs(score) > ROBUST_Z_LIMIT:
            flags.append(
                {
                    "province_id": province_id,
                    "year_month": year_month,
                    "check": "robust_z",
                    "severity": "warn",
                    "value": score,
                    "detail": f"|z|={abs(score):.4f} > {ROBUST_Z_LIMIT}",
                }
            )
        if len(past):
            # History is not guaranteed sorted. Use the latest year_month.
            province_history = history.loc[history["province_id"].astype(str) == province_id]
            previous = province_history.sort_values("year_month").iloc[-1]
            previous_value = float(previous["guest_arrivals_total"])
            if previous_value > 0 and abs(value - previous_value) / previous_value > MOM_LIMIT:
                flags.append(
                    {
                        "province_id": province_id,
                        "year_month": year_month,
                        "check": "month_over_month",
                        "severity": "warn",
                        "value": (value - previous_value) / previous_value,
                        "detail": "absolute month-over-month change above 100%",
                    }
                )
        if value == 0:
            flags.append(
                {
                    "province_id": province_id,
                    "year_month": year_month,
                    "check": "zero_arrival_kept",
                    "severity": "warn",
                    "value": 0.0,
                    "detail": "published zero kept",
                }
            )
        thai = row.guest_arrivals_thai
        foreign = row.guest_arrivals_foreign
        if pd.notna(thai) and pd.notna(foreign):
            gap = abs(value - (float(thai) + float(foreign)))
            limit = max(1.0, 0.001 * abs(value))
            if gap > limit:
                flags.append(
                    {
                        "province_id": province_id,
                        "year_month": year_month,
                        "check": "thai_foreign_vs_total",
                        "severity": "warn",
                        "value": gap,
                        "detail": f"gap {gap} > {limit}",
                    }
                )

    flag_frame = pd.DataFrame(flags, columns=list(FLAG_COLUMNS))
    if not flag_frame.empty:
        flag_frame = flag_frame.sort_values(["check", "province_id"]).reset_index(drop=True)
    hard_fail = any(item["severity"] == "hard" and not item["passed"] for item in checks)
    report = {
        "year_month": year_month,
        "n_rows": int(len(month)),
        "hard_fail": bool(hard_fail),
        "n_warnings": int(len(flag_frame)),
        "checks": checks,
    }
    return report, flag_frame


def _required_history_months(year_month: str) -> list[str]:
    from src.model.leakage import forecast_source_months

    return sorted(set(forecast_source_months(year_month).values()))
