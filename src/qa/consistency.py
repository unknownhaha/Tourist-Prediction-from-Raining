"""Arithmetic consistency checks on engineered tourism facts."""

from __future__ import annotations

import pandas as pd

QUARTER_MONTHS = {
    1: (1, 2, 3),
    2: (4, 5, 6),
    3: (7, 8, 9),
    4: (10, 11, 12),
}


def _within_tolerance(total: float, parts_sum: float) -> bool:
    tol = max(1.0, abs(total) * 0.001)
    return abs(total - parts_sum) <= tol


def check_thai_foreign_total(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return []
    errors: list[str] = []
    count_metrics = {"guest_arrivals", "guests", "visitors"}
    sub = df[df["metric"].isin(count_metrics) & df["year_month"].notna()].copy()
    if sub.empty:
        return errors
    for (_, ym, metric), grp in sub.groupby(["province_id", "year_month", "metric"], dropna=False):
        by_type = grp.set_index("tourist_type")["value"] if "tourist_type" in grp.columns else pd.Series(dtype=float)
        if not {"total", "thai", "foreign"}.issubset(by_type.index):
            continue
        if not _within_tolerance(float(by_type["total"]), float(by_type["thai"]) + float(by_type["foreign"])):
            errors.append(f"fact_tourism_province_month: {ym} {metric} total != thai+foreign")
            if len(errors) >= 20:
                return errors
    return errors


def check_guest_months_sum_to_quarter(staging_long: pd.DataFrame) -> list[str]:
    if staging_long.empty:
        return []
    errors: list[str] = []
    sub = staging_long[
        (staging_long.get("layout") == "quarterly_domestic")
        & (staging_long["metric"] == "guest_arrivals")
        & (staging_long.get("area_level") == "province")
    ].copy()
    if sub.empty or "month" not in sub.columns:
        return errors
    monthly = sub[sub["year_month"].notna()]
    quarterly = sub[sub["quarter"].notna()]
    if quarterly.empty:
        return errors
    keys = ["source_file", "sheet", "year_ce", "tourist_type"]
    for key, qgrp in quarterly.groupby(keys, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        mgrp = monthly.copy()
        for col, val in zip(keys, key_tuple, strict=True):
            mgrp = mgrp[mgrp[col] == val]
        year_ce = int(key_tuple[2])
        for qn in (1, 2, 3, 4):
            q_label = f"{year_ce}-Q{qn}"
            qrow = qgrp[qgrp["quarter"] == q_label]
            if qrow.empty:
                continue
            qval = float(qrow.iloc[0]["value"])
            months = QUARTER_MONTHS[qn]
            msum = mgrp[mgrp["month"].isin(months)]["value"].sum()
            if msum and not _within_tolerance(qval, float(msum)):
                errors.append(f"guest_arrivals: {key[1]} {q_label} total != sum(months)")
                if len(errors) >= 20:
                    return errors
    return errors
