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
