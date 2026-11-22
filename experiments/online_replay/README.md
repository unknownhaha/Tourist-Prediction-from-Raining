# 2025 online replay (Champion EWA, drift log, 2023 demo)

## Manual monthly training studio

On Windows run `experiments/online_replay/start_manual_studio.ps1` from the checkout
or run `python -m experiments.online_replay.manual_studio` with the experiment dependencies.
Open `http://127.0.0.1:8774/`. Each Train click walks one month from 2024-01 through 2025-12.
The serving model starts as Linear Regression fit on 2019-01..2023-12 with the six documented
forecast features. Ridge, CatBoost, and Random Forest are the challengers. Seasonal naive is scored
beside them and is not eligible for promotion. Page-Hinkley watches the served model's monthly WAPE.
The twelve one-step Linear WAPEs of 2023 supply the mean and sample standard deviation. The
standardized detector starts with count 12, delta 0.005, and threshold 0.7. An alarm does not
promote a challenger. PSI on `guest_lag_1`, `guest_lag_12`, `rain_lag_1`, and `rain_roll3` is a
watch against the same calendar month in 2022-2023. The session is
`outputs/manual_studio/level_session.sqlite`.

Each Train request predicts from the models fit through the previous month, then opens
that month's arrivals, scores Linear, Ridge, CatBoost, Random Forest, and seasonal naive,
applies Page-Hinkley, and refits all four learners through the revealed month. All four
keep learning after a promotion. SQLite commits the new parameters and the month history
together. Reloading resumes the session. Reset restores the December 2023 snapshot.

## Select among eight models before monthly replay

Run `python -m experiments.online_replay.compare_models` from the repository root
with the experiment dependencies and CatBoost installed. Results are written to
`results/comparison_2024/`. The target is monthly provincial guest arrivals. All eight
recipes forecast the same 924 province-months in 2024. Training starts in January 2019
after building lags with 2018 history. During selection, the fitted models, imputers,
scalers and the Champion's initial 50/50 expert weights remain fixed at December 2023.
Observed feature lags advance monthly. The lowest pooled 2024 WAPE wins, with MAE
as the first tiebreaker. The scoring year is reused in the retrospective online replay.
Only 2025 is outside this model-selection period.

Seasonal naive uses lag 12. Linear Regression, Ridge (alpha 1) and CatBoost
(300 trees, depth 6, learning rate 0.05) predict levels using the extended matrix.
Random Forest uses the repository's level expert. RF logdiff models the log change
from lag 12. YoY3 adds the lagged three-month year-over-year log ratio to RF logdiff.
Champion combines the seasonal-adjusted logdiff and level experts. Its original
configuration was itself chosen on 2024 in earlier work.

The online winner forecasts all provinces before the next expanding-window refit.
The run saves the fitted YoY3 forests through December 2023 and December 2025,
forecasts and monthly drift decisions. Winner-specific rolling forecast errors from
2022–2023 initialize ADWIN. The existing challenger promotion policy is retained,
excluding the duplicate of the winning model from the standby list.

## Fresh 2019–2023 fit and continuous 2024–2025 stream

From the repository root run `python -m experiments.online_replay.train_stream` with the
repository experiment dependencies installed. The run starts without cached predictions.
2018 supplies lag and seasonal history. The first two forests fit on eligible rows of
2019-01 through 2023-12. January 2024 starts with equal expert weights. Each calendar
month is forecast for every province before its losses update the EWA weights.
The forests refit on the expanding history and the weights carry across January 2025.
The December 2025 observations are included in the final saved fit.

The original C0/E2 configuration and eta 5 were chosen on 2024 in earlier work.
This run fixes that recipe in advance. Its 2024 scores therefore have prior model-selection exposure.
The drift detector starts with rolling one-month-ahead Champion errors from 2022–2023.
PSI uses same-calendar-month observations from the two years ending December 2023.
The original PSI + upward ADWIN trigger, six challenger definitions, two-month confirmation
and rollback policy apply throughout both stream years.

Outputs are in `results/stream_2024_2025/`. The four joblib files store the initial and final
scikit-learn estimators, training medians, feature order and scalers. For inference, build the
same feature matrix as `experts.fit_predict`, align columns, fill missing values with training
medians, scale, predict, and call `reconstruct` with each target and its lag-12 input.
Use equal mixture weights for the initial checkpoint and `summary.json`'s
`weights_after_last_update` for the final checkpoint. `forecasts.csv` records served
predictions, `monthly.csv` records detector and model decisions, and `events.jsonl`
contains policy events. `challenger_readiness.csv` exercises all six candidate predictors
on January 2024 at startup cutoff independently of promotion eligibility.

Implements `/workspace/plans/online-ml-replay-2025-plan.md` (Desin) with all of Desin's defaults, in the
trimmed form the PM asked for (see "What was trimmed"). Branch `exp/online-replay-2025` from
`exp/online-model-trials` @ 6cc2a63. Local commits only. Final model choice belongs to DS/ML: everything here is a recommendation.

