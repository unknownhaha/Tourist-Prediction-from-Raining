"""Tests for national monthly aggregation used in the handbook chart."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common.paths import ROOT
from src.docs.build_site import build
from src.docs.national_series import aggregate_national_monthly, load_national_series


def test_aggregate_sums_and_null_visitors():
    df = pd.DataFrame(
        {
            "year_month": ["2020-01", "2020-01", "2020-02", "2020-02"],
            "guest_arrivals": [100.0, 50.0, 200.0, None],
            "visitors_total": [None, None, 10.0, 20.0],
            "avg_rain_mm": [10.0, 30.0, 5.0, 15.0],
            "is_covid": [False, False, True, True],
        }
    )
    rows = aggregate_national_monthly(df)
    assert len(rows) == 2
    assert rows[0]["year_month"] == "2020-01"
    assert rows[0]["guest_arrivals"] == 150.0
    assert rows[0]["visitors_total"] is None
    assert rows[0]["avg_rain_mm"] == 20.0
    assert rows[0]["is_covid"] is False
    assert rows[1]["guest_arrivals"] == 200.0
    assert rows[1]["visitors_total"] == 30.0
    assert rows[1]["is_covid"] is True


def test_load_missing_parquet():
    missing = Path("nonexistent_province_month.parquet")
    out = load_national_series(missing)
    assert out["available"] is False
    assert out["months"] == []


def test_build_site_without_parquet(monkeypatch, tmp_path):
    real = ROOT / "site" / "index.html"
    before = real.read_text(encoding="utf-8") if real.is_file() else None
    monkeypatch.setattr(
        "src.docs.build_site.load_national_series",
        lambda: {"available": False, "months": []},
    )
    monkeypatch.setattr("src.docs.build_site.SITE", tmp_path / "site")
    index = build()
    html = index.read_text(encoding="utf-8")
    assert index == tmp_path / "site" / "index.html"
    assert '"available": false' in html or '"available": False' in html
    if before is not None:
        assert real.read_text(encoding="utf-8") == before
