"""Stable repo-relative paths for staging metadata."""

from __future__ import annotations

from pathlib import Path

from src.common.paths import ROOT


def path_relative_to_root(path: Path | str) -> str:
    p = Path(path).resolve()
    try:
        return str(p.relative_to(ROOT.resolve()))
    except ValueError:
        return str(path).replace("\\", "/")
