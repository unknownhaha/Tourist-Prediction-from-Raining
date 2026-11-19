"""Replay three archived versions, select on 2023, then evaluate later months."""
import json

import numpy as np
import pandas as pd

from train_baselines import FEATURES, OUT, TARGET, candidates, evaluate, load_panel


def main():
    train, update, replay, covid_test, validation, test = load_panel((2019, 2020, 2021, 2022, 2023, 2024))
    # 2025 is a partial year. Derive the endpoint from the actual source.
    source = pd.read_parquet(OUT.parents[1] / "data/core/province_month.parquet", columns=["year_month"])
    end = source.loc[source.year_month.str.startswith("2025"), "year_month"].max()
    future, = load_panel((2025,), end=end)
    versions = [
        ("first", train, OUT, "split", "test", replay, False),
        ("latest", pd.concat([train, update]), OUT.parent / "quarterly_retrain_2020",
         "window", "test_2021", replay, False),
        ("covid", pd.concat([train, update, replay]), OUT.parent / "covid_baseline_2022",
         "window", "test_2022", covid_test, True),
    ]
    rows, frames = [], []
    for version, history, folder, split_column, test_label, archived_test, covid in versions:
        features = FEATURES + ["iscovid"] if covid else FEATURES
        saved = pd.read_csv(folder / "metrics.csv")
        params_by_name = saved[saved[split_column].eq(test_label)].set_index("model").parameters
        original = pd.read_csv(folder / "predictions.csv", dtype={"province_id": str})
        original = original[original[split_column].eq(test_label)]
        for name, params, model in candidates(covid):
            if params != json.loads(params_by_name[name]):
                continue
            if model is not None:
                model.fit(history[features], np.log1p(history[TARGET]))
            _, check = evaluate(model, archived_test, features)
            keys = ["province_id", "year_month"]
            expected = original[original.model.eq(name)].set_index(keys).predicted.sort_index()
            pd.testing.assert_series_equal(check.set_index(keys).predicted.sort_index(), expected,
                                           rtol=1e-9, atol=1e-7)
            for split, data in [("validation_2023", validation), ("test_2024", test),
                                ("test_2025_partial", future)]:
                metrics, predictions = evaluate(model, data, features)
                rows.append(dict(version=version, model=name, split=split,
                                 parameters=json.dumps(params), **metrics))
                frames.append(predictions.assign(version=version, model=name, split=split))
    if len(rows) != 45:
        raise ValueError("Expected five models per version and split")
    output = OUT.parent / "version_comparison_2023_2025"
    output.mkdir(exist_ok=True)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(output / "metrics.csv", index=False)
    pd.concat(frames).to_csv(output / "predictions.csv", index=False)
    print(metrics.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
