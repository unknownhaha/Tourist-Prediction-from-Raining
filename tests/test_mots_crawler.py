from src.ingest.mots_crawler import (
    SITEWIDE_CATEGORY_IDS,
    build_tree,
    category_ids_for_year,
    discover_attachments_playwright,
    parse_child_categories,
)


def test_build_tree_skips_sitewide_child(monkeypatch):
    cfg = {
        "hub_category_id": 1,
        "year_categories": [{"category_id": 531, "year_be": 2561}],
        "rate_limit_seconds": 0,
    }
    html_menu = '<a href="/news/category/139">menu</a><a href="/news/category/999">child</a>'

    def fake_fetch(session, cid):
        return html_menu if cid == 1 else "<html></html>"

    def fake_title(html):
        return "t"

    import src.ingest.mots_crawler as mc

    monkeypatch.setattr(mc, "fetch_category_html", fake_fetch)
    monkeypatch.setattr(mc, "parse_title", fake_title)
    monkeypatch.setattr(mc, "parse_child_categories", lambda html: [139, 999])
    tree = build_tree(object(), cfg)
    ids = {n["category_id"] for n in tree}
    assert 139 not in ids
    assert 999 in ids


def test_year_categories_come_from_tree_without_http(monkeypatch):
    tree = [
        {"category_id": 497, "parent_id": 411},
        {"category_id": 10, "parent_id": 497},
        {"category_id": 139, "parent_id": 10},
        {"category_id": 521, "parent_id": 497},
        {"category_id": 12, "parent_id": 521},
    ]
    cfg = {"year_categories": [{"category_id": 497, "year_be": 2561}, {"category_id": 521, "year_be": 2562}]}

    def boom(*_args, **_kwargs):
        raise AssertionError("network")

    monkeypatch.setattr("src.ingest.mots_crawler.fetch_category_html", boom)
    assert category_ids_for_year(object(), cfg, 497, tree) == {497, 10}


def test_playwright_helper_uses_supplied_page():
    class _Button:
        def click(self, timeout=0):
            raise TimeoutError()

    class _Page:
        def __init__(self):
            self.urls = []

        def goto(self, url, **_kwargs):
            self.urls.append(url)

        def title(self):
            return "expected title"

        def content(self):
            return '<a href="https://www.mots.go.th/files/a.xlsx">x</a>'

        def get_by_role(self, *_args, **_kwargs):
            return _Button()

    page = _Page()
    links = discover_attachments_playwright(
        "https://www.mots.go.th/news/100001",
        "expected title",
        {},
        "run",
        {},
        page=page,
    )
    assert links == ["https://www.mots.go.th/files/a.xlsx"]
    assert page.urls == ["https://www.mots.go.th/news/100001"]


def test_sitewide_ids_not_year_statistics():
    html = '<a href="/news/category/139">x</a><a href="/news/category/531">y</a>'
    found = parse_child_categories(html)
    assert 139 in found
    assert 531 in found
    assert 139 in SITEWIDE_CATEGORY_IDS
    assert 531 not in SITEWIDE_CATEGORY_IDS
