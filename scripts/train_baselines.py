"""One-month-ahead provincial forecasts. Run from any directory with Python."""
from pathlib import Path
import argparse
import json

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, confusion_matrix,
                             f1_score, mean_absolute_error, precision_score, recall_score,
                             roc_auc_score, r2_score)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "baseline_2019"
TARGET = "guest_arrivals"
CATEGORIES = ["province_id", "month"]
LAGS = [f"{col}_lag{k}" for col in (TARGET, "avg_rain_mm") for k in (1, 3, 12)]
FEATURES = CATEGORIES + LAGS


def load_panel(years=(2019, 2020, 2021), end=None):
    panel = pd.read_parquet(ROOT / "data/core/province_month.parquet",
                            columns=["province_id", "year_month", TARGET, "avg_rain_mm"])
    if end is None:
        end = f"{max(years)}-12"
    panel = panel[panel.year_month.between("2018-01", end)].copy()
    panel = panel.sort_values(["province_id", "year_month"]).reset_index(drop=True)
    months = pd.period_range("2018-01", end, freq="M").astype(str)
    ids = panel.province_id.unique()
    expected = pd.MultiIndex.from_product([ids, months], names=["province_id", "year_month"])
    if len(ids) != 77 or not panel.set_index(expected.names).index.equals(expected):
        raise ValueError("Expected complete, unique monthly histories for all 77 provinces")
    values = panel[[TARGET, "avg_rain_mm"]].to_numpy()
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Invalid arrival count or rainfall")
    for col in (TARGET, "avg_rain_mm"):
        series = panel.groupby("province_id")[col]
        for k in (1, 3, 12):
            panel[f"{col}_lag{k}"] = series.shift(k)
    panel["month"] = panel.year_month.str[-2:]
    panel["iscovid"] = panel.year_month.between("2020-03", "2021-12").astype(int)
    panel["province_id"] = panel.province_id.astype(str)
    return [panel[panel.year_month.str.startswith(str(year))] for year in years]


def candidates(covid=False):
    def pipeline(estimator, scale):
        transform = ColumnTransformer([
            ("category", OneHotEncoder(drop="first", sparse_output=False), CATEGORIES),
            ("lags", StandardScaler() if scale else "passthrough", LAGS),
            *([("covid", "passthrough", ["iscovid"])] if covid else []),
        ])
        return make_pipeline(transform, estimator)

    yield "Seasonal naive", {}, None
    yield "Linear Regression", {}, pipeline(LinearRegression(), True)
    for alpha in (0.1, 1, 10, 100):
        yield "Ridge Regression", {"alpha": alpha}, pipeline(Ridge(alpha=alpha), True)
    for depth in (5, 10):
        forest = RandomForestRegressor(n_estimators=300, min_samples_leaf=3,
                                       max_depth=depth, random_state=42, n_jobs=1)
        yield "Random Forest", {"max_depth": depth}, pipeline(forest, False)
    for depth in (4, 6):
        model = CatBoostRegressor(iterations=300, learning_rate=0.05, depth=depth,
                                  cat_features=CATEGORIES, random_seed=42, thread_count=1,
                                  verbose=False, allow_writing_files=False)
        yield "CatBoost", {"depth": depth}, model


def evaluate(model, data, features=FEATURES):
    predicted = (data[f"{TARGET}_lag12"].to_numpy() if model is None else
                 np.maximum(0, np.expm1(model.predict(data[features]))))
    actual = data[TARGET].to_numpy()
    previous = data[f"{TARGET}_lag1"].to_numpy()
    truth = actual > previous
    calls = predicted > previous
    score = np.log1p(predicted) - np.log1p(previous)
    predictions = data[["province_id", "year_month"]].assign(
        actual=actual, previous_actual=previous, predicted=predicted,
        actual_increase=truth, predicted_increase=calls, increase_score=score)
    return summarize(predictions), predictions


