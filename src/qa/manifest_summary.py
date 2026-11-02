"""Latest-status manifest summary."""

from __future__ import annotations

import pandas as pd

from src.common.manifest import load_manifest


def _blank_to_na(series: pd.Series) -> pd.Series:
    s = series.astype("string")
    s = s.replace("", pd.NA)
    s = s.where(s.str.strip().ne(""), pd.NA)
    return s


def latest_manifest_df() -> pd.DataFrame:
    rows = load_manifest()
    if not rows:
        return pd.DataFrame()
    mdf = pd.DataFrame(rows)
    for col in ("downloaded_at", "first_seen_at", "file_url", "news_url"):
        if col in mdf.columns:
            mdf[col] = _blank_to_na(mdf[col])
    mdf["ts"] = mdf["downloaded_at"].fillna(mdf["first_seen_at"])
    mdf["key"] = mdf["file_url"].fillna(mdf["news_url"])
    return mdf.sort_values("ts").groupby("key", dropna=False).tail(1)
