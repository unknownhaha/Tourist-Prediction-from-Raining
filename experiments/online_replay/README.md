# 2025 online replay (Champion EWA, drift log, 2023 demo)

Implements `/workspace/plans/online-ml-replay-2025-plan.md` (Desin) with all of Desin's defaults, in the
trimmed form the PM asked for (see "What was trimmed"). Branch `exp/online-replay-2025` from
`exp/online-model-trials` @ 6cc2a63. Local commits only. Final model choice belongs to DS/ML: everything here is a recommendation.

Run (about 10 minutes from a cold cache on 8 cores; the cache `results/predictions.parquet` is git-ignored):

```
./.venv/bin/python experiments/online_replay/run.py --stage all
# or one stage: reproduce dq select covid ewa replay2025 demo2023 rain results charts
```

`select` rewrites the `selection` block of `config.json` from 2024 data. All other stages read the frozen choice and refuse to run without the `selected_on: 2024` stamp.

## Protocol and leakage

- Rolling origin. Every forecast for month t comes from a fit on 2019-01..t-1 (predict, then learn). 2018 is used only as lag history.
- **All choices are made on 2024 monthly WAPE only**: COVID variant, eta, and 2 vs 3 experts. `selection.py` refuses any frame that is not 2024. The choice is frozen in `config.json` before 2023 and 2025 are scored. 2024 was also the year RF logdiff was originally picked, and it has no COVID rows, so 2024 numbers are optimistic and can barely tell the COVID variants apart.
- How each feature avoids leakage (tests in `tests/test_online_replay_leakage.py`):
  - `exp_lr_recent`, `exp_lr3_recent`, `seasonal_last_month_forecast`: lags t-1..t-3 and t-12 come from the backtest table. The seasonal profile S for a row in year Y uses the **last two normal years strictly before Y** (2023 -> 2018+2019, 2024 -> 2019+2023, 2025 -> 2023+2024). A test sets every month >= Y-01 to NaN and gets identical features.
  - `rec_*`, `nat_rec_*`: same baseline years. The national lags are sums of the table's own lag columns.
  - `event_lag1`: the event in t-1. `event_now` is an oracle column, and `fit_predict` refuses it.
  - EWA weights for month t use only the losses of months before t, and they reset each January.
  - The whole panel from 2024-06 onward is replaced with 1e12 (guests) and 1e6 (rain). Month 2024-06 still gets the same features and the same forecasts from the logdiff and level experts.
- Input gate (`gate.py`): DQ v2 runs on panel rows <= t-1 every month, and the new feature columns of month t are range-checked. Its scores appear in the replay log.

## Reference numbers (step 0/2): all reproduced

`results/reproduce.json`. The online_trials rows match to 4 dp: RF logdiff 19.97/5.94/4.83, RF level 7.49/7.00/5.96, EWA eta 10 7.13/5.64/4.55, seasonal naive 32.72/11.15/5.48. The ported features rebuild the **Champion baseline at 6.5021 / 5.5909 / 4.5366** (target 6.50/5.59/4.54). The no-rain variant gives 6.6572 / 5.7193 / 4.4358 (target 6.66/5.72/4.44). The ported level expert equals `run_year` to < 1e-6.

## Headline (monthly WAPE %, `results/results.csv`)

<!-- headline:start -->
| model | 2023 | 2024 (selection) | 2025 |
|---|---|---|---|
| Seasonal naive | 32.72 | 11.15 | 5.48 |
| Old Champion: RF logdiff, monthly refit | 19.97 | 5.94 | 4.83 |
| Old EWA (logdiff, level), eta 10 | 7.13 | 5.64 | 4.55 |
| Champion baseline: EWA(logdiff+exp_lr_recent3, level), eta 10 | 6.50 | 5.59 | 4.54 |
| **Final Champion: C0, E2, eta 5 (chosen on 2024)** | **6.34** | **5.58** | **4.52** |
| Final Champion without rain (ablation) | 6.45 | 5.63 | 4.47 |
<!-- headline:end -->

Final Champion = EWA of (a) `rf|logdiff|ext|d10_leaf3` + `exp_lr_recent`, `exp_lr3_recent`, and (b) `rf|level|base|d10_leaf3`. It keeps the binary COVID flags. Both experts refit monthly, and eta = 5. Re-selecting eta on 2024 (grid 0.5..100) picked 5 (5.5779) over 10 (5.5909). Against seasonal naive the final Champion has a WAPE gap of -26.4 / -5.6 / -0.96 pt and beats naive in 75 / 62 / 53 of 77 provinces.

## DQ v2 (step 1, minimal)

