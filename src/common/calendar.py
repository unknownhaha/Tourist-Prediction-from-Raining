"""BE/CE calendar helpers and COVID period flag."""

BE_OFFSET = 543
COVID_START = "2020-03"
COVID_END = "2022-12"


def be_to_ce(year_be: int) -> int:
    return year_be - BE_OFFSET


def ce_to_be(year_ce: int) -> int:
    return year_ce + BE_OFFSET


def year_month(year_ce: int, month: int) -> str:
    return f"{year_ce:04d}-{month:02d}"


def is_covid(ym: str) -> bool:
    """True for project-defined disruption window (same for all provinces in that month)."""
    return COVID_START <= ym <= COVID_END


def month_range(start: str, end: str) -> list[str]:
    """Inclusive YYYY-MM list."""
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out: list[str] = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(year_month(y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out
