"""Compare eight frozen 2023-12 recipes on 2024, then replay the selected model."""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from experiments.backtest.run import training_window
from experiments.beat_seasonal_naive.run import matrix_for
from experiments.online_replay import experts as X
from experiments.online_replay import replay as R
from experiments.online_replay.selection import load_config
from src.model.leakage import shift_year_month
from src.stream.metrics import _block


OUT = Path(__file__).resolve().parent / "results" / "comparison_2024"
MODELS = ("Seasonal naive", "Linear Regression", "Ridge Regression", "Random Forest",
          "CatBoost", "RF logdiff", "YoY3", "Champion")


def forecast(ctx, name, end, months, checkpoint=None):
    """All fitted parameters (including preprocessing) use only targets through end."""
    part = X.part_of(ctx, months)
    if any(m <= end for m in months):
        raise ValueError("Forecast month must follow the training cutoff")
    if name == "Seasonal naive":
        return part.guest_lag_12.to_numpy(float)
    if name == "Champion":
        # Freeze both forests AND their equal starting weights for the selection test.
        return (X.fit_predict(ctx, "logdiff", end, months)[0] +
                X.fit_predict(ctx, "level", end, months)[0]) / 2
    if name in ("Random Forest", "RF logdiff", "YoY3"):
        target = "level" if name == "Random Forest" else "logdiff"
        extra = ("yoy_3m_log",) if name == "YoY3" else ()
        return X.fit_predict(ctx, target, end, months, add=extra, exp=(), checkpoint=checkpoint)[0]
    estimators = {
        "Linear Regression": LinearRegression(),
        "Ridge Regression": Ridge(alpha=1),
        "CatBoost": CatBoostRegressor(iterations=300, depth=6, learning_rate=.05,
                                      random_seed=42, thread_count=4, verbose=False,
                                      allow_writing_files=False),
    }
    train = training_window(ctx.table, end)
    features = matrix_for(pd.concat([train, part]), ctx.history, "ext", end)
    model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), estimators[name])
    model.fit(features.loc[train.index], train.guest_arrivals_total)
    if checkpoint:
        joblib.dump(dict(model=model, columns=features.columns.tolist(), train_end=end), checkpoint, compress=3)
    return np.maximum(0, model.predict(features.loc[part.index])) if months else np.empty(0)


def score(frame, column):
    return _block(frame.actual.to_numpy(), frame[column].to_numpy(), frame["Seasonal naive"].to_numpy())


def select(ctx):
    months = X.months_of(2024)
    frame = X.part_of(ctx, months)[["year_month", "province_id", "guest_arrivals_total"]].rename(
        columns={"guest_arrivals_total": "actual"}).copy()
    if len(frame) != 924 or frame.duplicated(["year_month", "province_id"]).any():
        raise ValueError("Incomplete 2024 comparison panel")
    for name in MODELS:
        frame[name] = forecast(ctx, name, "2023-12", months)
        if not np.isfinite(frame[name]).all():
            raise ValueError(f"Invalid predictions for {name}")
    scores = pd.DataFrame([{"model": name, **score(frame, name)} for name in MODELS])
    scores = scores.sort_values(["wape", "mae", "model"])
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT / "frozen_predictions.csv", index=False)
    scores.to_csv(OUT / "ranking.csv", index=False)
    print(scores.to_string(index=False), flush=True)
    return scores.iloc[0].model


def online(ctx, winner):
    if winner == "Champion":
        from experiments.online_replay.train_stream import main as train_champion
        train_champion()
        return
    cfg = load_config()
    # Remove an identical challenger if its recipe already won the frozen test.
    aliases = {"YoY3": "yoy3_expanding", "RF logdiff": "logdiff_expanding", "Random Forest": "expert_level"}
    candidates = cfg["drift"]["challengers"]
    cfg["drift"]["challengers"] = [name for name in candidates if name != aliases.get(winner)]
    seed = []
    predictions = {}
    months = X.months_of(2024) + X.months_of(2025)
    for month in X.months_of(2022) + X.months_of(2023) + months:
        end = shift_year_month(month, 1)
        if month in months:
            R._gate(ctx, month)
        checkpoint = OUT / "winner_2023-12.joblib" if month == months[0] else None
        pred = forecast(ctx, winner, end, [month], checkpoint)
        if month in months:
            predictions[month] = pred
        else:
            seed.extend(R.monthly_wape_series(X.part_of(ctx, [month]), pred))
        if month.endswith("-12"):
            print(f"{winner} predicted through {month}", flush=True)
    forecast(ctx, winner, months[-1], [], OUT / "winner_2025-12.joblib")
    track = R.run_track(ctx, cfg, months, {"winner": predictions}, "winner@2023-12",
                        "2023-12", "2023-12", seed, "C0")
    logs = track["log"]
    frames = []
    for row in logs.itertuples():
        m = row.month
        frames.append(pd.DataFrame({
            "year_month": m, "province_id": X.part_of(ctx, [m]).province_id.to_numpy(),
            "actual": track["y"][m], "Seasonal naive": track["naive"][m], "winner_prediction": predictions[m],
            "served": track["server"](row.served_model, m), "model": row.served_model,
        }))
    frame = pd.concat(frames, ignore_index=True)
    if (len(frame) != 1848 or frame.duplicated(["year_month", "province_id"]).any()
            or not np.isfinite(frame[["winner_prediction", "served", "actual"]]).all().all()):
        raise ValueError("Incomplete online replay")
    frame.to_csv(OUT / "online_predictions.csv", index=False)
    logs.assign(fit_through=[shift_year_month(m, 1) for m in months]).to_csv(OUT / "online_monthly.csv", index=False)
    summary = R._summary(track, winner)
    summary.update(selected_on=2024, fit_start="2019-01", initial_fit_end="2023-12", lag_history=2018,
                   challengers=cfg["drift"]["challengers"], yearly={})
    for year, group in frame.groupby(frame.year_month.str[:4]):
        summary["yearly"][year] = {key: score(group, key) for key in ("winner_prediction", "served", "Seasonal naive")}
    (OUT / "online_summary.json").write_text(json.dumps(summary, indent=2, default=float), encoding="utf-8")
    print(summary["yearly"], flush=True)


if __name__ == "__main__":
    context = X.Context.build()
    online(context, select(context))
