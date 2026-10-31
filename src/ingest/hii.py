"""Download HII spatial rainfall CSV and stage province-month table."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import requests

from src.common.config_loader import load_sources
from src.common.manifest import append_row, new_run_id, sha256_file
from src.common.paths import RAW, STAGING

HII_RAW = RAW / "hii"


def download_csv(run_id: str) -> Path:
    cfg = load_sources()["hii"]
    url = cfg["csv_url"]
    HII_RAW.mkdir(parents=True, exist_ok=True)
    dest = HII_RAW / "spatial-rain-hii.csv"
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    digest = sha256_file(dest)
    append_row(
        {
            "run_id": run_id,
            "source": "hii",
            "file_url": url,
            "local_path": str(dest.relative_to(RAW.parent.parent)),
            "file_type": "csv",
            "sha256": digest,
            "size_bytes": dest.stat().st_size,
            "http_status": resp.status_code,
            "status": "ok",
        }
    )
    return dest


def stage(csv_path: Path) -> Path:
    df = pd.read_csv(csv_path)
    df = df.rename(
        columns={
            "YEAR": "year_ce",
            "MONTH": "month",
            "PROV_ID": "province_id",
            "PROV_T": "province_th",
            "MinRain": "min_rain_mm",
            "MaxRain": "max_rain_mm",
            "AvgRain": "avg_rain_mm",
        }
    )
    df["year_month"] = df.apply(
        lambda r: f"{int(r['year_ce']):04d}-{int(r['month']):02d}", axis=1
    )
    df["source"] = "hii"
    df["source_file"] = csv_path.name
    out_cols = [
        "source",
        "source_file",
        "year_ce",
        "month",
        "year_month",
        "province_id",
        "province_th",
        "min_rain_mm",
        "max_rain_mm",
        "avg_rain_mm",
    ]
    out = STAGING / "hii"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "fact_rainfall.parquet"
    df[out_cols].to_parquet(path, index=False)
    return path


def build_province_crosswalk(csv_path: Path) -> Path:
    df = pd.read_csv(csv_path)
    provinces = (
        df[["PROV_ID", "PROV_T"]]
        .drop_duplicates()
        .rename(columns={"PROV_ID": "province_id", "PROV_T": "province_th"})
    )
    provinces["canonical_key"] = provinces["province_id"].astype(str)
    provinces["alias"] = provinces["province_th"]
    provinces["region"] = ""
    provinces["province_en"] = ""
    from src.common.paths import CONFIG

    path = CONFIG / "province_crosswalk.csv"
    provinces[
        ["canonical_key", "province_id", "alias", "province_th", "province_en", "region"]
    ].to_csv(path, index=False)
    return path


def main() -> int:
    run_id = new_run_id()
    csv_path = download_csv(run_id)
    stage(csv_path)
    build_province_crosswalk(csv_path)
    print(f"HII staged: {STAGING / 'hii' / 'fact_rainfall.parquet'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
