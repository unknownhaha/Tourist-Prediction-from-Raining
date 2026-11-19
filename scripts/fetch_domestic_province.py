"""Download MOTS 'สถิตินักท่องเที่ยวภายในประเทศ (จำแนกตามภูมิภาคและจังหวัด)' Excel files, 2019+.

Usage: python fetch_domestic_province.py <output_dir>
"""
import csv
import html as htmlmod
import re
import sys
import time
import urllib.request
from pathlib import Path

CATEGORIES = {  # year_ce: category_id (from config/mots_category_tree.json)
    2019: 618,
    2020: 740,
    2021: 741,
    2022: 766,
    2023: 796,
    2024: 812,
    2025: 829,
}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
LINK_RE = re.compile(
    r'<a href="(https?://[^"]+\.(?:xlsx|xls))"[^>]*>.*?<img alt="([^"]*)"', re.I | re.S
)
PAGE_RE = re.compile(r'href="(/news/category/\d+\?page=\d+)"')


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def main(out: Path) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for year, cid in CATEGORIES.items():
        pages = [f"https://www.mots.go.th/news/category/{cid}"]
        seen_pages, found = set(), {}
        while pages:
            p = pages.pop(0)
            if p in seen_pages:
                continue
            seen_pages.add(p)
            h = get(p).decode("utf-8", "replace")
            time.sleep(1.5)
            for url, title in LINK_RE.findall(h):
                found.setdefault(url, htmlmod.unescape(title).strip())
            for pp in PAGE_RE.findall(h):
                if f"/category/{cid}?" in pp:
                    pages.append("https://www.mots.go.th" + pp)
        print(f"{year} (cat {cid}): {len(found)} files")
        for url, title in found.items():
            dest = out / str(year) / url.rsplit("/", 1)[-1]
            status = "exists"
            if not dest.exists():
                try:
                    data = get(url)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    status = "ok"
                    time.sleep(1.5)
                except Exception as e:  # noqa: BLE001
                    status = f"error: {e}"
            size = dest.stat().st_size if dest.exists() else 0
            print(f"  [{status}] {dest.name} {size:,}B  {title}")
            rows.append(
                {"year_ce": year, "category_id": cid, "title": title, "file_url": url,
                 "local_path": str(dest), "size_bytes": size, "status": status}
            )
    with (out / "index.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
