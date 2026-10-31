from pathlib import Path

import yaml

from src.common.paths import CONFIG


def load_sources() -> dict:
    with (CONFIG / "sources.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)