def summarize(predictions):
    truth = predictions.actual_increase
    calls = predictions.predicted_increase
    tn, fp, fn, tp = confusion_matrix(truth, calls, labels=[False, True]).ravel()
    metrics = dict(f1=f1_score(truth, calls, zero_division=0), accuracy=accuracy_score(truth, calls),
                   balanced_accuracy=balanced_accuracy_score(truth, calls),
                   precision=precision_score(truth, calls, zero_division=0),
                   recall=recall_score(truth, calls, zero_division=0),
                   roc_auc=roc_auc_score(truth, predictions.increase_score),
                   mae=mean_absolute_error(predictions.actual, predictions.predicted),
                   positives=int(truth.sum()), tn=tn, fp=fp, fn=fn, tp=tp)
    return metrics


def rank(metrics):
    return metrics["f1"], metrics["balanced_accuracy"]


def main():
    train, validation, test = load_panel()
    best = {}
    for name, params, model in candidates():
        if model is not None:
            model.fit(train[FEATURES], np.log1p(train[TARGET]))
        metrics, predictions = evaluate(model, validation)
        if name not in best or rank(metrics) > rank(best[name][2]):
            best[name] = params, model, metrics, predictions
    # Candidate order breaks exact ties in favor of the simpler model.
    winner = max(best, key=lambda name: rank(best[name][2]))
    rows, frames = [], []
    for name, (params, model, metrics, predictions) in best.items():
        for split, (scores, forecasts) in [
            ("validation", (metrics, predictions)), ("test", evaluate(model, test))
        ]:
            rows.append(dict(split=split, model=name, selected=name == winner,
                             parameters=json.dumps(params), **scores))
            frames.append(forecasts.assign(split=split, model=name))
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT / "metrics.csv", index=False)
    pd.concat(frames).to_csv(OUT / "predictions.csv", index=False)
    print(pd.DataFrame(rows).to_string(index=False))


