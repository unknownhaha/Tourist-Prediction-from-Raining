"""Choices made on 2024 only, frozen in config.json before 2023/2025 are scored."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.online_trials.models import ewa
from src.stream.metrics import wape

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
SELECT_YEAR = 2024
# Fewer features first; used to break near-ties (C4 is never a candidate).
FEATURE_ORDER = ("C0", "C1", "C2", "C3")


class SelectionError(RuntimeError):
    pass


def _only_2024(part: pd.DataFrame) -> None:
    years = set(part["year_month"].astype(str).str[:4])
    if years != {str(SELECT_YEAR)}:
        raise SelectionError(f"selection received non-2024 rows: {sorted(years)}")


def ewa_wape(part: pd.DataFrame, experts: dict[str, np.ndarray], eta: float) -> float:
    pred, _ = ewa(part, experts, eta)
    return wape(part["guest_arrivals_total"].to_numpy(dtype=float), pred)


def select_eta(part: pd.DataFrame, experts: dict[str, np.ndarray], etas: list[float]) -> tuple[float, dict]:
    _only_2024(part)
    scores = {float(eta): ewa_wape(part, experts, eta) for eta in etas}
    best = min(scores, key=lambda k: (scores[k], k))
    return best, scores


def select_covid(part: pd.DataFrame, experts_by_variant: dict[str, dict[str, np.ndarray]], etas: list[float],
                 tie_pt: float) -> dict:
    """Lowest 2024 EWA WAPE; if the best is within tie_pt points of a variant with fewer
    features, take the one with fewer features."""
    _only_2024(part)
    grid = {}
    for variant, experts in experts_by_variant.items():
        eta, scores = select_eta(part, experts, etas)
        grid[variant] = {"eta": eta, "wape_2024": scores[eta], "eta_grid": scores}
    best = min(grid, key=lambda v: (grid[v]["wape_2024"], FEATURE_ORDER.index(v)))
    chosen = best
    for v in sorted(grid, key=FEATURE_ORDER.index):
        if FEATURE_ORDER.index(v) < FEATURE_ORDER.index(chosen) and grid[v]["wape_2024"] - grid[best]["wape_2024"] < tie_pt / 100:
            chosen = v
            break
    return {"chosen": chosen, "best_raw": best, "grid": grid, "tie_break_pt": tie_pt}


def select_experts(part: pd.DataFrame, e2: dict[str, np.ndarray], e3: dict[str, np.ndarray], etas: list[float],
                   min_gain_pt: float) -> dict:
    _only_2024(part)
    eta2, s2 = select_eta(part, e2, etas)
    eta3, s3 = select_eta(part, e3, etas)
    gain = (s2[eta2] - s3[eta3]) * 100
    keep3 = gain >= min_gain_pt
    return {"chosen": "E3" if keep3 else "E2", "E2": {"eta": eta2, "wape_2024": s2[eta2], "eta_grid": s2},
            "E3": {"eta": eta3, "wape_2024": s3[eta3], "eta_grid": s3}, "gain_pt": gain, "min_gain_pt": min_gain_pt}


def load_config(path: Path | None = None) -> dict:
    return json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))


def _rounded(obj, digits: int = 10):
    """Round floats so a rerun with ~1e-15 RF float noise writes a byte-identical config.json."""
    if isinstance(obj, float):
        return round(obj, digits)
    if isinstance(obj, dict):
        return {k: _rounded(v, digits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_rounded(v, digits) for v in obj]
    return obj


def freeze(config: dict, selection: dict, path: Path | None = None) -> dict:
    config = dict(config)
    selection = _rounded(selection)
    config["selection"] = {**selection, "selected_on": SELECT_YEAR}
    (path or CONFIG_PATH).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config


def frozen_choice(config: dict) -> dict:
    """The held-out scorer only runs on a choice stamped as made on 2024."""
    sel = config.get("selection") or {}
    if sel.get("selected_on") != SELECT_YEAR:
        raise SelectionError("config.json has no choice stamped selected_on: 2024; refusing to score held-out years")
    return sel
