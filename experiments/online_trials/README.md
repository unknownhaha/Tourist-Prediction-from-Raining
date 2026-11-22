# Online and alternative model trials (orders 0-6)

Run: `./.venv/bin/python experiments/online_trials/run.py` (about 13 minutes on the box; order 1's monthly refits take 10 of them). Needs the extras in `requirements-experiments.txt` (LightGBM 4.7.0, river 0.27.0).
Outputs: `results.csv`, `results.json` (selection grids, EWA weights, timings, notes), `monthly_wape.csv`, `validation_report.json`, `dq_report.json`, plus `predictions.parquet` (git-ignored, rebuilt by the run).
Leakage tests: `tests/test_online_trials_leakage.py`.

This is an offline replay. The results are recommendations; DS/ML make the final model choice.

## Protocol

- **Rolling origin.** Frozen models fit on 2019-01..(Y-1)-12 and forecast year Y. Monthly mode refits on an expanding window (batch models) or predicts and then learns (online models), using only rows before month t. 2018 is lag history only.
- **Selection on 2024 only.** Every variant, hyperparameter, alpha, threshold, and eta is chosen on 2024. 2023 and 2025 are held out. Batch models are selected on 2024 frozen WAPE. Online-native models (EWA, river time series, river linear) are selected on 2024 monthly WAPE. Only the selected configuration is scored on 2023 and 2025. The full 2024 grids are in `results.json → selection`.
- **2024 is optimistic for everything.** 2024 was also the year RF logdiff was selected on (Protocol B).
- **Reuse.** Feature table, `fit_spec`, `run_year`, and `score` come from `experiments/backtest/run.py` and `experiments/beat_seasonal_naive/run.py`. The validation and DQ gates from `experiments/new_features` run before any feature reaches a model.
- **Metrics.** WAPE, MAE, RMSE, MASE (relative to seasonal naive on the same rows), bias = `sum(pred)/sum(actual) - 1`, and `gap_vs_naive` = WAPE minus naive WAPE (negative means better than naive).

## Validation and DQ

Validation: 0 hard failures and 2 warnings, the same as the backtest (Thai/foreign YoY ratios are NaN where lag-13 is zero).
DQ: completeness 1.0, accuracy 0.539, consistency 1.0, uniqueness 1.0, timeliness 1.0, validity 1.0, overall 0.923. No hard stop was crossed.

## Order 0: reproduction

The reference backtest reproduces exactly before anything else runs (frozen WAPE, 2023 / 2024 / 2025):

| Model | 2023 | 2024 | 2025 |
|---|---|---|---|
| RF logdiff | 25.77 | 6.31 | 4.79 |
| RF level | 8.57 | 8.54 | 6.27 |
| Seasonal naive | 32.72 | 11.15 | 5.48 |

`run.py` stops if any of these numbers fails to match to 4 decimal places.

## What each order tried

1. **Base-year fix for RF logdiff.** When the lag-12 base month falls in COVID (2020-03..2022-12), the log-diff base becomes the last non-COVID same-month value: lag 36 for 2023 Jan-Feb, lag 48 (2019) for 2023 Mar-Dec, and likewise for the 2021-2022 training rows. Variants:
   - `basefix`: the base switch alone.
   - `+momentum`: adds `recovery_log = log(lag1 / clean base of t-1)`. Note that `lag1/lag13` is already in the feature set as `yoy_growth`.
   - `+gap`: adds `base_log_gap = log(lag12 / clean base)`.
   - `momentum_only`: the feature without the base switch.
2. **Combining RF logdiff and RF level.**
   - (a) Weighted average, alpha grid 0..1 in steps of 0.05.
   - (b) Switch to level when the base month is COVID (`is_covid_lag12 = 1`, fixed by design) or when `|log(lag1/lag13)|` exceeds a threshold chosen on 2024.
   - (c) EWA, river's `EWARegressor` update (`w ← w·exp(−eta·loss)`). Loss is each expert's monthly aggregate WAPE. Weights reset to 50/50 every January and only use months before t. This is coded directly because river's `EWARegressor` needs river-native experts. EWA is monthly-only by construction.
3. **LightGBM.** Log-diff target with the same extended features. Grid of 4: leaves 7/15/31, learning rate 0.05/0.1. Frozen and monthly expanding refit.
4. **Per-province river time series** on `log1p(arrivals)`. Each learns 2018-01..(Y-1)-12 in order.
   - Online mode: one-step forecast with exogenous `rain_lag_1`, then learn the actual.
   - Frozen mode: 12-step forecast from December. SNARIMAX gets the rain climatology up to December as its future input, because lagged rain only is allowed.
   - Grids: SNARIMAX (m=12) has 8 configurations; Holt-Winters (additive, seasonality 12) has 6.
5. **Incremental linear models** on the log-diff target. Features: the extended set re-expressed on a log scale (log1p lags and `log(lag1/lag13)` instead of the raw ratio; with raw ratios the linear models diverged to WAPE above 1000%). Raw predictions are clipped to the 1st–99th percentile of the training labels.
   - river (StandardScaler, then model): `LinearRegression`, `PARegressor`, and `BayesianLinearRegression` with forgetting (`smoothing`).
   - Expanding-window sklearn `Ridge` for comparison.

## Results (WAPE %, frozen / monthly; selected configuration only)

| Order | Model (config chosen on 2024) | 2023 | 2024* | 2025 | Beats naive all 3 years |
|---|---|---|---|---|---|
| 0 | Seasonal naive | 32.72 | 11.15 | 5.48 | n/a |
| 0 | **RF logdiff (current Champion)** | 25.77 / 19.97 | 6.31 / 5.94 | 4.79 / 4.83 | yes / yes |
| 0 | RF level | 8.57 / 7.49 | 8.54 / 7.00 | 6.27 / 5.96 | no (2025) |
| 1 | logdiff basefix | 28.07 / 18.79 | 11.76 / 7.31 | 4.73 / 4.73 | no / yes |
| 1 | logdiff basefix + gap (**selected in order 1**) | 28.09 / 11.87 | 6.47 / 5.94 | 4.61 / 4.60 | yes / yes |
| 1 | logdiff basefix + momentum | 13.19 / 12.79 | 7.93 / 7.18 | 5.03 / 4.85 | yes / yes |
| 1 | logdiff basefix + momentum + gap | 13.06 / 11.30 | 7.56 / 6.98 | 4.90 / 4.76 | yes / yes |
| 1 | logdiff momentum only | 34.53 / 20.39 | 6.54 / 6.06 | 4.75 / 4.75 | no / yes |
| 2 | Blend, alpha = 0.80 | 20.35 / 15.98 | 5.90 / 5.60 | **4.47** / **4.52** | yes / yes |
| 2 | **Switch** (COVID base or \|yoy\| > 0.3 → level) | 8.57 / 7.49 | 6.28 / 5.98 | 4.74 / 4.77 | yes / yes |
| 2 | **EWA**, eta = 10 (monthly only) | – / **7.13** | – / 5.64 | – / 4.55 | – / yes |
| 3 | LightGBM (31 leaves, lr 0.05, 300 trees) | 19.53 / 14.03 | 7.77 / 6.95 | 5.73 / 5.39 | no / yes |
| 4 | SNARIMAX (p1 d0 q0, sp1 sd1 sq0, lr 0.003) | 34.80 / 26.39 | 9.95 / 8.37 | 5.15 / 4.70 | no / yes |
| 4 | Holt-Winters (α 0.5, β 0.01, γ 0.1) | 27.37 / 17.28 | 17.90 / 15.10 | 14.87 / 14.34 | no / no |
| 5 | river LinearRegression (lr 3e-4, l2 0.01) | 48.03 / 19.35 | 20.39 / 7.37 | 8.91 / 6.68 | no / no |
| 5 | river PARegressor (C 0.001) | 147.85 / 27.12 | 25.21 / 13.45 | 7.84 / 8.40 | no / no |
| 5 | river BayesianLinearRegression (smoothing 0.995) | 27.30 / 9.63 | 29.18 / 10.06 | 6.27 / 5.67 | no / no |
| 5 | Ridge expanding (alpha 100) | 30.39 / 19.21 | 16.84 / 15.43 | 15.22 / 14.12 | no / no |

\* 2024 is the selection year. MAE, RMSE, MASE, bias, `gap_vs_naive`, and provinces beating naive for every row are in `results.csv`. Monthly WAPE per month is in `monthly_wape.csv`.

Order 1 note: on 2024, no base-fix variant beat plain RF logdiff (6.31). The order-1 selection (`basefix_gap`, 6.47) does not fix 2023 frozen. The momentum variants cut 2023 frozen from 25.8% to 13.1–13.2%, but they lose about 1.2–1.6 points on 2024, so a 2024-only rule cannot pick them.

EWA weights on RF logdiff, month by month:
- 2023: 0.50 → 0.36 → 0.10 → 0.07 → 0.00. It moves to the level forest within 3 months.
- 2024: 0.50 → about 0.70.
- 2025: 0.50 → 0.75.

## Caveats

- **2023 is one abnormal year.** It is the only held-out test of "base year was COVID". The switch rule's COVID branch is a design choice, not something learned: every 2023 base month is a COVID month, so in 2023 the switch is simply RF level.
- **The 2025 gains are small.** Switch beats RF logdiff by 0.05 points, EWA by 0.28 points (monthly), and the blend by 0.32 points (frozen). No significance test or bootstrap was run, and 77 provinces × 12 months is a small sample.
- **2024 cannot tune everything.** 2024 has no COVID-base rows, so it cannot tune anything about the COVID case. It is also the original RF logdiff selection year.
- **"Frozen" means different things.** For the river time-series models it means a 12-step forecast with no target-year data at all. For the batch models it means fixed parameters with fresh lag features, so the two frozen columns are not directly comparable.
- **Linear-model safeguards.** The linear models depend on the log-scale features and the label clipping described above. Without them they diverge.
- **Not run:** the optional order 3 extra (LightGBM `init_model` warm start vs full refit). A full monthly refit takes under a second at this data size, so a warm start has no speed advantage to show.

## Recommendation (for DS/ML to decide)

1. **Most robust candidate: EWA of RF logdiff and RF level, with monthly refit.** It is the only model that is best or close to best in all three years: 7.13 / 5.64 / 4.55 vs the current 19.97 / 5.94 / 4.83 (monthly). It fits the online framing and Champion/Challenger naturally: the experts stay as they are and only two weights move each month. Trade-off: 2025 is 4.55, not quite the blend's 4.47, in exchange for 2023 at 7.13 instead of 15.98–20.35.
2. **Simplest frozen-compatible option: the switch rule.** It equals or beats RF logdiff in every year (8.57 / 6.28 / 4.74 frozen) and costs nothing to run. Its 2023 gain comes from a hand-written COVID rule, so it is as good as that rule.
3. **Blend with alpha 0.8.** Best 2025 (4.47 frozen), but it still fails 2023 (20.35). Use it only if abnormal base years are considered out of scope.
4. **Not recommended as Champion:** LightGBM, SNARIMAX, Holt-Winters, river linear models, and Ridge. All are worse than RF logdiff on 2024 and 2025. SNARIMAX online (4.70 on 2025) is a reasonable per-province contrast model for the report.
5. **Worth a follow-up:** combine the EWA or switch with the order-1 `basefix_momentum_gap` forest (13.06 on 2023 frozen) as the logdiff expert. That was not tested here.
