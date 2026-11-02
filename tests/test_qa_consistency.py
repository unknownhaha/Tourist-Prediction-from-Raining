import pandas as pd

from src.qa.consistency import check_thai_foreign_total


def test_thai_foreign_total_detects_mismatch():
    df = pd.DataFrame(
        [
            {"province_id": "10", "year_month": "2024-01", "metric": "visitors", "tourist_type": "total", "value": 100},
            {"province_id": "10", "year_month": "2024-01", "metric": "visitors", "tourist_type": "thai", "value": 40},
            {"province_id": "10", "year_month": "2024-01", "metric": "visitors", "tourist_type": "foreign", "value": 50},
        ]
    )
    errs = check_thai_foreign_total(df)
    assert errs