`experiments/new_features/dq.py` with `accuracy_rule: v2` (in `dq_config.json`). `results/dq_report.json`, `charts/dq_accuracy_buckets.png`.
- Rain is compared with the **same calendar month** of earlier years, using a robust MAD z of log1p(rain) with limit 3.5. This gives 114 rain flags instead of 1,352 yoy flags plus 125 robust-z flags.
- Guest series use a log-change YoY rule (|log(x/x12)| > ln 2). Their robust-z history leaves out COVID-window months.
- Accuracy is scored per bucket: normal / covid / covid_base. `accuracy` (and so `scores.overall`) = the normal bucket.

| | value |
|---|---|
| legacy v1 accuracy (all rows; `accuracy_rule: v1` reproduces it exactly) | 0.5392 |
| **v2 accuracy, normal bucket** | **0.893** |
| v2 normal bucket, total arrivals + rain flags only | 0.952 |
| v2 COVID months / COVID-base months (reported, not scored) | 0.059 / 0.308 |
| overall DQ score v1 -> v2 | 0.923 -> 0.982 |

**Below Desin's 0.93 target:** the normal bucket is 0.893, which is also under the 0.90 warning floor (`below_normal_floor: true`, a warning only). Most of the remaining normal-bucket flags are foreign-arrival recovery jumps (2024 vs 2023: 103 yoy flags) and 2019 robust-z flags. Scored on total arrivals and rain alone, the bucket is 0.952. Validation still shows 0 hard failures and 2 warnings, and no hard stop is crossed. The gate refactor beyond `gate.py` was skipped.

## COVID A/B (step 3, trimmed to C0 vs C3; C4 shown for "with/without")

`results/covid_ab.csv`, `results/covid_with_without.csv`, `charts/covid_ab.png`. The level expert gets the same COVID, recovery and event columns as the logdiff expert, and eta is re-selected per variant on 2024.

| variant | eta | 2023 | 2024 (selection) | 2025 |
|---|---|---|---|---|
| **C0 binary COVID flags (selected)** | 5 | 6.34 | 5.58 | 4.52 |
| C3 recovery level vs last two normal years + `event_lag1` | 2 | 6.52 | 6.90 | 4.80 |
| C4 no COVID info (report-only "without" row, not a candidate) | 5 | 6.67 | 5.32 | 4.75 |

C0 wins clearly on 2024 and is also the better candidate on 2023 and 2025. C4 has the lowest 2024 score (5.32), but the plan makes it a report-only row, so it was not eligible. It is worse on both held-out years. Caveats: the recovery baseline for rows from 2020 to 2023 is necessarily 2018/2019. Event dates (`events.json`) still need DS/ML confirmation. There is only one training event before 2025 (Chiang Rai 2024-09).

## EWA 2 vs 3 experts (step 4)

`results/ewa_ab.csv`, `results/ewa_weights.csv`, `charts/ewa_ab.png`, `charts/ewa_weights_{2023,2024,2025}.png`.

| variant | eta | 2023 | 2024 (selection) | 2025 |
|---|---|---|---|---|
| **E2 (logdiff, level) (selected)** | 5 | 6.34 | 5.58 | 4.52 |
| E3 (+ no-ML seasonal last-month forecast) | 0.5 | 6.92 | 5.60 | 4.46 |

On 2024, E3 is 0.025 pt worse, which fails the >= 0.02 pt gain rule, so E2 is kept. E3 would have been 0.06 pt better on 2025 and 0.58 pt worse on 2023. Per the protocol, the choice does not switch on held-out years. The third expert alone scores 9.56 / 6.70 / 5.20.

## Replay 2025 (step 5): how to read the log

`results/replay_2025_log.csv` has one row per month with these columns:
- DQ scores from the gate;
- served, naive and per-expert WAPE;
- EWA weights;
- ADWIN window length, mean, cut, and cut direction;
- PSI value for each of `guest_lag_1`, `guest_lag_12`, `rain_lag_1`, `rain_roll3`, plus the alarm list and the rain confirmation;
- trigger, action and decision.

Other outputs: `results/replay_2025_events.jsonl`, `replay_2025_served.csv`, `replay_2025_summary.json`, and `charts/replay_2025_drift.png`. The chart's three panels are: WAPE served vs naive with the ADWIN window mean, PSI per feature with the alarm line, and action markers.

Settings: Seasonal-2Y PSI reference up to 2024-12 with alarm >= 0.15 (`studio_models.PSI_THRESHOLD`). A rain alarm counts only when it is confirmed two months in a row. ADWIN uses delta 0.002 on monthly WAPE, seeded with the Champion's one-step OOS WAPE for 2023-01..2024-12 (24 points), with `adwin_direction: up`. The trigger is `psi_and_adwin`. On a trigger the loop runs prepare_retrain with preset margin_0005_confirm_2 and rollback after 2 months. Challengers: logdiff_expanding, logdiff_12m, logdiff_seasonal_2y, yoy3_expanding, and each single expert.

