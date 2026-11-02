from src.staging.mots_monthly_province import _metric_kind


def test_thai_revenue_not_visitors():
    assert _metric_kind("รายได้จากผู้เยี่ยมเยือนคนไทย") == ("revenue_million_baht", "thai")


def test_foreign_revenue():
    assert _metric_kind("รายได้จากผู้เยี่ยมเยือนชาวต่างชาติ") == ("revenue_million_baht", "foreign")