def select_quarterly(train, validation, covid=False):
    features = FEATURES + ["iscovid"] if covid else FEATURES
    quarters = validation.groupby((validation.month.astype(int) - 1) // 3 + 1)
    best = {}
    year = validation.year_month.iloc[0][:4]
    for name, params, model in candidates(covid):
        history = train
        forecasts = []
        for quarter, window in quarters:
            train_end = history.year_month.max()
            if len(window) != 231 or train_end >= window.year_month.min():
                raise ValueError("Invalid validation window")
            if model is not None:
                model.fit(history[features], np.log1p(history[TARGET]))
            _, predictions = evaluate(model, window, features)
            forecasts.append(predictions.assign(window=f"{year}-Q{quarter}", train_end=train_end))
            history = pd.concat([history, window], ignore_index=True)
        pooled = pd.concat(forecasts)
        scores = summarize(pooled)
        if name not in best or rank(scores) > rank(best[name][0]):
            best[name] = scores, pooled, params, model

    return best


def quarterly_retrain():
    train, validation, test = load_panel()
    best = select_quarterly(train, validation)
    winner = max(best, key=lambda name: rank(best[name][0]))
    history = pd.concat([train, validation], ignore_index=True)
    baseline = pd.read_csv(OUT / "metrics.csv").query("split == 'test'").set_index("model")
    rows, frames = [], []
    for name, (scores, pooled, params, model) in best.items():
        meta = dict(model=name, selected=name == winner, parameters=json.dumps(params))
        for window, predictions in pooled.groupby("window"):
            rows.append(dict(**meta, window=window, **summarize(predictions)))
        rows.append(dict(**meta, window="validation_2020", **scores))
        if model is not None:
            model.fit(history[FEATURES], np.log1p(history[TARGET]))
        test_scores, predictions = evaluate(model, test)
        rows.append(dict(**meta, window="test_2021", **test_scores,
                         baseline_f1=baseline.loc[name, "f1"],
                         delta_f1=test_scores["f1"] - baseline.loc[name, "f1"]))
        frames.extend([pooled.assign(model=name), predictions.assign(
            model=name, window="test_2021", train_end=history.year_month.max())])
    output = OUT.parent / "quarterly_retrain_2020"
    output.mkdir(exist_ok=True)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(output / "metrics.csv", index=False)
    pd.concat(frames).to_csv(output / "predictions.csv", index=False)
    print(metrics[metrics.window.isin(["validation_2020", "test_2021"])].to_string(index=False))


def covid_coefficient(name, model, history, features):
    from scipy.stats import t

    row = dict(model=name, feature="iscovid", coefficient=np.nan, std_error=np.nan,
               df=np.nan, p_value=np.nan, method="No linear coefficient")
    if name not in ("Linear Regression", "Ridge Regression"):
        return row
    transform, estimator = model.steps[0][1], model.steps[1][1]
    idx = list(transform.get_feature_names_out()).index("covid__iscovid")
    beta = estimator.coef_[idx]
    row.update(coefficient=beta, method="Ridge penalized coefficient")
    if name == "Ridge Regression":
        return row

    # Partial out the intercept and all other regressors for the scalar sandwich.
    design = transform.transform(history[features])
    controls = np.column_stack((np.ones(len(design)), np.delete(design, idx, axis=1)))
    flag = design[:, idx]
    residual_flag = flag - controls @ np.linalg.lstsq(controls, flag, rcond=None)[0]
    errors = np.log1p(history[TARGET]).to_numpy() - estimator.predict(design)
    influence = pd.Series(residual_flag * errors / (residual_flag @ residual_flag))
    n = len(design)
    k = estimator.rank_ + 1

    def cluster_variance(groups):
        totals = influence.groupby(groups.to_numpy()).sum()
        g = len(totals)
        return (totals @ totals) * g / (g - 1) * (n - 1) / (n - k)

    # Each province-month intersection contains one observation.
    variance = (cluster_variance(history.province_id) + cluster_variance(history.year_month)
                - (influence @ influence) * n / (n - k))
    if variance <= 0:
        raise ValueError("Non-positive two-way clustered variance for iscovid")
    se = np.sqrt(variance)
    df = min(history.province_id.nunique(), history.year_month.nunique()) - 1
    return dict(row, std_error=se, df=df, p_value=2 * t.sf(abs(beta / se), df),
                method="OLS two-way cluster robust t (province and year-month)")


def covid_baseline():
    first, second, validation, test = load_panel((2019, 2020, 2021, 2022))
    train = pd.concat([first, second], ignore_index=True)
    best = select_quarterly(train, validation, covid=True)
    winner = max(best, key=lambda name: rank(best[name][0]))
    history = pd.concat([train, validation], ignore_index=True)
    features = FEATURES + ["iscovid"]
    rows, frames, coefficients = [], [], []
    for name, (_, pooled, params, model) in best.items():
        if model is not None:
            model.fit(history[features], np.log1p(history[TARGET]))
        _, forecasts = evaluate(model, test, features)
        forecasts = forecasts.assign(window="test_2022", train_end=history.year_month.max())
        frames.extend([pooled.assign(model=name), forecasts.assign(model=name)])
        coefficients.append(covid_coefficient(name, model, history, features))
        for label, frame in [*pooled.groupby("window"), ("validation_2021", pooled),
                             ("test_2022", forecasts)]:
            rows.append(dict(model=name, window=label, selected=name == winner,
                             parameters=json.dumps(params), n=len(frame),
                             r_squared=r2_score(frame.actual, frame.predicted), **summarize(frame)))
    output = OUT.parent / "covid_baseline_2022"
    output.mkdir(parents=True, exist_ok=True)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(output / "metrics.csv", index=False)
    pd.concat(frames).to_csv(output / "predictions.csv", index=False)
    pd.DataFrame(coefficients).to_csv(output / "coefficients.csv", index=False)
    print(metrics[metrics.window.isin(["validation_2021", "test_2022"])].to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--quarterly-retrain", action="store_true")
    modes.add_argument("--covid-baseline", action="store_true")
    args = parser.parse_args()
    if args.covid_baseline:
        covid_baseline()
    elif args.quarterly_retrain:
        quarterly_retrain()
    else:
        main()
