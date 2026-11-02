import pandas as pd
import pytest

from src.common.paths import CORE


@pytest.mark.integration
def test_province_month_no_late_months():
    path = CORE / "province_month.parquet"
    if not path.exists():
        pytest.skip("core tables not built")
    pm = pd.read_parquet(path)
    assert pm[pm["year_month"] > "2025-08"].empty


@pytest.mark.integration
def test_visitors_total_coverage():
    path = CORE / "province_month.parquet"
    if not path.exists():
        pytest.skip("core tables not built")
    pm = pd.read_parquet(path)
    assert pm["visitors_total"].notna().sum() >= 2400