Run (about 10 minutes from a cold cache on 8 cores; the cache `results/predictions.parquet` is git-ignored):

```
./.venv/bin/python experiments/online_replay/run.py --stage all
# or one stage: reproduce dq select covid ewa replay2025 demo2023 drift_demos rain results charts
```

`--stage all` deletes the prediction cache first, so a full run is always cold (about 15 minutes with
the drift demos). Single stages reuse the cache. The cache has no code or data fingerprint, so delete it
by hand after changing features or data and before running a single stage.

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

**Plain statement: with the approved settings, ADWIN cannot fire on drift that starts inside the replay year.** The local ADWIN uses a Hoeffding bound for values in [0, 1]. It needs a window of 32 or more points and at least 16 points on each side of a cut. With monthly WAPE input, a cut therefore needs a mean shift of about 0.8 WAPE (80 pt), with at least 16 post-shift months. With a 24-month seed, month 8 (August) is only the first month in which the window is long enough to be *tested*. A cut there needs a shift that began at least 8 months *before* the year, so it is not in-year detection. `tests/test_online_replay_drift.py::test_approved_adwin_cannot_fire_on_a_step_that_starts_inside_the_replay_year` pins this: a 5 % -> 100 % WAPE step from January, with PSI alarming all year, gives 12 x `watch` and never `prepare_retrain`. See "Drift demos" for parameters that do fire.

**Input gate placement:** `check_inputs` runs through the `before_month` hook of `policy.replay`, *before* each month is served and decided. A hard failure raises `GateError` and stops the loop at that month. This is tested in `test_gate_runs_before_each_month_is_served`, and in the e2e poison test, where the poisoned stream stops before 2025-08 is served. **The drift demos (`drift_demos.py`) are not gated like the 2025 replay.** The ILLUSTRATIVE_* detector runs are **ungated**: `check_inputs` never runs for them. Only the `approved_adwin` demo runs call the gate, in non-strict mode, because COVID months push `exp_lr_recent` outside its [-5, 5] range. That feature problem is logged in `gate_feature_problems` instead of raised; DQ hard failures would still raise. The 2025 replay and the original 2023 demo are gated strictly.

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

## Drift demos (follow-up): `drift_demos.py`, stage `drift_demos`

Two extra demos. The Champion and the 2025 replay are unchanged. Each detector runs on two tracks: **A** = monthly-refit RF logdiff (old model), **B** = EWA Champion (C0, E2, eta 5). Trigger `psi_and_adwin`, preset margin_0005_confirm_2, rollback 2 months, `adwin_direction: up`. Detectors:
- `approved_adwin`: delta 0.002, window >= 32, >= 16 per side, raw monthly WAPE (the config).
- `ILLUSTRATIVE_adwin_min16_side8`: same, window >= 16, >= 8 per side.
- `ILLUSTRATIVE_adwin_min16_side8_relative`: 16/8 on WAPE divided by the seed-mean WAPE (`ScaledADWIN`).
- `..._relative_confirm1`: the same with preset margin_0005_confirm_1.

ILLUSTRATIVE rows are *not* tuned and are not a config.

**Seeds (both variants):** one-step OOS monthly WAPE for 2019-02..2019-12 (11 points; 2019-01 has no training rows). Track A uses `champion_oos_wape` with logdiff_expanding; Track B uses EWA over the experts' 2019 one-step predictions. Why 2019: it is the last normal pre-COVID year, and it is *before* both replay windows, so the seed is leak-free. Pre-2019 rows have no features, so 2018 could not be scored. 2024 would be the future for a 2023 demo. PSI reference: same calendar month of 2019 (variant 1, never refreshed) and of 2021-2022 (variant 2, as in the original 2023 demo). A cut with no retrain is a downward cut, which `adwin_direction: up` ignores.

**Variant 1: 2020-01..2023-12** (`results/replay_2020_*`, `charts/replay_2020_drift.png`)

| detector | track | ADWIN cuts | retrain (prepare_retrain) | promotions / rollbacks | served WAPE |
|---|---|---|---|---|---|
| approved | A | 2023-08 (down, ignored) | none | none | 35.98 % |
| approved | B | 2023-12 (down, ignored) | none | none | 20.98 % |
| raw 16/8 | A | 2020-05, 2021-01, 2021-09, 2022-05 | 2020-05, 2021-09 | none | 35.98 % |
| raw 16/8 | B | 2020-05, 2021-01 | 2020-05 | none | 20.98 % |
| relative 16/8 | A | 2020-05, 2021-01, 2021-09, 2022-05, 2023-02, 2023-10 | 2020-05, 2021-09 | none | 35.98 % |
| relative 16/8 | B | 2020-05, 2021-01, 2021-09, 2022-05, 2023-01, 2023-09 | 2020-05, 2021-09 | none | 20.98 % |
| relative 16/8 + confirm-1 | A | 2020-05, 2021-01, 2021-09, 2022-05, 2023-01, 2023-09 (path changes after the promotion) | 2020-05, 2021-09 | promote expert_level 2020-05; promote logdiff_expanding 2021-09; rollback to expert_level@2020-04 in 2021-11 | 20.69 % |
| relative 16/8 + confirm-1 | B | same as relative 16/8 B | 2020-05, 2021-09 | same events | 20.34 % |

