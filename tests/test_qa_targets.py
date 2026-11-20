import pandas as pd
import pytest

from src.common.config_loader import load_sources
from src.common.paths import CORE


@pytest.mark.integration
def test_province_month_no_late_months():
    path = CORE / "province_month.parquet"
    if not path.exists():
        pytest.skip("core tables not built")
    pm = pd.read_parquet(path)
    end = load_sources()["date_window"]["end"]
    assert pm[pm["year_month"] > end].empty


@pytest.mark.integration
def test_visitors_total_coverage():
    path = CORE / "province_month.parquet"
    if not path.exists():
        pytest.skip("core tables not built")
    pm = pd.read_parquet(path)
    assert pm["visitors_total"].notna().sum() >= 2400
