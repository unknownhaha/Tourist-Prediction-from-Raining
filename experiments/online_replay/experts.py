"""The two ML experts of the Champion and the variants used in the A/B steps.

``fit_predict`` is a port of the data-review harness (``ab.py::fit_predict``),
generalised to both targets:

* logdiff expert = ``rf|logdiff|ext|d10_leaf3`` on the extended matrix + extra columns;
* level expert   = ``rf|level|base|d10_leaf3`` on the base matrix + extra columns.

Rolling origin: rows 2019-01..train_end are the fit, target months must be after
train_end, and the rain-anomaly baseline is capped at train_end (``matrix_for``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from experiments.backtest.run import build_table, training_window
from experiments.beat_seasonal_naive.run import BASE_FEATURES, _fit_estimator, _rain_history, matrix_for, reconstruct
from experiments.new_features.run import LEVEL, LOGDIFF, _load
from experiments.online_replay.features import EXP_COLUMNS, REC_COLUMNS, add_features, guest_wide, normal_years
from src.model.leakage import shift_year_month

HERE = Path(__file__).resolve().parent
COVID = ("is_covid", "is_covid_lag12")
RAIN = ("rain_lag_1", "rain_roll3", "rain_anomaly")
# Event and COVID variants. The level expert gets the same COVID/recovery/event columns.
VARIANTS = {
    "C0": {"add": (), "drop": (), "desc": "binary COVID flags is_covid, is_covid_lag12 (current)"},
    "C3": {"add": REC_COLUMNS + ("event_lag1",), "drop": COVID,
           "desc": "recovery level vs last two normal years + lagged event flag (no binary flags)"},
    "C4": {"add": (), "drop": COVID, "desc": "no COVID information at all (report-only 'without' row)"},
}
SELECTABLE = ("C0", "C3")


def months_of(year: int) -> list[str]:
    return [f"{year}-{m:02d}" for m in range(1, 13)]


@dataclass
class Context:
    panel: pd.DataFrame
    table: pd.DataFrame
    history: dict
    normal: list[int]
    cache: dict = field(default_factory=dict)

    @classmethod
    def build(cls, panel: pd.DataFrame | None = None, events: list[dict] | None = None) -> "Context":
        panel = _load() if panel is None else panel
        normal = normal_years(panel)
        table = add_features(build_table(panel), guest_wide(panel), normal, events)
        return cls(panel, table, _rain_history(panel), normal)


def fit_predict(ctx: Context, target: str, train_end: str, months: list[str], add=(), drop=(),
                seed: int = 42, table: pd.DataFrame | None = None, exp: tuple[str, ...] = EXP_COLUMNS) -> tuple[np.ndarray, pd.DataFrame]:
    """Fit one expert on 2019-01..train_end and predict ``months`` (all after train_end)."""
    if any(m <= train_end for m in months):
        raise AssertionError(f"target month not after train_end {train_end}")
    if "event_now" in add:
        raise AssertionError("event_now is an oracle column and never enters a model")
    T = ctx.table if table is None else table
    part = T.loc[T["year_month"].isin(months)]
    vis = pd.concat([training_window(T, train_end), part]).sort_values(["year_month", "province_id"])
    feature_set = "ext" if target == "logdiff" else "base"
    M = matrix_for(vis, ctx.history, feature_set, train_end)
    extra = list(exp) + list(add) if target == "logdiff" else list(add)
    if extra:
        M = pd.concat([M, vis.loc[M.index, extra].apply(pd.to_numeric, errors="coerce")], axis=1)
    M = M.drop(columns=[c for c in drop if c in M.columns])
    tr = vis["year_month"].between("2019-01", train_end).to_numpy()
    y = vis["guest_arrivals_total"].to_numpy(dtype=float)
    l12 = vis["guest_lag_12"].to_numpy(dtype=float)
    ok = tr & np.isfinite(y) & np.isfinite(l12)
    if target == "logdiff":
        ok &= (y > 0) & (l12 > 0)
        with np.errstate(all="ignore"):
            label = np.log(y) - np.log(l12)
    else:
        label = y
    base = [c for c in BASE_FEATURES if c in M.columns]
    ok &= np.isfinite(M[base].to_numpy(dtype=float)).all(axis=1)
    rows = M[ok]
    med = rows.median()
    X = rows.fillna(med).to_numpy(dtype=float)
    scaler = StandardScaler().fit(X)
    est = _fit_estimator(LOGDIFF if target == "logdiff" else LEVEL)
    est.set_params(n_jobs=8, random_state=seed)
    est.fit(scaler.transform(X), label[ok])
    tm = vis["year_month"].isin(months).to_numpy()
    raw = est.predict(scaler.transform(M[tm].fillna(med).to_numpy(dtype=float)))
    pred = reconstruct(target, raw, l12[tm])
    return pd.Series(pred, index=vis.index[tm]).reindex(part.index).to_numpy(dtype=float), part


def monthly(ctx: Context, target: str, variant: str, months: list[str], drop_extra=(), seed: int = 42,
            exp: tuple[str, ...] = EXP_COLUMNS) -> np.ndarray:
    """Predict-then-learn: month t from a fit on 2019-01..t-1 (cached per month)."""
    spec = VARIANTS[variant]
    out = []
    for month in months:
        tag = variant if exp == EXP_COLUMNS or target == "level" else f"{variant}+{'+'.join(exp) or 'noexp'}"
        key = (target, tag, tuple(drop_extra), seed, month)
        if key not in ctx.cache:
            pred, _ = fit_predict(ctx, target, shift_year_month(month, 1), [month], spec["add"],
                                  tuple(spec["drop"]) + tuple(drop_extra), seed, exp=exp)
            ctx.cache[key] = pred
        out.append(ctx.cache[key])
    return np.concatenate(out)


def part_of(ctx: Context, months: list[str]) -> pd.DataFrame:
    return ctx.table.loc[ctx.table["year_month"].isin(months)]


def save_cache(ctx: Context, path: Path) -> None:
    rows = []
    for (target, variant, drop, seed, month), pred in ctx.cache.items():
        part = part_of(ctx, [month])
        rows.append(pd.DataFrame({"target": target, "variant": variant, "drop": "|".join(drop), "seed": seed,
                                  "year_month": month, "province_id": part["province_id"].to_numpy(), "pred": pred}))
    if rows:
        pd.concat(rows, ignore_index=True).to_parquet(path, index=False)


def load_cache(ctx: Context, path: Path) -> None:
    if not path.exists():
        return
    frame = pd.read_parquet(path)
    for (target, variant, drop, seed, month), group in frame.groupby(["target", "variant", "drop", "seed", "year_month"]):
        part = part_of(ctx, [month])
        g = group.set_index("province_id").reindex(part["province_id"].to_numpy())
        ctx.cache[(target, variant, tuple(d for d in drop.split("|") if d), int(seed), month)] = g["pred"].to_numpy(dtype=float)
