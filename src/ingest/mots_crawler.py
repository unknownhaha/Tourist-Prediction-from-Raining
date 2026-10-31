"""MOTS tourism statistics crawler: category tree, inventory, Playwright downloads."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import requests

from src.common.calendar import be_to_ce
from src.common.config_loader import load_sources
from src.common.manifest import append_row, known_news_url, known_url, new_run_id, sha256_file
from src.common.paths import CONFIG, RAW

MOTS_RAW = RAW / "mots"
ATTACH_RE = re.compile(
    r'href="([^"]+\.(?:xlsx|xls|xlsx?|zip|rar|pdf))"', re.I
)
CATEGORY_RE = re.compile(r"/news/category/(\d+)")
NEWS_LINK_RE = re.compile(r'href="(/news/\d+)"')


def _abs_href(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.mots.go.th" + href
    return "https://www.mots.go.th/" + href


def parse_attachment_hrefs(html: str) -> list[str]:
    links = [_abs_href(h) for h in ATTACH_RE.findall(html)]
    return list(dict.fromkeys(links))


def _session(cfg: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": cfg["user_agent"]})
    return s


def fetch_category_html(session: requests.Session, category_id: int) -> str:
    url = f"https://www.mots.go.th/news/category/{category_id}"
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return r.text


def parse_child_categories(html: str) -> set[int]:
    return {int(x) for x in CATEGORY_RE.findall(html)}


def parse_title(html: str) -> str:
    m = re.search(r"<title>([^<]+)</title>", html)
    return m.group(1).split("|")[0].strip() if m else ""


def build_tree(session: requests.Session, cfg: dict) -> list[dict]:
    nodes: dict[int, dict] = {}
    hub = cfg["hub_category_id"]
    queue: list[tuple[int, int | None, int | None]] = [(hub, None, None)]
    seen: set[int] = set()

    year_map = {y["category_id"]: y for y in cfg["year_categories"]}

    while queue:
        cid, parent, year_be = queue.pop(0)
        if cid in seen:
            continue
        seen.add(cid)
        html = fetch_category_html(session, cid)
        title = parse_title(html)
        if cid in year_map:
            year_be = year_map[cid]["year_be"]
        nodes[cid] = {
            "category_id": cid,
            "parent_id": parent,
            "title": title,
            "year_be": year_be,
            "year_ce": be_to_ce(year_be) if year_be else None,
        }
        time.sleep(cfg["rate_limit_seconds"])
        for child in parse_child_categories(html):
            if child not in seen:
                queue.append((child, cid, year_be))

    return list(nodes.values())


def category_descendants(tree: list[dict], root_id: int) -> set[int]:
    children: dict[int | None, list[int]] = {}
    for node in tree:
        children.setdefault(node.get("parent_id"), []).append(node["category_id"])
    seen: set[int] = set()
    queue = [root_id]
    while queue:
        cid = queue.pop(0)
        if cid in seen:
            continue
        seen.add(cid)
        queue.extend(children.get(cid, []))
    return seen


def save_tree(tree: list[dict]) -> Path:
    path = CONFIG / "mots_category_tree.json"
    path.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def rss_items(session: requests.Session, category_id: int, rss_template: str) -> list[dict]:
    url = rss_template.format(category_id=category_id)
    r = session.get(url, timeout=60)
    if r.status_code != 200:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    items = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        if link_el is None or not link_el.text:
            continue
        items.append(
            {
                "title": (title_el.text or "").strip(),
                "link": link_el.text.strip(),
            }
        )
    return items


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1] or "unknown"


def discover_attachments_http(session: requests.Session, news_url: str) -> list[str]:
    try:
        r = session.get(news_url, timeout=60)
        if r.status_code != 200:
            return []
        return parse_attachment_hrefs(r.text)
    except requests.RequestException:
        return []


def discover_attachments_playwright(
    news_url: str,
    expected_title: str,
    cfg: dict,
    run_id: str,
    meta: dict,
) -> list[str]:
    from playwright.sync_api import sync_playwright

    links: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=cfg["user_agent"])
        page.goto(news_url, wait_until="domcontentloaded", timeout=25000)
        try:
            page.get_by_role("button", name="ยอมรับทั้งหมด").click(timeout=3000)
        except Exception:
            pass
        page_title = page.title()
        html = page.content()
        links = parse_attachment_hrefs(html)
        if not links and "ไม่พบข่าว" in page_title:
            append_row(
                {
                    **meta,
                    "run_id": run_id,
                    "news_url": news_url,
                    "news_title": expected_title,
                    "status": "rejected_title_mismatch",
                    "error_message": page_title[:200],
                }
            )
        elif (
            not links
            and expected_title
            and expected_title[:30] not in page_title
            and page_title[:30] not in expected_title
        ):
            append_row(
                {
                    **meta,
                    "run_id": run_id,
                    "news_url": news_url,
                    "news_title": expected_title,
                    "status": "rejected_title_mismatch",
                    "error_message": page_title[:200],
                }
            )
        browser.close()
    return links


def _news_numeric_id(news_url: str) -> int:
    m = re.search(r"/news/(\d+)", news_url)
    return int(m.group(1)) if m else 0


def list_posts(
    session: requests.Session,
    category_id: int,
    cat_html: str,
    rss_template: str,
) -> list[dict]:
    """Prefer category-page news links; RSS often points at migrated /news/{id} 404s."""
    by_url: dict[str, str] = {}
    for path in NEWS_LINK_RE.findall(cat_html):
        url = _abs_href(path)
        by_url[url] = ""
    for item in rss_items(session, category_id, rss_template):
        url = item["link"]
        if url in by_url:
            by_url[url] = item["title"] or by_url[url]
            continue
        if _news_numeric_id(url) >= 100000:
            by_url[url] = item["title"]
    return [{"link": u, "title": t} for u, t in by_url.items()]


def download_attachments(
    session: requests.Session,
    attach_urls: list[str],
    *,
    run_id: str,
    meta: dict,
    storage_slug: str,
) -> tuple[int, int]:
    files = 0
    total_bytes = 0
    year_ce = meta["year_ce"]
    cid = meta["category_id"]
    news_url = meta["news_url"]
    title = meta["news_title"]
    for file_url in attach_urls:
        if known_url("mots", file_url):
            continue
        ext = Path(urlparse(file_url).path).suffix.lower().lstrip(".")
        dest = (
            MOTS_RAW
            / str(year_ce)
            / str(cid)
            / storage_slug
            / Path(urlparse(file_url).path).name
        )
        if dest.exists():
            continue
        try:
            status, size = download_file(session, file_url, dest)
            digest = sha256_file(dest)
            append_row(
                {
                    "run_id": run_id,
                    "source": "mots",
                    "year_be": meta["year_be"],
                    "year_ce": year_ce,
                    "category_id": cid,
                    "news_url": news_url,
                    "news_title": title,
                    "file_url": file_url,
                    "local_path": str(dest.relative_to(RAW.parent.parent)),
                    "file_type": ext,
                    "sha256": digest,
                    "size_bytes": size,
                    "http_status": status,
                    "status": "ok" if status == 200 else "failed",
                }
            )
            if status == 200:
                files += 1
                total_bytes += size
        except Exception as e:
            append_row(
                {
                    "run_id": run_id,
                    "source": "mots",
                    "year_be": meta["year_be"],
                    "year_ce": year_ce,
                    "category_id": cid,
                    "news_url": news_url,
                    "news_title": title,
                    "file_url": file_url,
                    "status": "failed",
                    "error_message": str(e)[:500],
                }
            )
    return files, total_bytes


def download_file(session: requests.Session, url: str, dest: Path) -> tuple[int, int]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = session.get(url, timeout=300, stream=True)
    size = 0
    with dest.open("wb") as f:
        for chunk in r.iter_content(65536):
            if chunk:
                f.write(chunk)
                size += len(chunk)
    return r.status_code, size


def crawl_year(
    year_be: int,
    inventory_only: bool,
    run_id: str,
    skip_posts: bool = False,
) -> dict:
    cfg = load_sources()["mots"]
    session = _session(cfg)
    year_entry = next(y for y in cfg["year_categories"] if y["year_be"] == year_be)
    year_ce = year_entry["year_ce"]
    root_id = year_entry["category_id"]

    tree_path = CONFIG / "mots_category_tree.json"
    if tree_path.exists():
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
    else:
        tree = build_tree(session, cfg)
        save_tree(tree)

    cat_ids = category_descendants(tree, root_id)

    stats = {"categories": len(cat_ids), "posts": 0, "files": 0, "bytes": 0, "inventory": []}

    meta_base = {"year_be": year_be, "year_ce": year_ce}

    for cid in sorted(cat_ids):
        print(f"  category {cid}...", flush=True)
        cat_html = fetch_category_html(session, cid)
        cat_url = f"https://www.mots.go.th/news/category/{cid}"
        cat_title = parse_title(cat_html)
        posts = list_posts(session, cid, cat_html, cfg["rss_category_url"])
        stats["posts"] += len(posts)
        time.sleep(cfg["rate_limit_seconds"])

        cat_attach = parse_attachment_hrefs(cat_html)
        if inventory_only:
            for a in cat_attach:
                stats["inventory"].append({"category_id": cid, "news_url": cat_url, "file_url": a})
            for post in posts:
                stats["inventory"].append(
                    {"category_id": cid, "news_url": post["link"], "title": post["title"]}
                )
            continue

        if cat_attach and not known_news_url("mots", cat_url):
            meta = {
                **meta_base,
                "category_id": cid,
                "news_url": cat_url,
                "news_title": cat_title,
            }
            f, b = download_attachments(
                session, cat_attach, run_id=run_id, meta=meta, storage_slug=f"_cat_{cid}"
            )
            stats["files"] += f
            stats["bytes"] += b
            append_row(
                {
                    "run_id": run_id,
                    "source": "mots",
                    **meta,
                    "status": "ok",
                    "error_message": "category_listing",
                }
            )

        if skip_posts:
            continue
        for post in posts:
            news_url = post["link"]
            title = post["title"] or cat_title
            if known_news_url("mots", news_url):
                continue
            meta = {
                **meta_base,
                "category_id": cid,
                "news_url": news_url,
                "news_title": title,
            }
            attach_urls = discover_attachments_http(session, news_url)
            if not attach_urls and _news_numeric_id(news_url) >= 100000:
                try:
                    attach_urls = discover_attachments_playwright(
                        news_url, title, cfg, run_id, meta
                    )
                except Exception as e:
                    append_row(
                        {
                            "run_id": run_id,
                            "source": "mots",
                            **meta,
                            "status": "error",
                            "error_message": str(e)[:500],
                        }
                    )
                    continue
            time.sleep(cfg["rate_limit_seconds"])
            slug = slug_from_url(news_url)
            if not attach_urls:
                append_row(
                    {
                        "run_id": run_id,
                        "source": "mots",
                        **meta,
                        "status": "ok",
                        "error_message": "no_attachments",
                    }
                )
                continue
            f, b = download_attachments(
                session, attach_urls, run_id=run_id, meta=meta, storage_slug=slug
            )
            stats["files"] += f
            stats["bytes"] += b
            append_row(
                {
                    "run_id": run_id,
                    "source": "mots",
                    **meta,
                    "status": "ok",
                }
            )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MOTS statistics crawler")
    parser.add_argument("--year-be", type=int, help="Single BE year e.g. 2561")
    parser.add_argument("--all", action="store_true", help="All configured years")
    parser.add_argument("--build-tree", action="store_true", help="Only build category tree")
    parser.add_argument("--inventory-only", action="store_true", help="Dry-run inventory")
    parser.add_argument(
        "--skip-posts",
        action="store_true",
        help="Only category listing attachments (faster; skip per-news Playwright)",
    )
    args = parser.parse_args(argv)

    cfg = load_sources()["mots"]
    session = _session(cfg)

    if args.build_tree or (not args.year_be and not args.all):
        tree = build_tree(session, cfg)
        path = save_tree(tree)
        print(f"Category tree: {path} ({len(tree)} nodes)")
        if not args.year_be and not args.all:
            return 0

    years = (
        [y["year_be"] for y in cfg["year_categories"]]
        if args.all
        else [args.year_be]
    )
    run_id = new_run_id()
    for yb in years:
        if yb is None:
            continue
        print(f"Crawling BE {yb}...")
        stats = crawl_year(yb, args.inventory_only, run_id, skip_posts=args.skip_posts)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
