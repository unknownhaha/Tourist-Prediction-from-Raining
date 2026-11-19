"""Final data-quality / model-readiness check for data/clean/mots_domestic_province.

Uses all rows (COVID period included, is_covid NOT used as a feature) and scores simple
baselines with pandas + numpy only. Writes model_readiness_report.md next to the data.

Run from the project root:  python scripts/model_readiness_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "clean" / "mots_domestic_province"
RAIN_STAGING = ROOT / "data" / "staging" / "hii" / "fact_rainfall.parquet"

TEST_YEAR = 2025  # train <= 2024, test = 2025 (time-based split)


def wape(y, p):
    return float(np.abs(y - p).sum() / np.abs(y).sum() * 100)


def mape(y, p):
    m = y > 0
    return float(np.mean(np.abs(y[m] - p[m]) / y[m]) * 100)


def rmse_log(y, p):
    return float(np.sqrt(np.mean((np.log1p(y) - np.log1p(np.clip(p, 0, None))) ** 2)))


def add_rain_lags(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Rain lags from the continuous HII series (starts 2018-01), so 2019 rows also get lags."""
    r = pd.read_parquet(RAIN_STAGING).assign(province_id=lambda d: d.province_id.astype(int))
    if period == "month":
        r = r.sort_values(["province_id", "year_month"])
        g = r.groupby("province_id").avg_rain_mm
        r = r.assign(rain_lag1=g.shift(1), rain_lag12=g.shift(12))[["province_id", "year_month", "rain_lag1", "rain_lag12"]]
        return df.merge(r, on=["province_id", "year_month"], how="left")
    r = r.assign(quarter=(r.month - 1) // 3 + 1).groupby(["province_id", "year_ce", "quarter"], as_index=False).avg_rain_mm.sum()
    r = r.sort_values(["province_id", "year_ce", "quarter"])
    g = r.groupby("province_id").avg_rain_mm
    r = r.assign(rain_lag1=g.shift(1), rain_lag4=g.shift(4))[["province_id", "year_ce", "quarter", "rain_lag1", "rain_lag4"]]
    return df.merge(r, on=["province_id", "year_ce", "quarter"], how="left")


def ols_fe(train: pd.DataFrame, test: pd.DataFrame, target: str, season: str, rain_cols: list[str]):
    """log1p(target) ~ province FE + season dummies + year trend + log1p(rain features). numpy lstsq."""
    def design(d, cols=None):
        X = pd.get_dummies(d[["province_id", season]].astype(str), drop_first=False, dtype=float)
        X["year"] = d.year_ce - 2019
        for c in rain_cols:
            X[c] = np.log1p(d[c])
        X["const"] = 1.0
        return X if cols is None else X.reindex(columns=cols, fill_value=0.0)
    Xtr = design(train)
    beta, *_ = np.linalg.lstsq(Xtr.values, np.log1p(train[target].values), rcond=None)
    Xte = design(test, Xtr.columns)
    coef = dict(zip(Xtr.columns, beta))
    return np.expm1(Xte.values @ beta), {c: coef[c] for c in rain_cols}


def evaluate(df: pd.DataFrame, target: str, season: str, lag: int, rain_cols: list[str]) -> list[dict]:
    df = df.sort_values(["province_id", "year_ce", season]).copy()
    df["seasonal_naive"] = df.groupby("province_id")[target].shift(lag)
    train, test = df[df.year_ce < TEST_YEAR], df[df.year_ce == TEST_YEAR]
    y = test[target].values
    rows = []
    rows.append({"model": f"Seasonal naive (same {season} last year)", "pred": test["seasonal_naive"].values})
    m = train.groupby(["province_id", season])[target].mean().rename("p")
    rows.append({"model": f"Province × {season} mean (train)", "pred": test.join(m, on=["province_id", season]).p.values})
    m = train[train.year_ce == TEST_YEAR - 1].groupby(["province_id", season])[target].mean().rename("p")
    rows.append({"model": f"Province × {season} of {TEST_YEAR - 1}", "pred": test.join(m, on=["province_id", season]).p.values})
    p0, _ = ols_fe(train, test, target, season, [])
    rows.append({"model": "Log-linear FE (province + season + trend), no rain", "pred": p0})
    p1, coef = ols_fe(train, test, target, season, rain_cols)
    rows.append({"model": f"Log-linear FE + rain ({', '.join(rain_cols)})", "pred": p1, "coef": coef})
    # Seasonal naive + rain: Δlog(y) vs same period last year ~ Δlog(rain) (numpy lstsq)
    rc = rain_cols[0]
    df["dly"] = np.log1p(df[target]) - np.log1p(df["seasonal_naive"])
    df["dlr"] = np.log1p(df[rc]) - np.log1p(df.groupby("province_id")[rc].shift(lag))
    tr = df[(df.year_ce < TEST_YEAR) & df.dly.notna() & np.isfinite(df.dly)]
    X = np.c_[np.ones(len(tr)), tr.dlr.values]
    b, *_ = np.linalg.lstsq(X, tr.dly.values, rcond=None)
    te = df[df.year_ce == TEST_YEAR]
    pred = np.expm1(np.log1p(te.seasonal_naive.values) + b[0] + b[1] * te.dlr.values)
    rows.append({"model": f"Seasonal naive × rain change (Δlog {rc})", "pred": pred,
                 "coef": {f"Δlog {rc}": b[1], "intercept (avg growth)": b[0]}})
    for r in rows:
        r.update(WAPE=wape(y, r["pred"]), MAPE=mape(y, r["pred"]), RMSE_log=rmse_log(y, r["pred"]))
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    q = pd.read_parquet(OUT / "province_quarter.parquet")
    m = pd.read_parquet(OUT / "province_month.parquet")
    rep = ["# Model-readiness report (all rows, COVID included, `is_covid` not used as a feature)", ""]

    # ---- 1. structure ------------------------------------------------------------------------
    rep += ["## 1. Structure", "", "| check | province_quarter | province_month |", "|---|---|---|"]
    def s(d, keys):
        num = d.select_dtypes("number")
        return {
            "rows": f"{len(d):,}",
            "provinces × periods": f"{d.province_id.nunique()} × {d.drop_duplicates(keys[1:]).shape[0]}",
            "duplicate keys": int(d.duplicated(keys).sum()),
            "null cells": int(d.isna().sum().sum()),
            "inf cells": int(np.isinf(num).sum().sum()),
            "negative numeric cells": int((num < 0).sum().sum()),
            "balanced panel": bool(d.groupby("province_id").size().nunique() == 1),
        }
    sq, sm = s(q, ["province_id", "year_ce", "quarter"]), s(m, ["province_id", "year_month"])
    rep += [f"| {k} | {sq[k]} | {sm[k]} |" for k in sq]

    # ---- 2. columns that must not be features ----------------------------------------------
    const = [c for c in pd.concat([q, m], axis=1).columns.unique()
             if (c in q and q[c].nunique() == 1) or (c in m and m[c].nunique() == 1)]
    tourism_q = [c for c in q.columns if c.split("_")[0] in
                 ("visitors", "tourists", "excursionists", "guest", "revenue", "avg", "occupancy", "rooms")]
    rep += ["", "## 2. Column roles", "",
            f"- Constant columns (drop): {', '.join(sorted(const))}",
            "- Identifier / label columns (not numeric features): province_th, province_en (use province_id or region as categorical)",
            f"- Same-period tourism columns that leak the target ({len(tourism_q)}): every `visitors_*`, `tourists_*`, "
            "`excursionists_*`, `guest_arrivals_*`, `revenue_*`, `avg_expenditure_*`, `avg_length_of_stay_*`, "
            "`occupancy_rate_pct_total`, `rooms_total`. They are published together with the target "
            "(e.g. visitors_total = visitors_thai + visitors_foreign = tourists_total + excursionists_total exactly), "
            "so they cannot be inputs when predicting a tourism target for the same period.",
            "- `is_covid`: present, excluded from features in this run by request."]

    # ---- 3. targets ----------------------------------------------------------------------------
    rep += ["", "## 3. Target candidates", "", "| target | table | zeros | skew (raw) | skew (log1p) | max / median |",
            "|---|---|---:|---:|---:|---:|"]
    for t, d, name in [("visitors_total", q, "quarter"), ("guest_arrivals_total", q, "quarter"),
                       ("guest_arrivals_total", m, "month")]:
        v = d[t]
        rep.append(f"| {t} | {name} | {(v == 0).sum()} | {v.skew():.1f} | {np.log1p(v).skew():.2f} | {v.max() / v.median():,.0f}× |")
    rep.append("")
    rep.append("Raw targets are heavily right-skewed (Bangkok, Chon Buri, Phuket dominate); model `log1p(target)`.")

    # ---- 4. COVID impact while ignoring the flag ----------------------------------------------
    qq = q.assign(l=np.log1p(q.visitors_total))
    qq["dev"] = qq.l - qq.groupby(["province_id", "quarter"]).l.transform("median")
    by = qq.groupby(qq.is_covid).dev.agg(["mean", "std"])
    rep += ["", "## 4. What ignoring COVID means", "",
            f"- log(visitors) vs the province × quarter median: non-COVID rows mean {by.loc[False, 'mean']:+.2f} "
            f"(sd {by.loc[False, 'std']:.2f}); COVID rows mean {by.loc[True, 'mean']:+.2f} (sd {by.loc[True, 'std']:.2f}).",
            f"- {q.is_covid.mean() * 100:.0f}% of quarterly rows ({int(q.is_covid.sum())}) and "
            f"{m.is_covid.mean() * 100:.0f}% of monthly rows ({int(m.is_covid.sum())}) sit in the COVID window. "
            "Without the flag a model sees this drop as unexplained noise, or attributes part of it to rain.",
            "- Zeros in the targets are all COVID lockdown months/quarters; they are real and must stay."]

    # ---- 5. rain signal -----------------------------------------------------------------------
    q2 = add_rain_lags(q, "quarter")
    m2 = add_rain_lags(m, "month")
    rep += ["", "## 5. Rain features", "",
            f"- Rain lags built from the continuous HII series (from 2018-01): nulls in quarterly lags "
            f"{int(q2[['rain_lag1', 'rain_lag4']].isna().sum().sum())}, monthly lags {int(m2[['rain_lag1', 'rain_lag12']].isna().sum().sum())}.",
            "- Within-province correlation (both sides demeaned by province × season, so seasonality and province size are removed):", ""]
    for d, t, season, cols in [(q2, "visitors_total", "quarter", ["rain_sum_mm", "rain_lag1", "rain_lag4"]),
                               (m2, "guest_arrivals_total", "month", ["rain_avg_mm", "rain_lag1", "rain_lag12"])]:
        dd = d.assign(y=np.log1p(d[t]))
        grp = dd.groupby(["province_id", season])
        out = []
        for c in cols:
            x = np.log1p(dd[c]) - grp[c].transform(lambda s: np.log1p(s).mean())
            y = dd.y - grp.y.transform("mean")
            out.append(f"{c} {np.corrcoef(x, y)[0, 1]:+.3f}")
            yn, xn = y[~dd.is_covid], x[~dd.is_covid]
            out[-1] += f" (non-COVID rows {np.corrcoef(xn, yn)[0, 1]:+.3f})"
        rep.append(f"  - {t} ({season}): " + "; ".join(out))
    rep.append("")
    rep.append("Rain anomalies explain little of the tourism anomalies; expect rain to add a small gain over a seasonal baseline.")

    # ---- 6. baselines ---------------------------------------------------------------------------
    rep += ["", f"## 6. Baselines (train ≤ {TEST_YEAR - 1}, test = {TEST_YEAR}, all rows incl. COVID, no COVID feature)", ""]
    for d, t, season, lag, cols in [(q2, "visitors_total", "quarter", 4, ["rain_sum_mm", "rain_lag1"]),
                                    (m2, "guest_arrivals_total", "month", 12, ["rain_avg_mm", "rain_lag1"])]:
        res = evaluate(d, t, season, lag, cols)
        rep += [f"### {t} ({season}ly)", "", "| model | WAPE % | MAPE % | RMSE(log) |", "|---|---:|---:|---:|"]
        rep += [f"| {r['model']} | {r['WAPE']:.1f} | {r['MAPE']:.1f} | {r['RMSE_log']:.3f} |" for r in res]
        for r in res:
            if "coef" in r:
                rep.append(f"- coefficients, {r['model']}: " + ", ".join(f"{k} {v:+.4f}" for k, v in r["coef"].items()))
        rep.append("")

    text = "\n".join(rep) + "\n"
    (OUT / "model_readiness_report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
