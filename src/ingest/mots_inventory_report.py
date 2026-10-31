"""Dry-run MOTS inventory report (plan step 4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from src.common.config_loader import load_sources
from src.common.manifest import load_manifest
from src.common.paths import CONFIG, ROOT
from src.common.manifest import new_run_id
from src.ingest.mots_crawler import crawl_year


def main() -> int:
    cfg = load_sources()["mots"]
    run_id = new_run_id()
    rows = []
    total_posts = 0
    total_files = 0
    for y in cfg["year_categories"]:
        yb = y["year_be"]
        stats = crawl_year(yb, inventory_only=True, run_id=run_id)
        inv = stats.get("inventory", [])
        file_urls = [i for i in inv if i.get("file_url")]
        post_urls = [i for i in inv if not i.get("file_url")]
        total_posts += len(post_urls)
        total_files += len(file_urls)
        rows.append(
            {
                "year_be": yb,
                "year_ce": y["year_ce"],
                "categories": stats["categories"],
                "posts": len(post_urls),
                "attachment_links": len(file_urls),
            }
        )

    manifest = pd.DataFrame(load_manifest())
    mots = manifest[manifest["source"] == "mots"] if len(manifest) else pd.DataFrame()
    disk_bytes = 0
    raw = ROOT / "data" / "raw" / "mots"
    if raw.exists():
        disk_bytes = sum(f.stat().st_size for f in raw.rglob("*") if f.is_file())

    report = {
        "by_year": rows,
        "totals": {
            "inventory_posts": total_posts,
            "inventory_attachment_links": total_files,
            "downloaded_files_on_disk": len(list(raw.rglob("*"))) if raw.exists() else 0,
            "disk_bytes": disk_bytes,
        },
        "manifest_status": mots.groupby("status").size().to_dict() if len(mots) else {},
    }
    out = CONFIG / "mots_inventory_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
