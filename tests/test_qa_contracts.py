import pandas as pd

from src.qa.checks import check_unique


def test_empty_table_fails_contract():
    errors: list[str] = []
    check_unique(pd.DataFrame(), ["province_id", "year_month"], "province_month", errors)
    assert errors
