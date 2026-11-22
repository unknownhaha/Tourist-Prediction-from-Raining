"""Model trials for the online/alternative-model study (orders 1-5).

Contract shared by every model here, the same one experiments/backtest uses:
- a frozen forecast for target months only learns from rows <= train_end and
  never reads a label (guest_arrivals_total) of a target month;
- an online/monthly forecast for month t only learns from rows <= t-1.
Features for month t are the backtest table's features, which read months
before t only.
"""

from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.backtest.run import build_table, training_window  # noqa: E402
from experiments.beat_seasonal_naive.run import BASE_FEATURES, Spec, fit_spec, reconstruct  # noqa: E402
from experiments.new_features.run import LOGDIFF, _matrix  # noqa: E402
from src.common.calendar import is_covid  # noqa: E402
from src.model.leakage import shift_year_month  # noqa: E402

TRIAL_COLUMNS = ("base_clean", "base_gap", "base_switched", "base_log_gap", "recovery_log", "yoy_log")
MAX_BASE_LAG = 96
# Linear models extrapolate wildly on the COVID-era log ratios, so their raw log-diff
# prediction is clipped to the 1st..99th percentile of the training labels.
CLIP_Q = (0.01, 0.99)


# --------------------------------------------------------------------------- table


def _clean_base(guest: pd.DataFrame, ids: pd.Series, month: str) -> tuple[np.ndarray, np.ndarray]:
    """Same-month value from the most recent non-COVID year at least 12 months back."""
    value = np.full(len(ids), np.nan)
    gap = np.full(len(ids), np.nan)
    for lag in range(12, MAX_BASE_LAG + 1, 12):
        source = shift_year_month(month, lag)
        if is_covid(source):
            continue
        if source not in guest.columns:
            break
        candidate = guest.reindex(ids)[source].to_numpy(dtype=float)
        fill = np.isnan(value) & np.isfinite(candidate) & (candidate > 0)
        value[fill] = candidate[fill]
        gap[fill] = lag
        if not np.isnan(value).any():
            break
    return value, gap


