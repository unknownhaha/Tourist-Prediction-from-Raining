"""Build dimension and fact tables plus province_month spine."""

from __future__ import annotations

import sys

import pandas as pd

from src.common.calendar import ce_to_be, is_covid, month_range
from src.common.config_loader import load_sources
from src.common.paths import CONFIG, CORE, STAGING
from src.common.provinces import alias_to_province_id, load_crosswalk

PRIMARY_TOURISM_METRIC = "visitors"
PRIMARY_TOURIST_TYPE = "unknown"


def _norm_province_id(series: pd.Series) -> pd.Series:
    def one(val) -> str:
        if pd.isna(val):
            return ""
        try:
            return str(int(float(val)))
        except (TypeError, ValueError):
            return str(val).strip()

    return series.map(one)


def build_dim_province(cw: pd.DataFrame) -> pd.DataFrame:
    base = cw.dropna(subset=["province_id"]).copy()
    base = base[base["province_id"].astype(str).str.strip() != ""]
    dim = (
        base[["province_id", "province_th", "province_en", "region"]]
        .drop_duplicates(subset=["province_id"])
        .sort_values("province_id")
    )
    dim["province_id"] = _norm_province_id(dim["province_id"])
    return dim.reset_index(drop=True)


def build_dim_month() -> pd.DataFrame:
    cfg = load_sources()["date_window"]
    months = month_range(cfg["start"], cfg["end"])
    rows = []
    for ym in months:
        y, m = map(int, ym.split("-"))
        rows.append(
            {
                "year_month": ym,
                "year_ce": y,
                "month": m,
                "year_be": ce_to_be(y),
                "is_covid": is_covid(ym),
            }
        )
    return pd.DataFrame(rows)


def build_fact_rainfall() -> pd.DataFrame:
    path = STAGING / "hii" / "fact_rainfall.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["province_id"] = _norm_province_id(df["province_id"])
    cfg = load_sources()["date_window"]
    return df[(df["year_month"] >= cfg["start"]) & (df["year_month"] <= cfg["end"])]


def build_fact_tourism_long(cw: pd.DataFrame) -> pd.DataFrame:
    path = STAGING / "mots" / "fact_tourism_long.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if df.empty:
        return df
    if "year_month" in df.columns:
        valid_month = df["year_month"].astype(str).str.match(r"^\d{4}-(0[1-9]|1[0-2])$", na=False)
        df.loc[~valid_month & df["year_month"].notna(), "year_month"] = None
    df["province_id"] = _norm_province_id(
        df["province_th"].map(lambda a: alias_to_province_id(a, cw))
    )
    cfg = load_sources()["date_window"]
    monthly = df[df["year_month"].notna()]
    monthly = monthly[(monthly["year_month"] >= cfg["start"]) & (monthly["year_month"] <= cfg["end"])]
    quarterly = df[df["year_month"].isna() & df.get("quarter", pd.Series(dtype=str)).notna()]
    return pd.concat([monthly, quarterly], ignore_index=True)


def build_fact_tourism_province_month(long_df: pd.DataFrame) -> pd.DataFrame:
    if long_df.empty:
        return pd.DataFrame(
            columns=[
                "province_id",
                "year_month",
                "metric",
                "tourist_type",
                "value",
                "unit",
            ]
        )
    ok = long_df[long_df["province_id"].notna()].copy()
    key = ["province_id", "year_month", "metric", "tourist_type"]
    return ok.drop_duplicates(subset=key)


def build_fact_accommodation_nso() -> pd.DataFrame:
    path = STAGING / "nso" / "fact_accommodation_nso_inventory.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def build_province_month(
    dim_province: pd.DataFrame,
    dim_month: pd.DataFrame,
    rain: pd.DataFrame,
    tourism: pd.DataFrame,
) -> pd.DataFrame:
    spine = dim_province.assign(_k=1).merge(dim_month.assign(_k=1), on="_k").drop(columns="_k")
    if not rain.empty:
        rain_cols = rain[
            ["province_id", "year_month", "min_rain_mm", "max_rain_mm", "avg_rain_mm"]
        ].drop_duplicates(subset=["province_id", "year_month"])
        spine = spine.merge(rain_cols, on=["province_id", "year_month"], how="left")
    if not tourism.empty:
        t = tourism[
            (tourism["metric"] == PRIMARY_TOURISM_METRIC)
            & (tourism["tourist_type"] == PRIMARY_TOURIST_TYPE)
        ][["province_id", "year_month", "value"]].rename(columns={"value": "visitors_unknown"})
        t = t.drop_duplicates(subset=["province_id", "year_month"])
        spine = spine.merge(t, on=["province_id", "year_month"], how="left")
    keep = [
        "province_id",
        "province_th",
        "year_month",
        "year_ce",
        "month",
        "year_be",
        "is_covid",
        "min_rain_mm",
        "max_rain_mm",
        "avg_rain_mm",
        "visitors_unknown",
    ]
    return spine[[c for c in keep if c in spine.columns]]


def main() -> int:
    CORE.mkdir(parents=True, exist_ok=True)
    cw = load_crosswalk()
    dim_province = build_dim_province(cw)
    dim_month = build_dim_month()
    fact_rain = build_fact_rainfall()
    fact_long = build_fact_tourism_long(cw)
    fact_tourism_pm = build_fact_tourism_province_month(fact_long)
    fact_nso = build_fact_accommodation_nso()
    province_month = build_province_month(dim_province, dim_month, fact_rain, fact_tourism_pm)

    dim_province.to_parquet(CORE / "dim_province.parquet", index=False)
    dim_month.to_parquet(CORE / "dim_month.parquet", index=False)
    fact_rain.to_parquet(CORE / "fact_rainfall.parquet", index=False)
    fact_long.to_parquet(CORE / "fact_tourism_long.parquet", index=False)
    fact_tourism_pm.to_parquet(CORE / "fact_tourism_province_month.parquet", index=False)
    fact_nso.to_parquet(CORE / "fact_accommodation_nso.parquet", index=False)
    province_month.to_parquet(CORE / "province_month.parquet", index=False)
    print(f"Core tables written to {CORE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
