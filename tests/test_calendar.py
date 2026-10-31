from src.common.calendar import be_to_ce, ce_to_be, is_covid, month_range


def test_be_ce():
    assert be_to_ce(2561) == 2018
    assert ce_to_be(2018) == 2561


def test_is_covid():
    assert is_covid("2020-02") is False
    assert is_covid("2020-03") is True
    assert is_covid("2022-12") is True
    assert is_covid("2023-01") is False


def test_month_range():
    months = month_range("2018-01", "2018-03")
    assert months == ["2018-01", "2018-02", "2018-03"]
