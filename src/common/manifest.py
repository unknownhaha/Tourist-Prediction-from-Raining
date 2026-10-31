"""Append-only download manifest with checksum deduplication."""

from __future__ import annotations

import csv
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.common.paths import MANIFEST_DIR, MANIFEST_PATH

MANIFEST_COLUMNS = [
    "run_id",
    "source",
    "year_be",
    "year_ce",
    "category_id",
    "category_path",
    "news_url",
    "news_title",
    "file_url",
    "local_path",
    "file_type",
    "sha256",
    "size_bytes",
    "http_status",
    "first_seen_at",
    "downloaded_at",
    "status",
    "error_message",
]


def new_run_id() -> str:
    return str(uuid.uuid4())


def _ensure_manifest() -> None:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    if not MANIFEST_PATH.exists():
        with MANIFEST_PATH.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS).writeheader()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> list[dict[str, str]]:
    _ensure_manifest()
    with MANIFEST_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def known_sha256(source: str, digest: str) -> bool:
    for row in load_manifest():
        if row.get("source") == source and row.get("sha256") == digest and row.get("status") == "ok":
            return True
    return False


def known_url(source: str, file_url: str) -> dict[str, str] | None:
    for row in reversed(load_manifest()):
        if row.get("source") == source and row.get("file_url") == file_url and row.get("status") == "ok":
            return row
    return None


def known_news_url(source: str, news_url: str) -> bool:
    for row in reversed(load_manifest()):
        if row.get("source") == source and row.get("news_url") == news_url:
            return row.get("status") == "ok"
    return False


def append_row(row: dict[str, Any]) -> None:
    _ensure_manifest()
    now = datetime.now(timezone.utc).isoformat()
    full = {col: "" for col in MANIFEST_COLUMNS}
    full.update(row)
    if not full.get("first_seen_at"):
        full["first_seen_at"] = now
    if not full.get("downloaded_at") and full.get("status") == "ok":
        full["downloaded_at"] = now
    with MANIFEST_PATH.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS).writerow(full)
