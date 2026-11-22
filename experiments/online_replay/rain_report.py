"""How much rain moves the forecast. Rain stays in the Champion (lagged only).

* Drop-rain ablation inside the frozen Champion (both experts lose rain_lag_1, rain_roll3,
  rain_anomaly), monthly, 2023-2025.
* Lagged-rain incremental R^2 on the YoY log-growth label (port of data_review/rain.py,
  OLS with province-clustered standard errors, numpy).
* Same-month wettest-quintile effect: descriptive only, same-month rain is NOT a model input.
* RF permutation importance of the rain columns in the logdiff expert (frozen fit on ..Y-1, Y = 2024, 2025).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from experiments.online_replay import experts as X
from experiments.online_replay import selection as S
from src.stream.metrics import wape

RAIN_MODEL_COLUMNS = ("rain_lag_1", "rain_roll3", "rain_anomaly")


def ols_cluster(y: np.ndarray, X_: np.ndarray, groups: np.ndarray) -> dict:
    """OLS with CR1 cluster-robust covariance (statsmodels cov_type='cluster' defaults)."""
    n, k = X_.shape
    xtx_inv = np.linalg.inv(X_.T @ X_)
    beta = xtx_inv @ X_.T @ y
    u = y - X_ @ beta
    meat = np.zeros((k, k))
    uniq = np.unique(groups)
    for g in uniq:
        s = X_[groups == g].T @ u[groups == g]
        meat += np.outer(s, s)
    G = len(uniq)
    c = G / (G - 1) * (n - 1) / (n - k)
    V = c * xtx_inv @ meat @ xtx_inv
    se = np.sqrt(np.diag(V))
    z = beta / se
    p = 2 * stats.norm.sf(np.abs(z))
    r2 = 1 - (u @ u) / ((y - y.mean()) @ (y - y.mean()))
    return {"beta": beta, "se": se, "p": p, "r2": float(r2), "resid": u, "n": n}


def regression_frame(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    p["Y"] = p["guest_arrivals_total"]
    p = p.sort_values(["province_id", "year_month"])
    g = p.groupby("province_id")
    p["lag12"], p["lag1"], p["lag13"] = g.Y.shift(12), g.Y.shift(1), g.Y.shift(13)
    with np.errstate(all="ignore"):
        p["lr"] = np.log(p.Y.where(p.Y > 0) / p.lag12.where(p.lag12 > 0))
        p["mom"] = np.log(p.lag1.where(p.lag1 > 0) / p.lag13.where(p.lag13 > 0))
    p["rain_l12"] = g.rain_avg_mm.shift(12)
    p["d_rain_now"] = np.log1p(p.rain_avg_mm) - np.log1p(p.rain_l12)
    p["d_rain_l1"] = p.groupby("province_id").d_rain_now.shift(1)
    p["ranom"] = p.rain_avg_mm - p.groupby(["province_id", "month"]).rain_avg_mm.transform("mean")
    p["ranom_l1"] = p.groupby("province_id").ranom.shift(1)
    return p[p["year"].isin([2019, 2024, 2025])].dropna(subset=["lr", "mom", "d_rain_now", "d_rain_l1"])


def rain_stats(panel: pd.DataFrame) -> tuple[list[dict], dict]:
    N = regression_frame(panel)
    groups = N["province_id"].astype(str).to_numpy()
    rows, fits = [], {}
    for cols in (["mom"], ["mom", "d_rain_l1"], ["mom", "ranom_l1"], ["mom", "d_rain_now"]):
        Xm = np.column_stack([np.ones(len(N))] + [N[c].to_numpy(dtype=float) for c in cols])
        f = ols_cluster(N["lr"].to_numpy(dtype=float), Xm, groups)
        fits["+".join(cols)] = f
        rain_col = cols[-1] if len(cols) > 1 else None
        rows.append({"section": "regression", "stat": "r2", "spec": "lr ~ " + " + ".join(cols), "value": f["r2"],
                     "rain_coef": float(f["beta"][-1]) if rain_col else None, "rain_p": float(f["p"][-1]) if rain_col else None,
                     "model_input": rain_col != "d_rain_now", "n": f["n"]})
    base = fits["mom"]["r2"]
    for spec, label in (("mom+d_rain_l1", "lagged rain (log YoY change, t-1)"), ("mom+ranom_l1", "lagged rain anomaly (t-1)"),
                        ("mom+d_rain_now", "same-month rain (descriptive, not a model input)")):
        rows.append({"section": "regression", "stat": "delta_r2", "spec": label, "value": fits[spec]["r2"] - base,
                     "rain_coef": float(fits[spec]["beta"][-1]), "rain_p": float(fits[spec]["p"][-1]),
                     "model_input": "same-month" not in label, "n": fits[spec]["n"]})
    # Wettest-quintile effect: residual of lr ~ mom by same-month rain anomaly quintile.
    N = N.assign(res=fits["mom"]["resid"])
    sd = N.groupby(["province_id", "month"]).rain_avg_mm.transform("std").replace(0, np.nan)
    N["rq"] = pd.qcut(N.ranom / sd, 5, labels=False, duplicates="drop")
    q = N.groupby("rq").res.mean()
    for k, v in q.items():
        rows.append({"section": "quintile", "stat": f"mean_yoy_residual_log_q{int(k) + 1}", "spec": "same-month rain anomaly quintile (1=driest, 5=wettest)",
                     "value": float(v), "model_input": False, "n": int((N.rq == k).sum())})
    wet = float(q.iloc[-1] - N.loc[N.rq != q.index[-1], "res"].mean())
    rows.append({"section": "quintile", "stat": "wettest_vs_other_quintiles_pct", "spec": "exp(diff)-1, descriptive", "value": float(np.expm1(wet) * 100),
                 "model_input": False, "n": int(len(N))})
    rows.append({"section": "quintile", "stat": "wettest_vs_driest_quintile_pct", "spec": "exp(q5-q1)-1, descriptive", "value": float(np.expm1(q.iloc[-1] - q.iloc[0]) * 100),
                 "model_input": False, "n": int(len(N))})
    return rows, {"q": q}


def permutation_importance(ctx: X.Context, sel: dict, year: int, repeats: int = 5) -> list[dict]:
    """Frozen logdiff expert fit on ..(year-1)-12; WAPE increase when one rain column is shuffled in year's rows."""
    months = X.months_of(year)
    spec = X.VARIANTS[sel["covid_variant"]]
    base, part = X.fit_predict(ctx, "logdiff", f"{year - 1}-12", months, spec["add"], spec["drop"])
    y = part["guest_arrivals_total"].to_numpy(dtype=float)
    w0 = wape(y, base)
    rng = np.random.default_rng(42)
    out = []
    for col in ("rain_lag_1", "rain_roll3"):
        deltas = []
        for _ in range(repeats):
            t = ctx.table.copy()
            mask = t["year_month"].isin(months)
            t.loc[mask, col] = rng.permutation(t.loc[mask, col].to_numpy())
            pred, _ = X.fit_predict(ctx, "logdiff", f"{year - 1}-12", months, spec["add"], spec["drop"], table=t)
            deltas.append(wape(y, pred) - w0)
        out.append({"section": "permutation", "stat": f"wape_increase_pt_{col}_{year}", "spec": f"frozen logdiff expert, fit ..{year - 1}-12, {repeats} shuffles",
                    "value": float(np.mean(deltas) * 100), "model_input": True, "n": int(len(y))})
    out.append({"section": "permutation", "stat": f"note_{year}", "spec": "rain_anomaly is recomputed from rain_lag_1 inside the matrix, so shuffling rain_lag_1 also shuffles it", "value": None})
    return out


