import csv
from pathlib import Path

import pytest

from src.common import manifest as m


@pytest.fixture
def isolated_manifest(tmp_path, monkeypatch):
    path = tmp_path / "manifest_downloads.csv"
    monkeypatch.setattr(m, "MANIFEST_DIR", tmp_path)
    monkeypatch.setattr(m, "MANIFEST_PATH", path)
    return path


def test_append_idempotent_columns(isolated_manifest):
    m.append_row({"run_id": "r1", "source": "hii", "status": "ok", "file_url": "http://x"})
    rows = m.load_manifest()
    assert len(rows) == 1
    assert rows[0]["source"] == "hii"
    assert rows[0]["first_seen_at"]


def test_known_url_keeps_ok_when_latest_news_failed(isolated_manifest):
    m.append_row(
        {
            "run_id": "r1",
            "source": "mots",
            "status": "ok",
            "file_url": "http://file",
            "news_url": "http://news",
        }
    )
    m.append_row(
        {
            "run_id": "r2",
            "source": "mots",
            "status": "failed",
            "file_url": "http://file",
            "news_url": "http://news",
        }
    )
    found = m.known_url("mots", "http://file")
    assert found is not None
    assert found["status"] == "ok"
    assert m.known_news_url("mots", "http://news") is False
