"""WAPE, MAE, RMSE, and MASE for one replay log."""

from __future__ import annotations

import numpy as np
import pandas as pd

def wape(y: np.ndarray, pred: np.ndarray) -> float:
    denom = float(np.abs(y).sum())
    if denom == 0:
        return float("nan")
    return float(np.abs(y - pred).sum() / denom)


def mae(y: np.ndarray, pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y - pred)))


def rmse(y: np.ndarray, pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y - pred) ** 2)))


def mase(y: np.ndarray, pred: np.ndarray, naive: np.ndarray) -> float:
    scale = float(np.mean(np.abs(y - naive)))
    if scale == 0:
        return float("nan")
    return float(np.mean(np.abs(y - pred)) / scale)


def _block(y: np.ndarray, pred: np.ndarray, naive: np.ndarray) -> dict:
    return {
        "wape": wape(y, pred),
        "mae": mae(y, pred),
        "rmse": rmse(y, pred),
        "mase": mase(y, pred, naive),
        "n": int(len(y)),
    }


def summarize(log: pd.DataFrame, naive: pd.Series) -> dict:
    """Overall, per-province, and rolling 3-month WAPE.

    `naive` is aligned to `log` and is the seasonal-naive prediction
    (`guest_lag_12`), even when the model under test is not that baseline.
    """
    y = log["y_true"].to_numpy(dtype=float)
    pred = log["y_pred"].to_numpy(dtype=float)
    naive_values = naive.to_numpy(dtype=float)
    per_province = []
    for province_id, part in log.groupby("province_id", sort=True):
        index = part.index
        per_province.append(
            {
                "province_id": str(province_id),
                **_block(part["y_true"].to_numpy(dtype=float), part["y_pred"].to_numpy(dtype=float), naive.loc[index].to_numpy(dtype=float)),
            }
        )
    rolling = []
    months = list(dict.fromkeys(log["year_month"].tolist()))
    for end in range(2, len(months)):
        window = set(months[end - 2 : end + 1])
        mask = log["year_month"].isin(window)
        rolling.append(
            {
                "year_month": months[end],
                "wape": wape(y[mask.to_numpy()], pred[mask.to_numpy()]),
                "n_months": 3,
            }
        )
    return {"overall": _block(y, pred, naive_values), "per_province": per_province, "rolling_3m_wape": rolling}


def seasonal_naive_from_log(log: pd.DataFrame, features_by_month: dict[str, pd.DataFrame]) -> pd.Series:
    """Look up guest_lag_12 for each logged row. That is the seasonal naive."""
    values = []
    for row in log.itertuples(index=False):
        frame = features_by_month[row.year_month]
        match = frame.loc[frame["province_id"].astype(str) == str(row.province_id), "guest_lag_12"]
        values.append(float(match.iloc[0]))
    return pd.Series(values, index=log.index)