def stage_rain(ctx: X.Context) -> pd.DataFrame:
    from experiments.online_replay.run import _write, champion
    cfg = S.load_config()
    sel = S.frozen_choice(cfg)
    abl = []
    for year in (2023, 2024, 2025):
        part = X.part_of(ctx, X.months_of(year))
        y = part["guest_arrivals_total"].to_numpy(dtype=float)
        with_rain, _, _ = champion(ctx, sel, year)
        no_rain, _, _ = champion(ctx, sel, year, drop=RAIN_MODEL_COLUMNS)
        abl.append({"year": year, "champion_wape_pct": wape(y, with_rain) * 100, "no_rain_wape_pct": wape(y, no_rain) * 100,
                    "diff_pt_no_rain_minus_with": (wape(y, no_rain) - wape(y, with_rain)) * 100})
    abl = pd.DataFrame(abl)
    _write("rain_ablation.csv", abl)
    rows, _ = rain_stats(ctx.panel)
    for r in abl.to_dict("records"):
        rows.append({"section": "ablation", "stat": f"drop_rain_diff_pt_{r['year']}", "spec": "Champion without rain minus Champion (WAPE pt; + = rain helps)",
                     "value": r["diff_pt_no_rain_minus_with"], "model_input": True})
    for year in (2024, 2025):
        rows += permutation_importance(ctx, sel, year)
    frame = pd.DataFrame(rows)
    _write("rain_effect.csv", frame)
    print(abl.round(3).to_string(), "\n", frame.round(4).to_string(), flush=True)
    return frame