**Result: no ADWIN cut in 2025 and no prepare_retrain.** PSI watches fired in 2025-02, 03, 05, 06, 07, 08 and 12, almost all on rain features (rain_roll3 or confirmed rain_lag_1). Guest PSI stayed < 0.11 all year. Served WAPE is 4.5226 %, the same as the offline Champion number, so the loop does not change the model. The ADWIN window mean fell from 5.9 % to 5.5 %.

`policy.replay` was parameterised with `initial_model`, `trained_through`, `reference_end`, a `log` callback and `adwin_direction`. Its defaults reproduce the approved 2025 run exactly: served WAPE 0.04788460348726727 with 0 promotions. This is asserted in the replay2025 stage.

**Why ADWIN rarely fires here:** the local ADWIN uses a Hoeffding bound for values in [0, 1]. It needs a window of 32 or more points and at least 16 points on each side of a cut. With monthly WAPE input, a cut therefore needs a mean shift of about 0.8 sustained over 16 months. Within one replay year after a 24-month seed, that can only happen when the shift started months before the year. The first month that can cut is month 8.

## 2023 drift demo (step 6)

`results/replay_2023_demo_log.csv` (column `track`), `replay_2023_demo_events.jsonl`, `replay_2023_demo_summary.json`, and `charts/replay_2023_drift.png`. The PSI reference is 2021-2022 (COVID era). The ADWIN seed is OOS monthly WAPE for 2021-01..2022-12.
- **Track A** serves the old RF logdiff (19.97 %). **PSI alarms every month** (watch). The guest-lag PSI reaches about 0.4-0.7 from February, plus rain. **ADWIN never cuts at delta 0.002.** The seed errors are huge: mean monthly WAPE 0.83 over 2021-2022, peaking at 4.38 in 2021-07. Relative to the seed, 2023 is an *improvement*. A cut would be a downward one, and with the 32/16 limits even that cannot clear the bound. Result: **no prepare_retrain**.
- **Track B** serves the new EWA Champion (6.34 %). It behaves the same way: 12 PSI watches, no cut, no retrain.
- **Sensitivity panel (illustrative only, not tuned and not a config):** Track A with delta 0.05 cuts in **2023-10**. The cut is a **downward** one (mean 1.02 before, 0.33 after). `adwin_direction: up` correctly ignores it, so there is still no retrain.

Conclusion: in 2023, PSI clearly detects the feature shift away from the COVID-era reference. The error-based ADWIN trigger does not fire, because errors fell after COVID. Delta was not tuned on 2023.

## Rain (step 7): rain has little effect; kept for the project framing

`results/rain_effect.csv` (all statistics) and `results/rain_ablation.csv`, with `charts/rain_effect.png`. Rain enters the model lagged only.
- Drop-rain ablation inside the final Champion (both experts lose `rain_lag_1`, `rain_roll3` and `rain_anomaly`): 6.34 -> 6.45, 5.58 -> 5.63, 4.52 -> 4.47. The differences are +0.12 / +0.05 / -0.05 pt, mixed in sign and within noise.
- Incremental R^2 of lagged rain on the YoY log-growth label, on top of momentum: **0.0006**. Coefficient 0.0020, province-clustered p = 0.058. For the lagged anomaly the figures are ΔR² 0.0007 and p = 0.166 (2019 + 2024 + 2025, n = 2695).
- Same-month rain (descriptive only, **not** a model input): ΔR² 0.0042, coefficient -0.0053, p < 0.001. The wettest quintile has **about 2.0 % fewer arrivals** than the driest (1.5 % fewer than the other four quintiles).
- RF permutation importance in the frozen logdiff expert (5 shuffles): rain_lag_1 +0.08 pt (2024) and -0.02 pt (2025); rain_roll3 +0.14 pt (2024) and +0.43 pt (2025).
- These match Desin's re-run (`/workspace/plans/evidence/rain_stats_stdout.txt`): R^2 0.4295 / 0.4301 / 0.4337, p 0.058 / 0.166, quintile means 0.0083 ... -0.0122. statsmodels is not installed in the venv, so the OLS with CR1 clustered SE is re-implemented in numpy and gives the same values.

## Caveats and recommendations for DS/ML

- 2024 selection is optimistic (see above). The held-out years agree with the C0 and E2 choices, except that E3 would be slightly better in 2025.
- DQ normal-bucket accuracy (0.893) is below the 0.93 target, mainly because of foreign-arrival recovery jumps. Options: score accuracy on total arrivals + rain (0.952), or add a recovery bucket.
- ADWIN on monthly WAPE with this implementation is effectively unable to fire within a year. Consider province-level absolute errors as ADWIN input, river's ADWIN (clock/grace), or a smaller minimum window. That is a DS/ML decision, not tuned here.
- Seeds: RF random_state 42 everywhere. The seed-robustness footnote (seeds 1-3) was not run because of time.
