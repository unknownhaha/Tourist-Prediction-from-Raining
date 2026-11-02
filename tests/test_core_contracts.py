from pathlib import Path

import pandas as pd
import pytest

from src.common.calendar import month_range
from src.common.config_loader import load_sources
from src.common.paths import CORE


def test_dim_month_covid_count():
    path = CORE / "dim_month.parquet"
    if not path.exists():
        pytest.skip("core tables not built")
    dim = pd.read_parquet(path)
    cfg = load_sources()
    assert len(dim) == len(month_range(cfg["date_window"]["start"], cfg["date_window"]["end"]))
    assert int(dim["is_covid"].sum()) == 34


def test_province_month_spine():
    path = CORE / "province_month.parquet"
    if not path.exists():
        pytest.skip("core tables not built")
    pm = pd.read_parquet(path)
    assert pm["is_covid"].notna().all()
    assert pm.duplicated(subset=["province_id", "year_month"]).sum() == 0
    for col in ("guest_arrivals", "visitors_total"):
        if col in pm.columns:
            assert pm[col].notna().sum() > 0
