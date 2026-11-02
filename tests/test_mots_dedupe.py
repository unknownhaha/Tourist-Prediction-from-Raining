import pandas as pd

from src.staging.mots_dedupe import dedupe_long


def test_current_year_wins_over_prior(tmp_path):
    df = pd.DataFrame(
        [
            {
                "province_th_raw": "กรุงเทพมหานคร",
                "province_id": "10",
                "area_level": "province",
                "year_month": "2023-01",
                "quarter": None,
                "metric": "visitors",
                "tourist_type": "total",
                "value": 100.0,
                "source_file": "a.xlsx",
                "from_prior_year_column": True,
            },
            {
                "province_th_raw": "กทม.",
                "province_id": "10",
                "area_level": "province",
                "year_month": "2023-01",
                "quarter": None,
                "metric": "visitors",
                "tourist_type": "total",
                "value": 200.0,
                "source_file": "b.xlsx",
                "from_prior_year_column": False,
            },
        ]
    )
    out = dedupe_long(df, conflict_path=tmp_path / "conflicts.csv")
    assert len(out) == 1
    assert float(out.iloc[0]["value"]) == 200.0


def test_conflict_report_on_mismatch(tmp_path):
    out_path = tmp_path / "conflicts.csv"
    df = pd.DataFrame(
        [
            {
                "province_th_raw": "x",
                "province_id": "50",
                "area_level": "province",
                "year_month": "2024-01",
                "quarter": None,
                "metric": "guest_arrivals",
                "tourist_type": "total",
                "value": 1.0,
                "source_file": "old.xlsx",
                "from_prior_year_column": False,
            },
            {
                "province_th_raw": "x",
                "province_id": "50",
                "area_level": "province",
                "year_month": "2024-01",
                "quarter": None,
                "metric": "guest_arrivals",
                "tourist_type": "total",
                "value": 2.0,
                "source_file": "data/raw/mots/2024/812/new.xlsx",
                "from_prior_year_column": False,
            },
        ]
    )
    dedupe_long(df, conflict_path=out_path)
    assert out_path.exists()
    conflicts = pd.read_csv(out_path)
    assert len(conflicts) == 1
