"""Input gate run before any matrix is built for month t (minimal version, see README).

* DQ v2 (``experiments.new_features.dq``) on panel rows <= t-1, i.e. every value a
  feature row of month t can read. Month t's label is never part of the check.
* The new feature columns of month t's rows: finite where expected, exp_lr in [-5, 5],
  recovery ratios >= 0, event flags in {0, 1}.
Raises ``GateError`` on a hard failure.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from experiments.new_features.dq import DataQualityError, assess_feature_inputs, load_config
from experiments.new_features.features import build_new_features
from src.model.leakage import shift_year_month


class GateError(RuntimeError):
    pass


def check_features(rows: pd.DataFrame) -> dict:
    problems = []
    for column in ("exp_lr_recent", "exp_lr3_recent"):
        v = rows[column].to_numpy(dtype=float)
        if not np.isfinite(v).all() or (np.abs(v) > 5).any():
            problems.append(f"{column} not finite or outside [-5, 5]")
    for column in ("rec_lag1", "rec3", "rec_lag12", "nat_rec_lag1", "nat_rec_lag12", "seasonal_last_month_forecast"):
        v = rows[column].to_numpy(dtype=float)
        if not np.isfinite(v).all() or (v < 0).any():
            problems.append(f"{column} not finite or negative")
    for column in ("event_now", "event_lag1"):
        if not np.isin(rows[column].to_numpy(dtype=float), (0.0, 1.0)).all():
            problems.append(f"{column} not in {{0, 1}}")
    return {"n_rows": int(len(rows)), "problems": problems}


def check_inputs(panel: pd.DataFrame, table: pd.DataFrame, as_of: str, config: dict | None = None) -> dict:
    """DQ on what month ``as_of`` may read (panel <= as_of - 1) plus its feature rows."""
    visible_end = shift_year_month(as_of, 1)
    visible = panel.loc[panel["year_month"].astype(str) <= visible_end]
    cfg = {**(load_config() if config is None else config), "date_end": visible_end}
    try:
        dq = assess_feature_inputs(visible, build_new_features(visible), cfg)
    except DataQualityError as exc:
        raise GateError(f"{as_of}: {exc}") from exc
    features = check_features(table.loc[table["year_month"] == as_of])
    if features["problems"]:
        raise GateError(f"{as_of}: " + "; ".join(features["problems"]))
    acc = dq["accuracy"]
    return {
        "as_of": as_of,
        "visible_through": visible_end,
        "dq_overall": dq["scores"]["overall"],
        "dq_accuracy_normal": acc["accuracy_normal"],
        "dq_accuracy_covid": acc["accuracy_covid"],
        "dq_accuracy_covid_base": acc["accuracy_covid_base"],
        "dq_hard_fail": dq["hard_fail"],
        "feature_rows_checked": features["n_rows"],
    }