def add_trial_features(table: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """Add the base-year-fix columns. Every source month is t-12 or earlier (base)
    or t-1 / t-13 and earlier (recovery), so nothing reads month t."""
    panel = panel.copy()
    panel["province_id"] = panel["province_id"].astype(str).str.zfill(2)
    guest = panel.pivot(index="province_id", columns="year_month", values="guest_arrivals_total")
    out = table.copy()
    base = np.full(len(out), np.nan)
    gap = np.full(len(out), np.nan)
    prev_base = np.full(len(out), np.nan)
    for month, index in out.groupby("year_month").groups.items():
        ids = out.loc[index, "province_id"]
        b, g = _clean_base(guest, ids, month)
        pb, _ = _clean_base(guest, ids, shift_year_month(month, 1))
        positions = out.index.get_indexer(index)
        base[positions], gap[positions], prev_base[positions] = b, g, pb
    lag12 = out["guest_lag_12"].to_numpy(dtype=float)
    lag1 = out["guest_lag_1"].to_numpy(dtype=float)
    lag13 = out["guest_lag_13"].to_numpy(dtype=float)
    # Where no clean base exists, fall back to the plain lag-12 base.
    base = np.where(np.isfinite(base), base, lag12)
    out["base_clean"] = base
    out["base_gap"] = np.where(np.isfinite(gap), gap, 12.0)
    out["base_switched"] = (out["base_gap"] > 12).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ok = np.isfinite(lag1) & np.isfinite(prev_base) & (lag1 > 0) & (prev_base > 0)
        out["recovery_log"] = np.where(ok, np.log(np.where(ok, lag1, 1.0)) - np.log(np.where(ok, prev_base, 1.0)), np.nan)
        ok2 = np.isfinite(lag1) & np.isfinite(lag13) & (lag1 > 0) & (lag13 > 0)
        ok3 = np.isfinite(lag12) & (lag12 > 0) & (base > 0)
        out["base_log_gap"] = np.where(ok3, np.log(np.where(ok3, lag12, 1.0)) - np.log(np.where(ok3, base, 1.0)), 0.0)
        out["yoy_log"] = np.where(ok2, np.log(np.where(ok2, lag1, 1.0)) - np.log(np.where(ok2, lag13, 1.0)), np.nan)
    return out


def build_trial_table(panel: pd.DataFrame) -> pd.DataFrame:
    return add_trial_features(build_table(panel), panel)


def _visible(table: pd.DataFrame, train_end: str, months: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if any(month <= train_end for month in months):
        raise AssertionError(f"target month not after train_end {train_end}")
    part = table.loc[table["year_month"].isin(months)]
    visible = pd.concat([training_window(table, train_end), part]).sort_values(["year_month", "province_id"])
    return part, visible


# --------------------------------------------------------------------------- order 1


def predict_basefix(table: pd.DataFrame, history: dict, train_end: str, months: list[str], *,
                    switch_base: bool = True, momentum: bool = False, gap_feature: bool = False,
                    spec: Spec = LOGDIFF) -> np.ndarray:
    """RF logdiff whose base is the last non-COVID same-month value instead of lag 12.

    momentum adds recovery_log = log(lag1 / clean base of t-1); gap_feature adds
    base_log_gap = log(lag12 / clean base), which is 0 when the base was not switched."""
    part, visible = _visible(table, train_end, months)
    extra = (("recovery_log",) if momentum else ()) + (("base_log_gap",) if gap_feature else ())
    matrix = _matrix(visible, history, train_end, extra)
    base_col = "base_clean" if switch_base else "guest_lag_12"
    fit_table = visible.copy()
    fit_table["guest_lag_12"] = visible[base_col]
    fitted = fit_spec(spec, fit_table, matrix, train_end)
    target_index = visible.index[visible["year_month"].isin(months)]
    pred = fitted.predict(matrix.loc[target_index], visible.loc[target_index, base_col].to_numpy(dtype=float))
    return pd.Series(pred, index=target_index).reindex(part.index).to_numpy(dtype=float)


# --------------------------------------------------------------------------- order 2


def blend(logdiff: np.ndarray, level: np.ndarray, alpha: float) -> np.ndarray:
    """alpha * logdiff + (1 - alpha) * level."""
    return alpha * logdiff + (1.0 - alpha) * level


def switch(part: pd.DataFrame, logdiff: np.ndarray, level: np.ndarray, threshold: float) -> np.ndarray:
    """Use the level forest when the lag-12 base month is COVID or |log(lag1/lag13)| > threshold."""
    covid_base = part["is_covid_lag12"].to_numpy(dtype=float) > 0
    yoy = np.abs(part["yoy_log"].to_numpy(dtype=float))
    extreme = np.isfinite(yoy) & (yoy > threshold)
    use_level = covid_base | extreme
    return np.where(use_level, level, logdiff)


def ewa(part: pd.DataFrame, experts: dict[str, np.ndarray], eta: float) -> tuple[np.ndarray, list[dict]]:
    """Exponentially weighted average of experts (the update river's EWARegressor uses:
    w_i <- w_i * exp(-eta * loss_i)), with loss = the expert's aggregate WAPE for the month.

    Weights start equal on the first target month; the weight for month t only uses the
    losses of months before t, so month t's label never reaches month t's forecast.
    """
    names = list(experts)
    months = part["year_month"].to_numpy()
    y = part["guest_arrivals_total"].to_numpy(dtype=float)
    log_w = np.zeros(len(names))
    out = np.zeros(len(part))
    trace = []
    for month in sorted(set(months)):
        keep = months == month
        w = np.exp(log_w - log_w.max())
        w = w / w.sum()
        out[keep] = sum(w[i] * experts[name][keep] for i, name in enumerate(names))
        trace.append({"year_month": month, **{f"w_{name}": float(w[i]) for i, name in enumerate(names)}})
        denom = np.abs(y[keep]).sum()
        for i, name in enumerate(names):
            loss = np.abs(y[keep] - experts[name][keep]).sum() / denom
            log_w[i] -= eta * loss
    return out, trace


# --------------------------------------------------------------------------- order 3 / 5 batch


def lgbm_spec(tag: str, **params) -> Spec:
    base = dict(random_state=42, n_jobs=1, verbosity=-1, subsample=1.0, colsample_bytree=1.0, deterministic=True)
    base.update(params)
    return Spec(f"lgbm|logdiff|ext|{tag}", "lgbm", "logdiff", "ext", tuple(sorted(base.items())))


def ridge_spec(alpha: float) -> Spec:
    return Spec(f"ridge|logdiff|ext|a{alpha:g}", "ridge", "logdiff", "ext", (("alpha", alpha), ("random_state", 42)))


def predict_spec(table: pd.DataFrame, history: dict, train_end: str, months: list[str], spec: Spec,
                 clip: bool = False, log_features: bool = False) -> np.ndarray:
    """Any fit_spec model on the extended log-diff features (LightGBM, Ridge)."""
    part, visible = _visible(table, train_end, months)
    matrix = linear_matrix(visible, history, train_end) if log_features else _matrix(visible, history, train_end, ())
    fitted = fit_spec(spec, visible, matrix, train_end)
    target_index = visible.index[visible["year_month"].isin(months)]
    lag12 = visible.loc[target_index, "guest_lag_12"].to_numpy(dtype=float)
    if clip:
        lo, hi = _label_range(visible, train_end)
        filled = matrix.loc[target_index].copy()
        for column in fitted.columns:
            filled[column] = filled[column].fillna(fitted.medians[column])
        values = filled[fitted.columns].to_numpy(dtype=float)
        if fitted.scaler is not None:
            values = fitted.scaler.transform(values)
        raw = np.clip(np.asarray(fitted.estimator.predict(values), dtype=float), lo, hi)
        pred = reconstruct("logdiff", raw, lag12)
    else:
        pred = fitted.predict(matrix.loc[target_index], lag12)
    return pd.Series(pred, index=target_index).reindex(part.index).to_numpy(dtype=float)


LOG_LAGS = ("guest_lag_1", "guest_lag_2", "guest_lag_3", "guest_lag_12")


def linear_matrix(table: pd.DataFrame, history: dict, train_end: str) -> pd.DataFrame:
    """Extended features re-expressed on a log scale for the linear models: log1p of the
    guest lags and log(lag1/lag13) instead of the raw ratio. Same source months as the
    extended matrix, so nothing reads month t."""
    base = _matrix(table, history, train_end, ())
    out = base.drop(columns=["yoy_growth"])
    for column in LOG_LAGS:
        out[column] = np.log1p(out[column].clip(lower=0))
    out["yoy_log"] = table.loc[base.index, "yoy_log"].to_numpy(dtype=float)
    out["recovery_log"] = table.loc[base.index, "recovery_log"].to_numpy(dtype=float)
    return out


def _label_range(visible: pd.DataFrame, train_end: str) -> tuple[float, float]:
    train = visible.loc[visible["year_month"].between("2019-01", train_end)]
    y = train["guest_arrivals_total"].to_numpy(dtype=float)
    lag = train["guest_lag_12"].to_numpy(dtype=float)
    ok = np.isfinite(y) & np.isfinite(lag) & (y > 0) & (lag > 0)
    label = np.log(y[ok]) - np.log(lag[ok])
    return float(np.quantile(label, CLIP_Q[0])), float(np.quantile(label, CLIP_Q[1]))


# --------------------------------------------------------------------------- order 5 river linear


def river_rows(table: pd.DataFrame, history: dict, last_month: str) -> tuple[pd.DataFrame, list[dict]]:
    """Rows 2019-01..last_month with their extended feature dicts (NaN keys dropped).

    The rain anomaly baseline for row t is capped at min(t-1, last_month), so it only
    reads rain before t; no label enters a feature."""
    rows = table.loc[table["year_month"].between("2019-01", last_month)].sort_values(["year_month", "province_id"])
    matrix = linear_matrix(rows, history, last_month)
    values = matrix.to_numpy(dtype=float)
    cols = list(matrix.columns)
    dicts = [{c: float(v) for c, v in zip(cols, row) if math.isfinite(v)} for row in values]
    return rows, dicts


def _river_label(rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    y = rows["guest_arrivals_total"].to_numpy(dtype=float)
    lag = rows["guest_lag_12"].to_numpy(dtype=float)
    base = np.isfinite(rows[BASE_FEATURES].to_numpy(dtype=float)).all(axis=1)
    ok = np.isfinite(y) & np.isfinite(lag) & (y > 0) & (lag > 0) & base
    label = np.zeros(len(rows))
    label[ok] = np.log(y[ok]) - np.log(lag[ok])
    return ok, label


def river_linear_factory(kind: str, params: dict):
    from river import compose, linear_model, optim, preprocessing

    if kind == "river_linreg":
        model = linear_model.LinearRegression(optimizer=optim.SGD(params.get("lr", 0.01)), l2=params.get("l2", 0.0))
    elif kind == "river_pa":
        model = linear_model.PARegressor(C=params.get("C", 1.0), mode=params.get("mode", 1), eps=params.get("eps", 0.1))
    elif kind == "river_blr":
        model = linear_model.BayesianLinearRegression(alpha=params.get("alpha", 1.0), beta=params.get("beta", 1.0),
                                                      smoothing=params.get("smoothing"))
    else:
        raise KeyError(kind)
    return compose.Pipeline(preprocessing.StandardScaler(), model)


def river_linear_year(table: pd.DataFrame, history: dict, year: int, kind: str, params: dict) -> tuple[np.ndarray, np.ndarray]:
    """Learn rows 2019-01..(year-1)-12 in time order, then:
    frozen  -> predict all 12 target months with no further learning;
    online  -> predict month t, then learn month t's rows (predict-then-learn).
    Returns (frozen, online) aligned to the table's rows of `year`. Rows whose label is
    missing are never learned."""
    train_end = f"{year - 1}-12"
    rows, dicts = river_rows(table, history, f"{year}-12")
    ok, label = _river_label(rows)
    months = rows["year_month"].to_numpy()
    model = river_linear_factory(kind, params)
    lo, hi = np.inf, -np.inf
    target = months >= f"{year}-01"
    frozen = np.zeros(int(target.sum()))
    online = np.zeros(int(target.sum()))
    target_pos = np.flatnonzero(target)
    lag12 = rows["guest_lag_12"].to_numpy(dtype=float)

    def learn(indices):
        for i in indices:
            if not ok[i]:
                continue
            model.learn_one(dicts[i], float(label[i]))

    def raw_pred(indices):
        return np.array([np.clip(model.predict_one(dicts[i]), lo, hi) for i in indices], dtype=float)

    pre = np.flatnonzero(months <= train_end)
    learn(pre)
    pre_labels = label[pre][ok[pre]]
    lo, hi = float(np.quantile(pre_labels, CLIP_Q[0])), float(np.quantile(pre_labels, CLIP_Q[1]))
    frozen_raw = raw_pred(target_pos)
    online_raw = np.zeros(len(target_pos))
    for m in range(1, 13):
        month = f"{year}-{m:02d}"
        idx = np.flatnonzero(months == month)
        local = np.searchsorted(target_pos, idx)
        online_raw[local] = raw_pred(idx)
        learn(idx)
    frozen = reconstruct("logdiff", frozen_raw, lag12[target_pos])
    online = reconstruct("logdiff", online_raw, lag12[target_pos])
    order = rows.index[target_pos]
    part_index = table.index[table["year_month"].between(f"{year}-01", f"{year}-12")]
    return (pd.Series(frozen, index=order).reindex(part_index).to_numpy(dtype=float),
            pd.Series(online, index=order).reindex(part_index).to_numpy(dtype=float))


# --------------------------------------------------------------------------- order 4 per-province TS


def _province_series(panel: pd.DataFrame, province_id: str) -> pd.DataFrame:
    part = panel.loc[panel["province_id"] == province_id].sort_values("year_month")
    out = part[["year_month", "guest_arrivals_total", "rain_avg_mm"]].copy().reset_index(drop=True)
    out["rain_lag_1"] = out["rain_avg_mm"].shift(1)
    return out


def ts_factory(kind: str, params: dict):
    from river import compose, linear_model, optim, preprocessing, time_series

    if kind == "snarimax":
        regressor = compose.Pipeline(preprocessing.StandardScaler(),
                                     linear_model.LinearRegression(optimizer=optim.SGD(params.get("lr", 0.01)),
                                                                   intercept_lr=params.get("lr", 0.01)))
        return time_series.SNARIMAX(p=params["p"], d=params["d"], q=params["q"], m=12, sp=params["sp"],
                                    sd=params["sd"], sq=params["sq"], regressor=regressor)
    if kind == "holtwinters":
        return time_series.HoltWinters(alpha=params["alpha"], beta=params.get("beta"), gamma=params["gamma"],
                                       seasonality=12, multiplicative=False)
    raise KeyError(kind)


def ts_year(panel: pd.DataFrame, table: pd.DataFrame, year: int, kind: str, params: dict) -> tuple[np.ndarray, np.ndarray]:
    """Per-province model on log1p(arrivals), learned 2018-01..(year-1)-12 in order.
    frozen -> 12-step forecast from December (no target-year data at all; SNARIMAX gets
              the rain_lag_1 climatology up to December as its future exogenous input);
    online -> one-step forecast for month t with rain_lag_1 (rain of t-1), then learn y_t."""
    panel = panel.copy()
    panel["province_id"] = panel["province_id"].astype(str).str.zfill(2)
    train_end = f"{year - 1}-12"
    part = table.loc[table["year_month"].between(f"{year}-01", f"{year}-12")]
    frozen = pd.Series(np.nan, index=part.index)
    online = pd.Series(np.nan, index=part.index)
    lookup = {(p, m): i for i, p, m in zip(part.index, part["province_id"], part["year_month"])}
    use_x = kind == "snarimax"
    for province_id in sorted(part["province_id"].unique()):
        series = _province_series(panel, province_id)
        model = ts_factory(kind, params)
        history = series.loc[series["year_month"] <= train_end]
        clim = {}
        for row in history.itertuples():
            y = row.guest_arrivals_total
            if not np.isfinite(y):
                continue
            x = {"rain_lag_1": float(row.rain_lag_1)} if use_x and np.isfinite(row.rain_lag_1) else ({"rain_lag_1": 0.0} if use_x else None)
            model.learn_one(float(np.log1p(max(y, 0.0))), x) if use_x else model.learn_one(float(np.log1p(max(y, 0.0))))
            if np.isfinite(row.rain_lag_1):
                clim.setdefault(int(row.year_month[5:7]), []).append(float(row.rain_lag_1))
        dec_rain = history["rain_avg_mm"].iloc[-1]
        months = [f"{year}-{m:02d}" for m in range(1, 13)]
        if use_x:
            xs = [{"rain_lag_1": float(dec_rain)}] + [{"rain_lag_1": float(np.mean(clim.get(m, [0.0])))} for m in range(2, 13)]
            fc = model.forecast(horizon=12, xs=xs)
        else:
            fc = model.forecast(horizon=12)
        for month, value in zip(months, fc):
            frozen[lookup[(province_id, month)]] = float(np.expm1(value))
        target = series.loc[series["year_month"].isin(months)].set_index("year_month")
        for month in months:
            rain_prev = target.loc[month, "rain_lag_1"]
            x = {"rain_lag_1": float(rain_prev) if np.isfinite(rain_prev) else 0.0}
            value = model.forecast(horizon=1, xs=[x])[0] if use_x else model.forecast(horizon=1)[0]
            online[lookup[(province_id, month)]] = float(np.expm1(value))
            y = target.loc[month, "guest_arrivals_total"]
            if np.isfinite(y):
                model.learn_one(float(np.log1p(max(y, 0.0))), x) if use_x else model.learn_one(float(np.log1p(max(y, 0.0))))
    clean = lambda s: np.maximum(np.nan_to_num(s.to_numpy(dtype=float), nan=0.0, posinf=0.0), 0.0)  # noqa: E731
    return clean(frozen), clean(online)


def small(spec: Spec, n: int = 20) -> Spec:
    params = dict(spec.params)
    if "n_estimators" in params:
        params["n_estimators"] = n
    return replace(spec, params=tuple(sorted(params.items())))
