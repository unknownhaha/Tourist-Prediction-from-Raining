"""Deterministic cleaning. Every change is logged. Target values are not edited."""

from __future__ import annotations

import pandas as pd

from src.stream.validate import REQUIRED_COLUMNS, _pad_candidate

RAIN_COLUMNS = ("rain_avg_mm", "rain_min_mm", "rain_max_mm")
TARGET_COLUMNS = ("guest_arrivals_total", "guest_arrivals_thai", "guest_arrivals_foreign")


def _action(name: str, **detail: object) -> dict:
    row = {"action": name}
    row.update(detail)
    return row


def pad_province_id(value: object) -> str:
    padded = _pad_candidate(value)
    if padded is None:
        raise ValueError(f"province_id cannot be padded: {value!r}")
    return padded


def clean_panel(
    frame: pd.DataFrame,
    staging_rain: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    """Return a cleaned copy and the actions that changed the frame.

    `guest_arrivals_*` may change dtype to float. Their numeric values stay
    the same. Negatives are not clipped. Missing rain is copied only from the
    same province-month in staging, never from a neighbor or a previous month.
    """
    actions: list[dict] = []
    out = frame.copy()

    padded = out["province_id"].map(pad_province_id)
    changed = out["province_id"].astype(str) != padded.astype(str)
    for index in out.index[changed]:
        actions.append(
            _action(
                "pad_province_id",
                province_id=padded.loc[index],
                year_month=str(out.at[index, "year_month"]),
                before=str(out.at[index, "province_id"]),
                after=padded.loc[index],
            )
        )
    out["province_id"] = padded

    out["year_month"] = out["year_month"].map(lambda value: str(value).strip())

    for column in TARGET_COLUMNS + RAIN_COLUMNS:
        if column not in out.columns:
            continue
        original = out[column]
        numeric = pd.to_numeric(original, errors="coerce")
        destroyed = original.notna() & numeric.isna()
        if bool(destroyed.any()):
            raise ValueError(f"clean refused to change {column}: non-numeric values")
        if str(original.dtype) != str(numeric.dtype):
            actions.append(_action("cast", column=column, dtype_from=str(original.dtype), dtype_to=str(numeric.dtype)))
        both_numbers = original.notna() & numeric.notna()
        if bool(both_numbers.any()) and not (original[both_numbers].astype(float) == numeric[both_numbers]).all():
            raise ValueError(f"clean refused to change numeric values in {column}")
        out[column] = numeric

    if "is_covid" in out.columns:
        out["is_covid"] = out["is_covid"].map(lambda value: bool(value) if not pd.isna(value) else value)

    before_rows = len(out)
    sort_columns = [column for column in ("year_month", "province_id") if column in out.columns]
    out = out.sort_values(sort_columns).drop_duplicates(subset=["province_id", "year_month"], keep="first")
    # drop_duplicates keeps the first row even when values conflict. Conflicting
    # keys must already have been rejected by validate. Exact duplicates only
    # reach this point, so the kept row matches the dropped ones.
    removed = before_rows - len(out)
    if removed:
        actions.append(_action("dedupe_exact", rows_removed=int(removed)))

    if staging_rain is not None and not staging_rain.empty:
        out, rain_actions = _fill_rain_from_staging(out, staging_rain)
        actions.extend(rain_actions)

    actions.sort(key=lambda item: (item["action"], str(item.get("year_month", "")), str(item.get("province_id", ""))))
    return out.reset_index(drop=True), actions


def _fill_rain_from_staging(
    frame: pd.DataFrame,
    staging_rain: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict]]:
    actions: list[dict] = []
    frame = frame.copy()
    frame["_needs_rain"] = frame[list(RAIN_COLUMNS)].isna().any(axis=1)
    if not bool(frame["_needs_rain"].any()):
        return frame.drop(columns=["_needs_rain"]), actions

    staging = staging_rain.copy()
    staging["province_id"] = staging["province_id"].map(pad_province_id)
    staging["year_month"] = staging["year_month"].map(lambda value: str(value).strip())
    rename = {
        "avg_rain_mm": "rain_avg_mm",
        "min_rain_mm": "rain_min_mm",
        "max_rain_mm": "rain_max_mm",
    }
    staging = staging.rename(columns=rename)
    keep = ["province_id", "year_month", *RAIN_COLUMNS]
    missing = [column for column in keep if column not in staging.columns]
    if missing:
        raise ValueError(f"staging rainfall missing columns: {missing}")
    staging = staging[keep].drop_duplicates(["province_id", "year_month"], keep="first")

    out = frame.merge(staging, on=["province_id", "year_month"], how="left", suffixes=("", "_staging"))
    for index in out.index[out["_needs_rain"]]:
        staged = out.loc[index, [f"{column}_staging" for column in RAIN_COLUMNS]]
        if staged.isna().any():
            continue
        for column in RAIN_COLUMNS:
            out.at[index, column] = out.at[index, f"{column}_staging"]
        actions.append(
            _action(
                "rain_from_staging_exact_month",
                province_id=out.at[index, "province_id"],
                year_month=out.at[index, "year_month"],
            )
        )
    drop_columns = ["_needs_rain", *[f"{column}_staging" for column in RAIN_COLUMNS]]
    return out.drop(columns=drop_columns), actions
