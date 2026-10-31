"""Re-export HII staging from ingest (idempotent rebuild)."""

from pathlib import Path

from src.common.paths import RAW, STAGING
from src.ingest.hii import stage


def rebuild() -> Path:
    csv_path = RAW / "hii" / "spatial-rain-hii.csv"
    if not csv_path.exists():
        raise FileNotFoundError("Run python -m src.ingest.hii first")
    return stage(csv_path)
