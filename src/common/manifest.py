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


# Latest successful file URL, and latest status per news URL.
# Rebuilt when the manifest path or size changes; append_row updates it in place.
_index_path: str | None = None
_index_size: int | None = None
_ok_by_file: dict[tuple[str, str], dict[str, str]] = {}
_latest_news_status: dict[tuple[str, str], str] = {}


def _row_strings(row: dict[str, Any]) -> dict[str, str]:
    return {col: "" if row.get(col) is None else str(row.get(col)) for col in MANIFEST_COLUMNS}


def _sync_index() -> None:
    """Load lookup maps once per manifest size. known_* used to reread the CSV every call."""
    global _index_path, _index_size, _ok_by_file, _latest_news_status
    _ensure_manifest()
    path = str(MANIFEST_PATH.resolve())
    size = MANIFEST_PATH.stat().st_size
    if _index_path == path and _index_size == size:
        return
    ok_by_file: dict[tuple[str, str], dict[str, str]] = {}
    latest_news: dict[tuple[str, str], str] = {}
    for row in load_manifest():
        source = row.get("source", "")
        file_url = row.get("file_url", "")
        if file_url and row.get("status") == "ok":
            ok_by_file[(source, file_url)] = row
        news_url = row.get("news_url", "")
        if news_url:
            latest_news[(source, news_url)] = row.get("status", "")
    _ok_by_file = ok_by_file
    _latest_news_status = latest_news
    _index_path = path
    _index_size = size


def _remember_row(full: dict[str, str]) -> None:
    global _index_size
    if _index_path != str(MANIFEST_PATH.resolve()):
        return
    source = full.get("source", "")
    file_url = full.get("file_url", "")
    if file_url and full.get("status") == "ok":
        _ok_by_file[(source, file_url)] = full
    news_url = full.get("news_url", "")
    if news_url:
        _latest_news_status[(source, news_url)] = full.get("status", "")
    _index_size = MANIFEST_PATH.stat().st_size


def known_sha256(source: str, digest: str) -> bool:
    for row in load_manifest():
        if row.get("source") == source and row.get("sha256") == digest and row.get("status") == "ok":
            return True
    return False


def known_url(source: str, file_url: str) -> dict[str, str] | None:
    """Most recent ok row for this file URL. A later failure does not hide an earlier ok."""
    _sync_index()
    return _ok_by_file.get((source, file_url))


def known_news_url(source: str, news_url: str) -> bool:
    """True only when the latest row for this news URL succeeded."""
    _sync_index()
    return _latest_news_status.get((source, news_url)) == "ok"


def append_row(row: dict[str, Any]) -> None:
    _ensure_manifest()
    now = datetime.now(timezone.utc).isoformat()
    full = {col: "" for col in MANIFEST_COLUMNS}
    full.update(row)
    if not full.get("first_seen_at"):
        full["first_seen_at"] = now
    if not full.get("downloaded_at") and full.get("status") == "ok":
        full["downloaded_at"] = now
    stored = _row_strings(full)
    with MANIFEST_PATH.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS).writerow(stored)
    _remember_row(stored)
