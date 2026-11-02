import pandas as pd

from src.qa.manifest_summary import latest_manifest_df


def test_blank_file_url_does_not_collapse_keys(monkeypatch):
    rows = [
        {"file_url": "", "news_url": "http://a/1", "status": "error", "downloaded_at": "2020-01-01", "first_seen_at": "2020-01-01"},
        {"file_url": "", "news_url": "http://a/2", "status": "ok", "downloaded_at": "2020-01-02", "first_seen_at": "2020-01-02"},
        {"file_url": "http://files/x.xlsx", "news_url": "", "status": "failed", "downloaded_at": "2020-01-03", "first_seen_at": "2020-01-03"},
    ]

    def fake_load():
        return rows

    import src.qa.manifest_summary as ms

    monkeypatch.setattr(ms, "load_manifest", fake_load)
    latest = latest_manifest_df()
    assert len(latest) == 3
    assert set(latest["status"]) >= {"error", "ok", "failed"}
