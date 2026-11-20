"""Content hashes and the per-month audit record. Wall-clock time is not included."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_frame(frame: pd.DataFrame) -> str:
    ordered = frame.copy()
    sort_columns = [column for column in ("year_month", "province_id") if column in ordered.columns]
    if sort_columns:
        ordered = ordered.sort_values(sort_columns)
    ordered = ordered.reindex(columns=sorted(ordered.columns))
    payload = ordered.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
