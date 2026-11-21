"""Data-quality scores for the new-feature inputs. Separate from schema validation.

Outliers and YoY jumps are warnings. The thresholds in ``dq_config.json`` are
the only hard stops, and DS/ML can change them.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.new_features.calendar_data import calendar_month_table
from src.stream.quality import ROBUST_Z_LIMIT, _mad_z

CONFIG_PATH = Path(__file__).resolve().parent / "dq_config.json"
CORE_COLUMNS = ("guest_arrivals_total", "rain_avg_mm")
SPLIT_COLUMNS = ("guest_arrivals_thai", "guest_arrivals_foreign")
SERIES = ("guest_arrivals_total", "guest_arrivals_thai", "guest_arrivals_foreign", "rain_avg_mm")


class DataQualityError(RuntimeError):
    def __init__(self, message: str, report: dict):
        super().__init__(message)
        self.report = report


def load_config(path: Path | None = None) -> dict:
    return json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))


def _missing_frac(frame: pd.DataFrame, columns: tuple[str, ...]) -> dict[str, float]:
    return {column: float(frame[column].isna().mean()) if len(frame) else 1.0 for column in columns}


def _score(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def assess_feature_inputs(panel: pd.DataFrame, features: pd.DataFrame, config: dict | None = None) -> dict:
    config = dict(load_config() if config is None else config)
    work = panel.copy()
    work["province_id"] = work["province_id"].astype(str).str.zfill(2)
    work["year_month"] = work["year_month"].astype(str)
    features = features.copy()
    features["province_id"] = features["province_id"].astype(str).str.zfill(2)
    features["year_month"] = features["year_month"].astype(str)

    core = _missing_frac(work, CORE_COLUMNS)
    splits = work.loc[work["year_month"] >= "2019-01"]
    split_missing = _missing_frac(splits, SPLIT_COLUMNS)
    by_year = {}
    work = work.assign(year=work["year_month"].str.slice(0, 4))
    for year, part in work.groupby("year"):
        by_year[str(year)] = _missing_frac(part, CORE_COLUMNS + SPLIT_COLUMNS)
    by_province = (
        work.groupby("province_id")[list(CORE_COLUMNS)]
        .apply(lambda frame: float(frame.isna().mean().mean()))
        .to_dict()
    )

    duplicate = int(work.duplicated(["province_id", "year_month"]).sum())
    expected = pd.period_range("2018-01", "2025-12", freq="M").strftime("%Y-%m")
    gap_rows = 0
    province_ids = sorted(work["province_id"].unique())
    have = set(zip(work["province_id"], work["year_month"]))
    for province_id in province_ids:
        for month in expected:
            if (province_id, month) not in have:
                gap_rows += 1
    gap_frac = gap_rows / (len(province_ids) * len(expected)) if province_ids else 1.0

    flags = []
    for province_id, province in work.groupby("province_id", sort=False):
        province = province.sort_values("year_month")
        months = province["year_month"].to_numpy()
        lookup = {month: index for index, month in enumerate(months)}
        for column in SERIES:
            values = province[column].to_numpy(dtype=float)
            past: list[float] = []
            for index, value in enumerate(values):
                if not np.isfinite(value):
                    continue
                score = _mad_z(value, np.asarray(past, dtype=float))
                if score is not None and abs(score) > ROBUST_Z_LIMIT:
                    flags.append({"check": "robust_z", "column": column, "province_id": province_id, "year_month": months[index]})
                prior = str(pd.Period(months[index], freq="M") - 12)
                prior_index = lookup.get(prior)
                if prior_index is not None and np.isfinite(values[prior_index]) and values[prior_index] > 0:
                    jump = abs(value - values[prior_index]) / values[prior_index]
                    if jump > 1.0:
                        flags.append({"check": "yoy_jump", "column": column, "province_id": province_id, "year_month": months[index]})
                past.append(float(value))
    flagged_keys = {(item["province_id"], item["year_month"]) for item in flags}
    flagged_frac = len(flagged_keys) / len(work) if len(work) else 1.0

    both = splits["guest_arrivals_thai"].notna() & splits["guest_arrivals_foreign"].notna()
    gap = (
        splits.loc[both, "guest_arrivals_thai"] + splits.loc[both, "guest_arrivals_foreign"] - splits.loc[both, "guest_arrivals_total"]
    ).abs()
    limit = np.maximum(1.0, 0.001 * splits.loc[both, "guest_arrivals_total"].abs())
    mismatches = int((gap > limit).sum())
    mismatch_frac = mismatches / int(both.sum()) if int(both.sum()) else 1.0

    calendar = calendar_month_table().set_index("year_month")["holiday_count"]
    expected_holidays = features["year_month"].map(calendar)
    calendar_mismatch = int((features["holiday_count"].to_numpy() != expected_holidays.to_numpy()).sum())

    invalid = 0
    checked = 0
    if "holiday_count" in features:
        values = features["holiday_count"].to_numpy(dtype=float)
        checked += len(values)
        invalid += int((~np.isfinite(values) | (values < 0) | (values > config["holiday_count_max"])).sum())
    for column in ("festival_songkran", "festival_new_year", "festival_loy", "festival_cny", "festival_province"):
        if column not in features:
            continue
        values = features[column].to_numpy(dtype=float)
        checked += len(values)
        invalid += int((~np.isfinite(values) | ~np.isin(values, (0.0, 1.0))).sum())
    ratio_columns = [column for column in ("thai_yoy", "foreign_yoy", "yoy_3m_log", "region_yoy_log") if column in features]
    inf_count = 0
    for column in ratio_columns:
        inf_count += int(np.isinf(features[column].to_numpy(dtype=float)).sum())
    invalid += inf_count
    invalid_frac = invalid / checked if checked else 0.0

    dimensions = {
        "completeness": _score(1.0 - float(np.mean([*core.values(), *split_missing.values()]))),
        "accuracy": _score(1.0 - flagged_frac),
        "consistency": _score(1.0 - mismatch_frac),
        "uniqueness": _score(1.0 if duplicate == 0 else 0.0),
        "timeliness": _score(1.0 - gap_frac),
        "validity": _score(1.0 - invalid_frac),
    }
    overall = float(np.mean(list(dimensions.values())))
    hard = []
    if max(core.values(), default=1.0) > config["completeness_max_missing_frac_core"]:
        hard.append("completeness_core")
    if max(split_missing.values(), default=1.0) > config["completeness_max_missing_frac_splits_from_2019"]:
        hard.append("completeness_splits")
    if duplicate > config["uniqueness_max_duplicate_keys"]:
        hard.append("uniqueness")
    if gap_frac > config["timeliness_max_gap_frac"]:
        hard.append("timeliness")
    if invalid_frac > config["validity_max_invalid_frac"]:
        hard.append("validity")
    if mismatch_frac > config["consistency_max_mismatch_frac"]:
        hard.append("consistency_thai_foreign")
    if calendar_mismatch > config["consistency_max_calendar_mismatch"]:
        hard.append("consistency_calendar")
    if flagged_frac > config["accuracy_max_flagged_row_frac"]:
        hard.append("accuracy")
    report = {
        "hard_fail": bool(hard),
        "hard_reasons": hard,
        "thresholds": config,
        "scores": {**dimensions, "overall": overall},
        "completeness": {"core": core, "splits_from_2019": split_missing, "by_year": by_year,
                         "by_province_mean_core_missing": by_province},
        "accuracy": {
            "robust_z_limit": ROBUST_Z_LIMIT,
            "yoy_jump_limit": 1.0,
            "n_flags": len(flags),
            "n_flagged_rows": len(flagged_keys),
            "flagged_row_frac": flagged_frac,
        },
        "consistency": {"thai_foreign_mismatches": mismatches, "mismatch_frac": mismatch_frac,
                        "holiday_count_mismatches": calendar_mismatch},
        "uniqueness": {"duplicate_keys": duplicate},
        "timeliness": {"gap_rows": gap_rows, "gap_frac": gap_frac, "n_provinces": len(province_ids)},
        "validity": {"invalid_values": invalid, "invalid_frac": invalid_frac, "inf_ratios": inf_count},
        "warnings": [
            "Robust-z and year-over-year jumps are warnings unless accuracy_max_flagged_row_frac is exceeded.",
            "2018 Thai and foreign arrivals are unpublished. They are excluded from the split completeness threshold.",
        ],
    }
    if hard:
        raise DataQualityError("data-quality thresholds exceeded: " + ", ".join(hard), report)
    return report
