from src.ingest.mots_crawler import SITEWIDE_CATEGORY_IDS, build_tree, parse_child_categories


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


def test_sitewide_ids_not_year_statistics():
    html = '<a href="/news/category/139">x</a><a href="/news/category/531">y</a>'
    found = parse_child_categories(html)
    assert 139 in found
    assert 531 in found
    assert 139 in SITEWIDE_CATEGORY_IDS
    assert 531 not in SITEWIDE_CATEGORY_IDS