PSI alarms (watch) in nearly every month of 2020-2023 against the 2019 reference. Under confirm-2 nothing is promoted, because the triggers are isolated months.

**Variant 2: 2023 with a normal-period seed** (`results/replay_2023_normalseed_*`, `charts/replay_2023_normalseed_drift.png`)

| detector | track | ADWIN cuts | retrain | promotions | served WAPE |
|---|---|---|---|---|---|
| approved | A / B | none (11 + 12 = 23 points < 32: cannot cut) | none | none | 19.97 % / 6.34 % |
| raw 16/8 | A / B | none | none | none | 19.97 % / 6.34 % |
| relative 16/8 | A | 2023-05 | 2023-05 | none | 19.97 % |
| relative 16/8 | B | none | none | none | 6.34 % |
| relative 16/8 + confirm-1 | A | 2023-05 | 2023-05 | promote expert_level 2023-05 | 15.45 % |

PSI watches in all 12 months. Caveat: a promoted `expert_*` challenger is served as a monthly refit, not frozen.

**What ADWIN would need (recommendation for DS/ML, not applied):** delta only enters through a log, so moving it from 0.002 to 0.05 shrinks the bound by only about 17 %. On raw monthly WAPE, a 0.15 shift needs about 240 points per side, which is impossible with monthly data. The levers that work are a smaller window (16 total / 8 per side) and a **relative input** (WAPE divided by the normal-period mean WAPE). That combination detects a sustained shift above about 1.14x the normal level after 8 or more post-shift months. A 20x step is caught in its first month (`test_relative_adwin_16_8_fires_in_year_and_logs_prepare_retrain`). river's ADWIN was also tried. It has a scale-dependent term, so it misses the 4 % -> 20 % step at WAPE scale too (tested). An alternative is province-level errors as the ADWIN input (77 points per month).

## Rain: why my 0.0006 / p = 0.058 differs from HYPOTHESES.md H1 (p = 0.73, ΔR² "0.00005 pp")

They are different specifications, and both say rain has little effect:
- **Mine (`rain_report.py`, = Desin's re-run):** dependent variable = YoY log growth log(Y/Y_-12). Control = momentum log(lag1/lag13) only, no fixed effects. Rain = lagged log YoY rain change. Province-clustered CR1, normal p. Years 2019, 2024 and 2025 (n = 2695). Base R² 0.4295, ΔR² 0.0006 (0.06 pp), coefficient 0.0020, p = 0.058.
- **H1 (`/workspace/hypotheses/run_hypotheses.py`):** dependent variable = log arrivals *level*. Controls = province FE + calendar-month FE + logY_l1 + logY_l12. Rain = raw rain_l1 in mm. Same clusters, years and n. Base R² 0.99595, so ΔR² = 5e-7. The "0.00005 pp" label is that value x 100. Coefficient -7.2e-6 per mm, t = -0.35, p = 0.73.
- The tiny ΔR² in H1 comes from the level model with fixed effects, which already explains 99.6 % of the variance. It is not a contradiction.
- **For the slides:** quote H1 as the primary test (more controls, the forecast-relevant level model): p = 0.73, IQR effect about -0.13 %. Quote mine as a robustness check: on growth rates lagged rain adds ΔR² 0.0006 (p = 0.058). Do not put the two ΔR² numbers side by side as if they were comparable.

## How the work was committed

Some plan steps were combined into one commit: steps 0 + 2 (reproduce + selection scaffolding) and steps 3 + 4 (COVID A/B + EWA experts). The rain report was committed before the drift-demo outputs. The QA follow-ups (H1 tests, gate inside the loop, cold cache on `--stage all`, rounded config, label-poison e2e tests) are separate later commits. History was not rewritten and nothing was pushed. DQ was deliberately left unchanged (on hold pending a decision).

## Caveats and recommendations for DS/ML

- 2024 selection is optimistic (see above). The held-out years agree with the C0 and E2 choices, except that E3 would be slightly better in 2025.
- DQ normal-bucket accuracy (0.893) is below the 0.93 target, mainly because of foreign-arrival recovery jumps. Options: score accuracy on total arrivals + rain (0.952), or add a recovery bucket.
- ADWIN on monthly WAPE with the approved settings cannot fire on in-year drift. Recommended (see "Drift demos"): a 16/8 window on relative WAPE, or province-level errors as input. That is a DS/ML decision and is not applied here.
- Seeds: RF random_state 42 everywhere. The seed-robustness footnote (seeds 1-3) was not run because of time.
