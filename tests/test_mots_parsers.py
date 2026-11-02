from pathlib import Path

import pandas as pd
import pytest

from src.common.provinces import load_crosswalk
from src.staging.mots_monthly_province import parse_workbook as parse_monthly
from src.staging.mots_quarterly_province import parse_workbook as parse_quarterly


def test_monthly_synthetic_workbook(tmp_path):
    path = tmp_path / "mots" / "2024" / "760" / "syn.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [[0] * 12 for _ in range(10)]
    rows[1][2] = "รายได้จากผู้เยี่ยมเยือนคนไทย"
    rows[3][2] = "2567"
    rows[3][3] = "2566"
    rows[3][4] = "%"
    rows[4][1] = "กรุงเทพมหานคร"
    rows[4][2] = 11.0
    rows[4][3] = 9.0
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="ม.ค.2567(R)", index=False, header=False)
    cw = load_crosswalk()
    out = parse_monthly(path, cw=cw)
    rev = out[(out["metric"] == "revenue_million_baht") & (out["tourist_type"] == "thai")]
    assert not rev.empty


def test_parser_accepts_explicit_crosswalk(tmp_path):
    path = tmp_path / "mots" / "2024" / "812" / "syn.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [[None] * 12 for _ in range(45)]
    rows[2] = [None] * 7 + ["Jan", "Feb", "Mar"] + [None] * 2
    rows[42] = [None] * 6 + ["Grand Total", 1.0, 2.0, 3.0]
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="เชียงใหม่", index=False, header=False)
    cw = load_crosswalk()
    assert parse_quarterly(path, cw=cw) is not None


def test_quarterly_synthetic_workbook(tmp_path):
    path = tmp_path / "mots" / "2024" / "812" / "syn.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [[None] * 15 for _ in range(45)]
    rows[2] = [None] * 7 + ["Jan", "Feb", "Mar"] + [None] * 5
    rows[42] = [None] * 6 + ["Grand Total", 100.0, 200.0, 300.0] + [None] * 5
    rows[43] = [None] * 6 + ["Thai", 60.0, 120.0, 180.0] + [None] * 5
    rows[44] = [None] * 6 + ["Foreigners", 40.0, 80.0, 120.0] + [None] * 5
    rows[1][1] = "January-March"
    rows[3][1] = 900.0
    rows[4][1] = 500.0
    rows[5][1] = 400.0
    rows[1][10] = "Q1"
    rows[42][10] = 600.0
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="เชียงใหม่", index=False, header=False)
    out = parse_quarterly(path)
    assert not out.empty
    jan = out[(out["metric"] == "guest_arrivals") & (out["tourist_type"] == "total") & (out["year_month"].str.endswith("-01"))]
    assert float(jan.iloc[0]["value"]) == 100.0
    qvis = out[(out["metric"] == "visitors") & (out["quarter"] == "2024-Q1") & (out["tourist_type"] == "total")]
    assert float(qvis.iloc[0]["value"]) == 900.0
    qguest = out[(out["metric"] == "guest_arrivals") & (out["quarter"] == "2024-Q1") & (out["tourist_type"] == "total")]
    assert float(qguest.iloc[0]["value"]) == 600.0


@pytest.mark.integration
def test_golden_chiang_mai_guests_2024():
    root = Path("data/raw/mots/2024/812")
    if not root.exists():
        pytest.skip("MOTS raw data not present")
    candidates = list(root.rglob("*1757992702405*"))
    if not candidates:
        pytest.skip("Chiang Mai 2024 workbook not found")
    out = parse_quarterly(candidates[0])
    hit = out[
        (out["sheet"] == "เชียงใหม่")
        & (out["metric"] == "guest_arrivals")
        & (out["tourist_type"] == "total")
        & (out["year_month"] == "2024-01")
    ]
    assert len(hit) == 1
    assert int(hit.iloc[0]["value"]) == 815178


@pytest.mark.integration
def test_golden_bangkok_visitors_2024():
    root = Path("data/raw/mots/2024/760")
    if not root.exists():
        pytest.skip("MOTS monthly 2024 not present")
    files = list(root.rglob("*.xls*"))
    if not files:
        pytest.skip("monthly workbook missing")
    out = parse_monthly(files[0])
    hit = out[
        (out["province_th_raw"] == "กรุงเทพมหานคร")
        & (out["metric"] == "visitors")
        & (out["tourist_type"] == "total")
        & (out["year_month"] == "2024-01")
        & (~out["from_prior_year_column"])
    ]
    assert len(hit) == 1
    assert int(hit.iloc[0]["value"]) == 5097274
