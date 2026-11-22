# Slide-deck plan: Tourist-Prediction-from-Raining (for Canvo to build in Canva)

Written 2026-11-22 19:20 (Asia/Bangkok) by Desin. Planning only; no repo changes.
Audience: a university professor and experienced data engineer. Presenter: the team's Data Engineer. Tone: method, evidence, limitations; no hype.
Length: 16 slides plus 1 optional appendix slide. Target talk time about 16-20 minutes.
**FINAL, 2026-11-22 20:15 (Asia/Bangkok): ready for Canvo to build.** All Webber results are in (commit 54eeb6d on local branch exp/online-replay-2025). The only remaining placeholder is the slide 13 chart, which Webber is redoing.
Earlier revision 2026-11-22 19:40 (Asia/Bangkok): added the ARF slide (new slide 9), renumbered 9-15 → 10-16, rewrote the hypotheses slide (now 14) from /workspace/hypotheses/HYPOTHESES.md, aligned the rain numbers on slide 6 and the limitations with the hypothesis tests.

## Number rules (for Canvo and the presenter)
- Every number on a slide is copied from the file cited next to it. Do not round further than shown here and do not add numbers that are not listed.
- WAPE is in % (sum|y−ŷ| / sum y over 77 provinces × 12 months = 924 rows per year). "Frozen" = model fit through Dec of the prior year, one forecast for the whole year. "Monthly" = refit on all data before month t, then predict t.
- There are no TBD cells left. The only placeholder is the slide 13 chart: use the current PNG with a visible red corner tag "PLACEHOLDER – to be updated" (Webber is redoing it).
- **Final Champion = C0 (binary COVID flags), 2 experts, eta 5: monthly WAPE 6.34 / 5.58 / 4.52** (2023 / 2024* / 2025) `[OR]` results/results.csv, README "Headline". The earlier 6.50 / 5.59 / 4.54 (eta 10) is shown only as the labelled "Champion baseline (history)".

Source shorthand used below:
- `[OT]` = /workspace/tpr-test/experiments/online_trials/results.csv (selected configs also summarised in online_trials/README.md)
- `[BT]` = /workspace/tpr-test/experiments/backtest/results.csv
- `[TR]` = /workspace/data_review/transform_results.csv
- `[RAIN]` = /workspace/plans/evidence/rain_stats_stdout.txt (stdout of /workspace/data_review/rain.py, re-run read-only on 2026-11-22; the script prints these numbers and does not save them anywhere else)
- `[EDA2]` = /workspace/data_review/eda2.json; `[DQF]` = /workspace/data_review/dq_accuracy_flags.csv
- `[DQ]` = /workspace/tpr-test/experiments/online_trials/dq_report.json; `[VAL]` = .../online_trials/validation_report.json
- `[PA]` = /workspace/tpr-test/experiments/psi_adwin/README.md, config.json, results.json
- `[CFG]` = /workspace/tpr-test/config/sources.yaml
- `[ARF]` = /workspace/arf_experiment/REPORT.md (tables there are generated from /workspace/arf_experiment/results/yearly_wape_pct.csv, drift_counts_by_year.csv, summary.json)
- `[HYP]` = /workspace/hypotheses/HYPOTHESES.md (machine-readable: /workspace/hypotheses/stats/summary_table.csv, hypothesis_stats.json)
- `[OR]` = /workspace/tpr-test/experiments/online_replay/ at commit 54eeb6d (results/ and charts/; README.md; config.json). Note: Webber has uncommitted follow-up work in this folder (2020-2023 replay); numbers in this deck come from the committed files listed per slide.

## Global style
- Clean, white background, one idea per slide, a short headline sentence as the title. Use a sans-serif font (Inter or Roboto). Body text ≥ 18 pt, table text ≥ 14 pt.
- Fixed colour meaning across all slides and charts:
  - **Actual** = near-black #222222 (solid line)
  - **Predicted / Champion** = blue #1F77B4
  - **Baseline / seasonal naive** = grey #9E9E9E (dashed)
  - **Alternative models / challengers** = teal #17BECF, plus light shades of it
  - **Drift events**: PSI watch = amber #F2A900 (triangle), ADWIN cut = red #D62728 (vertical line), prepare_retrain = purple #9467BD (diamond), promote = green #2CA02C, rollback = dark red #8B0000
  - **Selection year 2024** column = light-yellow background in every table (it is the tuning year, not a test)
  - **Placeholder** = red corner tag "PLACEHOLDER – to be updated" (slide 13 only)
- Webber's PNGs use matplotlib default colours (e.g. naive is blue in headline_wape.png). Do not recolour the PNGs. Where colour meaning matters (slide 11 headline), Canvo may rebuild the bar chart natively from the numbers given, using the colour key above; otherwise insert PNGs as-is and add a one-line legend note.
- PSI alarm threshold in the replay is 0.15 (studio_models.PSI_THRESHOLD), not 0.2; the charts show both lines (0.15 alarm, 0.2 rule of thumb).
- Tables: best value per held-out column in bold. Always show the 2024 column with the note "*2024 = selection year*".
- Footer on every slide: source path(s) in 10 pt grey.
- No stock photos, no icons beyond a simple pipeline diagram.

---

## Slide 1: Title
- **Key message:** Forecasting monthly tourist arrivals for 77 Thai provinces from rainfall, run as an online ML replay.
- **Layout:** title slide. Title centred, subtitle below, team and date at the bottom.
- **Content:** "Tourist-Prediction-from-Raining: an online ML replay of monthly provincial arrivals" · Data Engineer (presenter), team names · date of presentation.
- **Chart/table:** none.
- **Speaker notes:** Introduce the project as a data engineering and ML evaluation exercise, not just a model. Say up front that the honest headline is "rain matters very little; the hard part was COVID".

## Slide 2: Problem and framing
- **Key message:** Predict next month's guest arrivals per province, using only information available before that month.
- **Layout:** two columns. Left: bullets. Right: a simple timeline diagram (2018 → 2025, with the COVID window shaded).
- **Bullets:**
  - Target: monthly guest arrivals (total), 77 provinces, 2018-01..2025-12 `[CFG]` (date_window)
  - Inputs: lagged arrivals, lagged rainfall (rain_lag_1, rain_roll3, rain anomaly), calendar, COVID flags `[PA]` config.json
  - Framing: online ML. Each month predict, then learn the actual; drift detection decides when to retrain
  - COVID window 2020-03..2022-12 `[CFG]`
- **Chart:** a timeline drawn in Canva (no data file).
- **Speaker notes:** Rain was the original hypothesis. The evaluation is strictly forward in time, so every forecast for month t uses only data up to t−1. The online framing means the pipeline itself is part of the result, not just the model.

## Slide 3: Data and pipeline
- **Key message:** Two public sources joined into one province-month table with 7,392 rows and no gaps.
- **Layout:** pipeline diagram across the top (Sources → Ingest → Clean/crosswalk → Unified province-month → Features → Validation + DQ gate → Model). Three small fact boxes underneath.
- **Bullets / fact boxes:**
  - Arrivals: Ministry of Tourism and Sports (MOTS) monthly statistics; rainfall: HII spatial rain dataset `[CFG]` (mots, hii)
  - 77 provinces × 96 months = 7,392 rows; timeliness gap fraction 0.0; duplicate keys 0 `[DQ]` (timeliness, uniqueness)
  - Thai/foreign split published from 2019-01 (2018 split unpublished) `[DQ]` warnings
  - Code: scripts/build_unified_province_month.py, src/ingest/{mots_crawler,hii}.py
- **Chart:** pipeline diagram drawn in Canva.
- **Speaker notes:** This is the data engineering core: crawling, the province crosswalk, and one unified table that every experiment reads. The validation and DQ gate sits before features reach any model, and it can stop a run.

## Slide 4: Validation and data quality
- **Key message:** Schema and contract checks pass. The old DQ "accuracy" score of 0.54 was a measurement artefact; a seasonal rain rule and separate COVID buckets raise it to 0.893 on normal months.
- **Layout:** left: before/after table. Right: dq_accuracy_buckets.png.
- **Bullets:**
  - Validation: 21 checks, 16 hard, 0 hard failures, 2 warnings (Thai YoY NaN = 42, foreign YoY NaN = 667, from zero denominators) `[VAL]`
  - Old rule flagged any YoY change > 100 %, including rain (which routinely doubles in dry months) and the COVID collapse: 3,406 flagged rows `[DQF]`, `[EDA2]`
  - New rule (v2): rain vs the same calendar month in earlier years (robust MAD z of log1p, limit 3.5: 114 rain flags instead of 1,352 + 125); guest YoY as |log(x/x₁₂)| > ln 2; COVID-window and COVID-base months scored separately `[OR]` README "DQ v2"
  - Honest gap: 0.893 is below our 0.93 target and the 0.90 warning floor (a warning, not a hard stop); the remaining flags are mostly foreign-arrival recovery jumps (103 in 2024 vs 2023). On total arrivals + rain alone it is 0.952 `[OR]` results/dq_report.json, README
- **Table (left)** `[OR]` results/dq_report.json ("scores", "accuracy"):

| Measure | v1 (old) | v2 (new) |
|---|---|---|
| Accuracy, all rows | 0.539 | – |
| Accuracy, normal months (scored) | – | 0.893 |
| Normal months, total arrivals + rain only | – | 0.952 |
| COVID months / COVID-base months (reported, not scored) | – | 0.059 / 0.308 |
| Overall DQ score (mean of 6 dimensions) | 0.923 | 0.982 |

- **Chart (right):** /workspace/tpr-test/experiments/online_replay/charts/dq_accuracy_buckets.png
- **Speaker notes:** We kept the data and fixed the check instead of "cleaning" real values. The v1 rule is still reproducible bit-for-bit for traceability. The new score misses our own 0.93 target, mainly because foreign arrivals were still recovering in 2024, and we report that rather than loosening the rule.

## Slide 5: Finding 1: the COVID base problem
- **Key message:** The best log-difference model failed in 2023 because its lag-12 base month was a COVID month.
- **Layout:** chart left (recovery index), table right.
- **Bullets:**
  - RF logdiff predicts log(y / y₋₁₂). In 2023 every y₋₁₂ is a COVID month, so the forecast overshoots
  - RF logdiff 2023: WAPE 25.77 % frozen, bias +16.3 %; seasonal naive 32.72 % `[BT]` (rf_logdiff 2023 frozen: wape 0.2577, bias 0.1625)
  - With monthly refit the 2023 overshoot is smaller but still the only large positive bias: +6.88 % (WAPE 19.97 %) vs RF level −3.36 % `[HYP]` H4 / `[BT]` (rf_logdiff 2023 monthly bias 0.0688). Always label frozen vs monthly
  - The same model in 2024: 6.31 % frozen `[BT]`. The model itself is not the problem
  - Recovery was uneven: 6 provinces never got back to a 3-month average ≥ 90 % of 2019 `[EDA2]` recovery_first90 "never": 6
- **Table:** RF logdiff vs RF level vs seasonal naive, frozen WAPE % for 2023 / 2024 / 2025: 25.77 / 6.31 / 4.79; 8.57 / 8.54 / 6.27; 32.72 / 11.15 / 5.48 `[BT]` (2023, 2024) and `[OT]` order 0 (2025)
- **Chart:** /workspace/data_review/recovery_index.png (province recovery vs 2019, national line in red).
- **Speaker notes:** This is the key diagnosis. Binary is_covid flags tell the model "this was COVID" but not how far each province had recovered. The RF level model avoids the problem but is worse in normal years, which motivates a mix.

## Slide 6: Finding 2: rain has little effect
- **Key message:** After controlling for province, season and recent arrivals, last month's rain explains essentially nothing; we keep it but report it honestly.
- **Layout:** chart left (H1_rainfall.png), compact table right.
- **Bullets:**
  - Raw correlation of log arrivals with same-month rain is 0.000; the apparent pattern is the season `[HYP]` H1 (raw correlation)
  - Lagged rain (forecast-valid), with province FE, month FE, log lag-1 and lag-12: t = −0.35, p = 0.73, added R² = 0.00005 pp; an IQR change (185.3 mm) ≈ −0.13 % arrivals (95 % CI −0.88 % to +0.62 %) `[HYP]` H1
  - Same-month rain (descriptive only, not known in advance): t = −4.75, p < 0.0001, IQR effect −1.66 %; wettest vs driest anomaly quintile ≈ −1.11 % `[HYP]` H1
  - Dropping rain from the final Champion changes WAPE by ≤ 0.12 points, in mixed directions (table) `[OR]` results/rain_ablation.csv
- **Table:** drop-rain ablation inside the final Champion (monthly WAPE %), 2023 / 2024* / 2025: with lagged rain 6.34 / 5.58 / 4.52; without rain 6.45 / 5.63 / 4.47; difference +0.12 / +0.05 / −0.05 pt `[OR]` results/rain_ablation.csv. History: RF logdiff monthly 19.97 / 5.94 / 4.83 vs no rain 18.52 / 5.99 / 4.73 `[TR]`.
- **Chart:** main: /workspace/hypotheses/charts/H1_rainfall.png. Second (small, right under the table): /workspace/tpr-test/experiments/online_replay/charts/rain_effect.png **cropped to the left 'Drop-rain ablation' panel and with the bottom caption line cropped off** (that caption quotes the no-fixed-effects ΔR² = 0.0006 and the ≈ 2 % quintile figure, which belong to the footnote spec, not the headline). Backup: /workspace/data_review/rain_signal.png.
- **Footnote (10 pt):** "A simpler YoY-growth regression without fixed effects gives the same conclusion: ΔR² ≈ 0.0006 (0.4295 → 0.4301), p = 0.058; wettest quintile ≈ 2 % below driest `[RAIN]`, `[OR]` results/rain_effect.csv. Headline numbers use the fixed-effects specification (lag-1 rain ΔR² = 0.00005 pp) `[HYP]` H1."
- **Speaker notes:** Same-month rain cannot be used to forecast because it is not known when the month begins, so its −1.7 % IQR effect is descriptive. Last month's rain, the only valid feature, is indistinguishable from zero. We kept rain because it is the project framing, but the evidence says it does not drive accuracy.

## Slide 7: Evaluation protocol
- **Key message:** Rolling origin with all settings chosen on 2024 only; 2023 and 2025 are held out.
- **Layout:** a diagram of three horizontal year bars (train | test) for 2023, 2024 and 2025, with a bullets box on the right.
- **Bullets:**
  - Frozen: fit on 2019-01..(Y−1)-12, forecast year Y. Monthly: refit on everything before t, predict t `[OT]` README Protocol
  - Every hyperparameter, alpha, threshold and eta is chosen on 2024; only that configuration is scored on 2023 and 2025 `[OT]` README
  - Metrics: WAPE, MAE, RMSE, MASE vs seasonal naive, bias, gap vs naive `[OT]` columns
  - Leakage tests in tests/test_online_trials_leakage.py and tests/test_backtest_leakage.py; the whole suite passed (145 passed, 3 skipped) on exp/online-model-trials @ 6cc2a63, run 2026-11-22; the new branch adds tests/test_online_replay_{leakage,selection,drift}.py
- **Chart:** diagram drawn in Canva.
- **Speaker notes:** 2023 is the stress test (COVID base) and 2025 is the most recent normal year. 2024 is optimistic for every model because it is the tuning year. It was also the year the original RF logdiff was chosen.

## Slide 8: Models tried and results
- **Key message:** Across all model families tried, only the online EWA mix stays near the best in all three years.
- **Layout:** full-width table. Highlight the EWA row and the RF logdiff baseline row.
- **Table (WAPE %, frozen / monthly, configuration chosen on 2024)** `[OT]` (also README "Results"):

| Model | 2023 | 2024* | 2025 |
|---|---|---|---|
| Seasonal naive | 32.72 | 11.15 | 5.48 |
| RF logdiff (old Champion) | 25.77 / 19.97 | 6.31 / 5.94 | 4.79 / 4.83 |
| RF level | 8.57 / 7.49 | 8.54 / 7.00 | 6.27 / 5.96 |
| Blend α = 0.80 | 20.35 / 15.98 | 5.90 / 5.60 | 4.47 / 4.52 |
| Switch rule (COVID base or \|yoy\| > 0.3 → level) | 8.57 / 7.49 | 6.28 / 5.98 | 4.74 / 4.77 |
| **EWA logdiff + level, eta 10 (monthly)** | – / **7.13** | – / 5.64 | – / 4.55 |
| LightGBM (31 leaves, lr 0.05) | 19.53 / 14.03 | 7.77 / 6.95 | 5.73 / 5.39 |
| SNARIMAX (river) | 34.80 / 26.39 | 9.95 / 8.37 | 5.15 / 4.70 |
| Holt-Winters (river) | 27.37 / 17.28 | 17.90 / 15.10 | 14.87 / 14.34 |
| river BayesianLinearRegression | 27.30 / 9.63 | 29.18 / 10.06 | 6.27 / 5.67 |
| Ridge expanding | 30.39 / 19.21 | 16.84 / 15.43 | 15.22 / 14.12 |

- **Bullets (under the table, max 3):** EWA weights move to RF level within 3 months in 2023 (0.50 → 0.00 on logdiff) and lean to logdiff in 2024/2025 (≈ 0.70-0.75) `[OT]` README · Linear models needed log-scale features and label clipping to avoid divergence `[OT]` README · The 2025 gain of EWA over RF logdiff (4.55 vs 4.83) is borderline: DM-HLN p = 0.049 one-sided, 0.097 two-sided `[HYP]` H3.
- **Speaker notes:** Tree models on a log-difference target beat per-province time-series and linear models. The EWA mix is a simple online learner: two experts and two weights updated monthly by their WAPE. The full grids and the MAE/RMSE/bias columns are in results.csv.

## Slide 9: Streaming trees: Adaptive Random Forest (ARF) experiment
- **Key message:** Incremental streaming trees react to drift but do not adapt usefully on small monthly data; monthly RF refit plus the EWA mix is better.
- **Layout:** chart left (yearly WAPE bars), small table right, monthly chart as a thin strip along the bottom (or move it to backup if space is tight).
- **Bullets:**
  - Setup: river ARFRegressor (log-diff target, 89 features), warm start on 2019 only, then prequential (predict all 77, then learn) month by month 2020-01..2025-12; best config chosen on 2024: n_models = 10, grace_period = 50 `[ARF]` Protocol
  - Tree resets (ADWIN per tree), chosen config: 25 / 21 / 13 / 4 / 1 / 1 in 2020-2025 (65 total), so it reacts to the COVID shock `[ARF]` "Drift detections / tree resets"
  - Slow recovery: monthly WAPE first falls below 15 % in 2023-08, 41 months after 2020-03; it beats naive only in 2020 and 2023 `[ARF]` "Recovery"
  - Caveats: 10-30 trees vs 300 in the batch RF; one shared national model; 8 runs (4 configs × 2 targets), seed 42 only; different training protocol from the monthly refit `[ARF]` Caveats
- **Table (yearly WAPE %)** `[ARF]` "Yearly WAPE" (ARF, naive, RF logdiff rows); Champion row `[OR]` results/results.csv. Note: the ARF report itself compares against the Champion baseline 6.50 / 5.59 / 4.54 (history); the conclusion is unchanged:

| Model | 2020 | 2021 | 2022 | 2023 | 2024* | 2025 |
|---|---|---|---|---|---|---|
| ARF logdiff n=10, grace=50 | 48.06 | 148.26 | 86.60 | 22.24 | 13.44 | 10.80 |
| Seasonal naive | 127.07 | 137.45 | 66.46 | 32.72 | 11.15 | 5.48 |
| RF logdiff monthly | – | – | – | 19.97 | 5.94 | 4.83 |
| Final Champion (EWA, C0, eta 5) | – | – | – | 6.34 | 5.58 | 4.52 |

- **Charts:** /workspace/arf_experiment/charts/yearly_wape_comparison.png (main); /workspace/arf_experiment/charts/monthly_wape_arf_vs_naive.png (bottom strip or backup; its monthly axis is capped at 250 %, and 2020-04 and 2021-05 are annotated above the cap).
- **Speaker notes:** We tested the "true" online option: a streaming forest that never refits and only resets trees when its drift detectors fire. The detectors fire when they should (most resets in 2020-2022), but the model loses to seasonal naive in 2021, 2022, 2024 and 2025, and took 41 months to get back under 15 %. With only 77 rows per month, refitting a batch forest each month is cheap and works better. The ARF used far fewer trees, so this is a fair test of the streaming approach on this data, not of ARF in general.

## Slide 10: The new feature: exp_lr_recent
- **Key message:** One leak-free "seasonally adjusted last month" feature cuts 2023 error from 25.8 % to 9.6 % without hurting 2025.
- **Layout:** formula box at the top; table bottom-left; chart bottom-right.
- **Content:**
  - Formula: exp_lr_recent = log(lag1 · S[m] / S[m−1]) − log(lag12); the 3-month version uses lag1+lag2+lag3. S = the province's monthly share over the last two complete normal years before the target year `[TR]` / /workspace/data_review/REPORT.md
  - Meaning: "what last month implies for this month, relative to the COVID-affected base"
  - The same quantity with no ML at all ("SA-persistence naive") already scores 9.56 / 6.70 / 5.20 frozen `[TR]`
- **Table (frozen WAPE %, 2023 / 2024* / 2025)** `[TR]`: RF logdiff baseline 25.77 / 6.31 / 4.79 · recovery index (vs 2018-19) 14.53 / 6.19 / 5.27 · +exp_lr_recent 10.21 / 6.52 / 4.69 · +exp_lr_recent (1m+3m) 9.60 / 6.64 / 4.71 · clip label ±ln2 16.88 / 6.21 / 4.72
- **Chart:** /workspace/data_review/transform_results.png
- **Footnote:** Monthly refit: 8.55 vs 19.97 % in 2023 (DM-HLN p = 0.011), 5.94 vs 5.94 % in 2024, 4.66 vs 4.83 % in 2025 (p = 0.18, not significant) `[HYP]` H6.
- **Speaker notes:** Note the honest trade-off: on 2024 alone the feature is slightly worse (6.64 vs 6.31 frozen), so a strict 2024-only rule would not have picked it. It was adopted by user decision because of the 2023 diagnosis, and we flag this as a protocol limitation.

## Slide 11: Final Champion and the two A/B tests
- **Key message:** Final Champion = EWA of RF logdiff (+ exp_lr_recent) and RF level, with binary COVID flags, 2 experts and eta 5: 6.34 / 5.58 / 4.52 monthly WAPE. It beats naive significantly in 2023 and 2025; the gain over RF logdiff is significant only in 2023.
- **Layout:** top-left: headline chart; top-right: H3b box. Bottom-left: COVID with/without table. Bottom-right: 2 vs 3 experts table. (If too dense, move the two A/B tables to a backup slide 11b and keep only their one-line verdicts here.)
- **Headline (monthly WAPE %, 2023 / 2024* / 2025)** `[OR]` results/results.csv (README "Headline"):

| Model | 2023 | 2024* | 2025 |
|---|---|---|---|
| Seasonal naive | 32.72 | 11.15 | 5.48 |
| Old Champion: RF logdiff, monthly | 19.97 | 5.94 | 4.83 |
| Old EWA (logdiff, level), eta 10 (history) | 7.13 | 5.64 | 4.55 |
| Champion baseline, eta 10 (history) | 6.50 | 5.59 | 4.54 |
| **Final Champion: C0, 2 experts, eta 5** | **6.34** | **5.58** | **4.52** |

  - Eta re-selected on 2024: 5 (5.5779) beat 10 (5.5909) `[OR]` config.json selection.covid_ab.grid.C0.eta_grid. Beats naive in 75 / 62 / 53 of 77 provinces `[OR]` results/ewa_ab.csv (provinces_beating_naive)
- **COVID with/without (monthly WAPE %)** `[OR]` results/covid_with_without.csv, covid_ab.csv:

| Variant | eta | 2023 | 2024* | 2025 | Role |
|---|---|---|---|---|---|
| **C0 binary is_covid flags** | 5 | **6.34** | 5.58 | **4.52** | candidate, selected |
| C3 recovery level (vs last two normal years) + lagged event flag | 2 | 6.52 | 6.90 | 4.80 | candidate |
| C4 no COVID information | 5 | 6.67 | **5.32** | 4.75 | "without" row, **not a candidate** |

  - **Why C0 and not C4 (state this honestly on the slide, small text under the table):** "C4 has the best 2024 score, but it was defined in advance as a report-only 'without' row for this comparison, not a candidate (config.json: covid_candidates = [C0, C3], covid_report_only = [C4]). Among the candidates, C0 wins 2024 by 1.32 pt. Had C4 been eligible, the 2024-only rule would have picked it; it is worse than C0 on both held-out years (6.67 vs 6.34; 4.75 vs 4.52)." `[OR]` config.json, README "COVID A/B"
- **EWA 2 vs 3 experts (monthly WAPE %)** `[OR]` results/ewa_ab.csv:

| Experts | eta | 2023 | 2024* | 2025 |
|---|---|---|---|---|
| **E2: logdiff + level (kept)** | 5 | **6.34** | **5.58** | 4.52 |
| E3: + no-ML seasonal last-month forecast | 0.5 | 6.92 | 5.60 | **4.46** |

  - Rule: keep E3 only if ≥ 0.02 pt better on 2024; it was 0.025 pt worse, so E2 was kept. E3 would have won 2025 by 0.06 pt but loses 2023 by 0.58 pt; the choice does not switch on held-out years `[OR]` config.json selection.ewa_ab, README
- **H3b box (DM-HLN, one-sided; tested on the FINAL Champion C0, 2 experts, eta 5: 6.34 / 5.58 / 4.52)** `[HYP]` H3b: vs RF logdiff 2023 p = 0.005 (significant), 2024 p = 0.12, 2025 p = 0.15 (not significant); vs seasonal naive 2023 p < 0.0001, 2024 p < 0.0001, 2025 p = 0.003.
- **Charts:** /workspace/tpr-test/experiments/online_replay/charts/headline_wape.png (top-left; or rebuild natively from the table). Backup / 11b: /workspace/tpr-test/experiments/online_replay/charts/covid_ab.png, /workspace/tpr-test/experiments/online_replay/charts/ewa_ab.png, /workspace/tpr-test/experiments/online_replay/charts/ewa_weights_2025.png, /workspace/hypotheses/charts/H3b_champion.png.
- **Speaker notes:** All three choices (COVID variant, eta, number of experts) were made on 2024 and frozen before 2023 and 2025 were scored. Be upfront about C4: the plan treated "no COVID information" as the comparison row, and it happens to have the best 2024 score; we report that rather than hide it, and note it is worse on both test years. In 2025 the Champion is significantly better than naive but not significantly better than plain RF logdiff, so the main value of the mix is robustness in an abnormal year like 2023.

## Slide 12: 2025 online replay with drift detection
- **Key message:** In 2025 the monitoring watched but did not fire: PSI flagged rain drift in 7 months, ADWIN never cut, and no retrain was triggered. That is the expected behaviour in a stable year.
- **Layout:** left 35 %: loop diagram (DQ gate → predict t → observe y_t → EWA update → PSI (Seasonal-2Y) + ADWIN (monthly WAPE) → trigger? → prepare_retrain → challengers → preset D promote → 2-month rollback check). Right 65 %: replay_2025_drift.png.
- **Bullets:**
  - Trigger = PSI AND ADWIN; PSI alone is a logged "watch". ADWIN: delta 0.002 on monthly WAPE, seeded with 24 OOS months (2023-2024), direction "up" only; preset D: margin 0.005, confirm 2; rollback after 2 months `[OR]` config.json "drift"
  - PSI watches: 2025-02, 03, 05, 06, 07, 08 and 12, almost all on rain features (rain_roll3 or confirmed rain_lag_1); guest-lag PSI stayed < 0.11 all year `[OR]` results/replay_2025_summary.json, README
  - ADWIN cuts: none; prepare_retrain: none; promotions 0; rollbacks 0. ADWIN window mean fell from 5.9 % to 5.5 % `[OR]` results/replay_2025_summary.json, README
  - Served 2025 WAPE 4.52 % = the offline Champion number, so the loop does not change the model. EWA weight on logdiff rose from 0.50 to 0.66 over the year `[OR]` results/replay_2025_summary.json, results/replay_2025_log.csv (w_logdiff)
- **Chart:** /workspace/tpr-test/experiments/online_replay/charts/replay_2025_drift.png (panels: served vs naive WAPE with ADWIN mean; PSI per feature with 0.15 alarm line; action markers).
- **Optional mini-table (presenter backup)** from `[OR]` results/replay_2025_log.csv: month | served WAPE | PSI alarm features | action, e.g. 2025-06: 6.78 % | rain_lag_1, rain_roll3 | watch.
- **Speaker notes:** No drift in a stable year is the correct outcome: we did not retrain for nothing. The PSI watches were all rain, consistent with rain varying a lot but not moving arrivals. One honest limitation: with this ADWIN implementation (minimum window 32, 16 points per side) and monthly WAPE input, a cut needs a large shift sustained over many months, so the first month that could ever cut is August.

## Slide 13: 2023 drift demo  **[CHART = PLACEHOLDER – to be updated]**
- **Key message:** Against a COVID-era reference, PSI detects the 2023 feature shift every month; the error-based ADWIN trigger does not fire, because errors fell after COVID.
- **Layout:** full-width chart with a red corner tag "PLACEHOLDER – to be updated (2020-2023 replay with a normal-period seed in progress)"; three bullets underneath.
- **Bullets** (current committed run; may change with Webber's redo):
  - Track A (old RF logdiff, 19.97 %) and Track B (final Champion, 6.34 %): PSI watch in 12 of 12 months (guest-lag PSI ≈ 0.4-0.7 from February); ADWIN no cut at delta 0.002; no prepare_retrain `[OR]` results/replay_2023_demo_summary.json, README "2023 drift demo"
  - Why: the ADWIN seed (2021-2022 OOS WAPE) has mean 0.83, peak 4.38 in 2021-07, so 2023 is an improvement, not a degradation `[OR]` README
  - Sensitivity only (not a config, not tuned): delta 0.05 cuts in 2023-10, a downward cut (1.02 → 0.33) that "direction = up" correctly ignores `[OR]` README
- **Chart:** /workspace/tpr-test/experiments/online_replay/charts/replay_2023_drift.png (**placeholder – to be updated**; Webber is replacing it with a 2020-2023 replay and a normal-period seed. When the new PNG lands at the same path, swap it in and update the three bullets from the new summary JSON only).
- **Speaker notes:** This shows the monitoring behaving as designed, including its limits. PSI reacts to the regime change immediately; the error detector stays quiet because the model's errors went down. A better demo, which Webber is running now, seeds ADWIN with a normal period so the COVID shock shows up as a real error increase.

## Slide 14: Hypotheses and statistical tests
- **Key message:** Six pre-stated hypotheses plus a champion test (H3b), tested with Diebold-Mariano (HLN) and fixed-effects regressions: rain does not help forecasting; the COVID base, the EWA mix and exp_lr_recent are supported in 2023; 2025 gains over RF logdiff are small and not significant; the Champion beats naive in both test years.
- **Layout:** full-width results table (top 70 %); a row of six small thumbnails (H1..H6 charts) underneath; caveats strip at the bottom. If too dense, split into 14a (table) and 14b (thumbnails + caveats) and count it as one slide in the talk.
- **Table** `[HYP]` "Summary table" (also stats/summary_table.csv). One-sided p for forecast comparisons; WAPE = monthly-refit:

| # | Hypothesis (H1 = alternative) | Test | Key statistic | p | Verdict |
|---|---|---|---|---|---|
| H1 | Last month's rain affects arrivals (after province FE, month FE, lag-1/lag-12) | Cluster-robust t, 2019/2024/2025 | lag-1 rain t = −0.35, IQR effect −0.13 %; same-month rain t = −4.75, IQR −1.66 % (descriptive) | 0.73 (lag-1); < 0.0001 (same-month) | Not supported for forecasting |
| H2 | RF logdiff beats seasonal naive | DM-HLN, t(11) | 2023: 19.97 vs 32.72 %, t = −3.54; 2025: 4.83 vs 5.48 %, t = −1.63 | 0.002 (2023); 0.066 (2025) | Supported 2023; not significant 2025 |
| H3 | Online EWA mix (original) beats RF logdiff | DM-HLN, t(11) | 2023: 7.13 vs 19.97 %, t = −2.77; 2025: 4.55 vs 4.83 %, t = −1.81 | 0.009 (2023); 0.049 (2025; two-sided 0.097) | Supported 2023; borderline 2025 |
| H3b | Final Champion EWA (C0, 2 experts, eta 5) beats RF logdiff and naive | DM-HLN, t(11) | vs RF logdiff: 2023 6.34 vs 19.97 %, t = −3.13; 2025 4.52 vs 4.83 %, t = −1.10. vs naive: 2023 6.34 vs 32.72 %, t = −12.22; 2025 4.52 vs 5.48 %, t = −3.47 | vs RF logdiff 0.005 (2023), 0.15 (2025); vs naive < 0.0001 (2023), 0.003 (2025) | Supported 2023 (both); 2025: beats naive, not RF logdiff |
| H4 | COVID lag-12 base caused the 2023 failure | Diff-in-diff of AE(logdiff − level), 2023 vs 2024-25, month-clustered | t = 3.06; 2023 bias logdiff +6.88 % vs level −3.36 %; predicted YoY 58.2 % vs actual 48.0 % | 0.001 | Supported (association + mechanism) |
| H5 | Seasonality dominates rain | Wet vs dry after province + year FE; incremental R² | wet season −10.3 % (t = −10.18); ΔR² month 0.33 pp vs same-month rain 0.18 pp vs lag-1 rain 0.09 pp | < 0.0001 | Supported |
| H6 | exp_lr_recent improves RF logdiff | DM-HLN, t(11); nested models | 2023: 8.55 vs 19.97 %, t = −2.67; 2025: 4.66 vs 4.83 %, t = −0.97 | 0.011 (2023); 0.18 (2025) | Supported 2023; not significant 2025 |
| H7 | Drift monitoring stays quiet in a stable year (2025) | 2025 replay log (PSI + ADWIN, preset D) | PSI watches in 7 months (rain); 0 ADWIN cuts; 0 prepare_retrain; served 4.52 % `[OR]` results/replay_2025_summary.json | – (behavioural) | Supported for 2025. The "fires when the regime breaks" half is not yet shown (slide 13, redo in progress) |

- **Charts (thumbnails):** /workspace/hypotheses/charts/H1_rainfall.png, /workspace/hypotheses/charts/H2_rf_vs_naive.png, /workspace/hypotheses/charts/H3_ewa_vs_logdiff.png, /workspace/hypotheses/charts/H3b_champion.png, /workspace/hypotheses/charts/H4_covid_base.png, /workspace/hypotheses/charts/H5_seasonality.png, /workspace/hypotheses/charts/H6_exp_lr_recent.png
- **Caveats strip (max 4)** `[HYP]` Caveats 1, 2, 5, 6:
  - Only 12 monthly loss differentials per year, so the DM tests have low power
  - 2023 is the only COVID-base year; H3, H4 and H6 rest on it
  - No multiple-testing correction across 6 hypotheses + H3b and years
  - EWA and exp_lr_recent were motivated by looking at 2023/2025, so they are not fully held-out
- **Speaker notes:** The rain hypothesis that started the project is the one the data does not support for forecasting: last month's rain is indistinguishable from zero once season and recent arrivals are known. The structural fixes are significant in 2023 but only borderline or not significant in 2025, where all models are already close to naive. H3b tests the final Champion (C0, 2 experts, eta 5, 6.34 / 5.58 / 4.52) directly: it clearly beats naive in every year, and beats RF logdiff significantly only in 2023.

## Slide 15: Limitations
- **Key message:** The results are small-sample and partly tuned on a favourable year.
- **Layout:** single column, 4 bullets plus a small "what would change our mind" box.
- **Bullets:**
  - 2023 is one abnormal year and the only test of a "COVID base". 2024 has no COVID rows, so it cannot tune COVID handling `[OT]` README Caveats
  - 2025 differences are 0.05-0.32 points; DM tests give p = 0.049 (EWA vs RF logdiff) to 0.18 (exp_lr_recent), with only 12 months per test and no multiplicity correction `[HYP]` H3, H6, Caveats 1 and 5
  - exp_lr_recent was adopted by decision, not by the 2024 rule (6.64 vs 6.31 on 2024 frozen) `[TR]`
  - Selection caveats: C4 (no COVID info) had the best 2024 score but was report-only by design; DQ normal-bucket accuracy 0.893 misses the 0.93 target; event dates in events.json are unconfirmed `[OR]` config.json, README
  - Drift: with monthly WAPE input this ADWIN effectively cannot fire within a year (min window 32, 16 per side); the 2023 demo shows PSI firing but no ADWIN cut `[OR]` README
- **Speaker notes:** We would rather show modest, defensible gains than a cherry-picked number. Each limitation maps to a concrete next step on the next slide.

## Slide 16: Next steps
- **Key message:** Make the gains statistically defensible and the pipeline production-ready.
- **Layout:** two columns: "Modelling" | "Data engineering".
- **Bullets:**
  - Modelling: a block bootstrap for 2025 differences; carry EWA weights across years; seeds 1-3 robustness (not run, time)
  - Drift: ADWIN on province-level errors or river's ADWIN, and a normal-period seed (in progress for the 2020-2023 demo); a recovery bucket for DQ accuracy
  - Modelling: a richer recovery baseline once more normal years exist (2026+)
  - Data engineering: scheduled monthly ingestion (MOTS + HII) with the validation/DQ gate as a hard stop and alerting on PSI/ADWIN events
  - Data engineering: model registry and versioned predictions (the replay already logs registry versions v001… `[PA]`)
- **Speaker notes:** Close by restating the three take-aways: rain has a negligible effect on forecasting, the COVID base was the real problem, and an online mix with monitored drift is the robust choice. Invite questions on the protocol and the leakage controls.

## Appendix A1 (optional): Leakage controls
- **Key message:** Every feature and every online step is tested against reading the future.
- **Layout:** table with columns: component | rule | test file.
- **Content:** lags and rolling rain only through t−1 `[PA]` config.json "rain" · seasonal profile S from complete normal years before the target year `[TR]`/REPORT.md · EWA weight at t uses only losses before t (online_trials/models.py `ewa` docstring) · tests: tests/test_online_trials_leakage.py, tests/test_backtest_leakage.py, tests/test_feature_leakage.py; new in exp/online-replay-2025: tests/test_online_replay_leakage.py, tests/test_online_replay_selection.py (selection refuses non-2024 frames; scorer refuses without the selected_on: 2024 stamp), tests/test_online_replay_drift.py `[OR]` README "Protocol and leakage".
- **Speaker notes:** For a data engineering audience this is often the first question. Each rule has an automated test that blanks future labels and checks that forecasts do not change.

---

## Consistency notes and verification (final)
- **Albert's numbers vs files: all match.** Final Champion 6.34 / 5.58 / 4.52; C3 6.52 / 6.90 / 4.80; no COVID info (C4) 6.67 / 5.32 / 4.75; 3 experts 6.92 / 5.60 / 4.46 (`[OR]` results/covid_with_without.csv, ewa_ab.csv, results.csv). DQ v1 0.539; v2 normal 0.893 (0.952 totals + rain); overall 0.923 → 0.982 (`[OR]` results/dq_report.json). 2025 drift: PSI watches 2025-02, 03, 05, 06, 07, 08, 12; no ADWIN cut; no retrain (`[OR]` results/replay_2025_summary.json). H3b (re-run on final eta-5 Champion) p-values 0.005 / 0.15 / < 0.0001 / 0.003 match `[HYP]`.
- **C0 vs C4:** C0 was chosen because C4 was pre-registered as report-only (config.json covid_candidates = [C0, C3], covid_report_only = [C4]), not because of a tie rule, another metric, or a user decision. My replay plan defined C4 as the "without" row but also listed it in a tie-break order, so the eligibility was a design reading (Webber's, consistent with the user's request for "binary flags vs recovery level" plus a "with/without table"). Had C4 been a candidate, a strict 2024 rule would have selected it (5.32 vs 5.58, beyond the 0.05 pt tie threshold). Slide 11 says this verbatim.
- **E3:** best in 2025 (4.46) but 0.025 pt worse on 2024, failing the ≥ 0.02 pt gain rule, so correctly not kept.
- **H3b was re-run on the final eta-5 Champion (6.34 / 5.58 / 4.52)** by Albert; the old eta-10 caveat and the 'retest' next step are removed (2026-11-22 20:05).
- **Rain R²:** the deck uses lag-1 rain ΔR² = 0.00005 pp (fixed-effects spec, `[HYP]`) everywhere in headlines. Webber's rain_effect.csv / README / rain_effect.png caption still quote the no-FE YoY spec (ΔR² 0.0006, ≈ 2.0 % quintile gap). These are a different specification, not an error; they appear only in the slide 6 footnote, and rain_effect.png is cropped to hide its caption.
- **COVID-base bias:** +16.3 % frozen `[BT]`, +6.88 % monthly `[HYP]` H4. Labelled on slide 5.
- **exp_lr_recent:** frozen 25.77 → 9.60 (slide 10) vs monthly 19.97 → 8.55 (H6). Labelled.
- **ARF report** compares with the baseline 6.50 / 5.59 / 4.54; slide 9 shows the final Champion and notes this.
- **PSI threshold:** 0.15 in the replay (not 0.2 as an earlier draft implied).
- Earlier "145 passed, 3 skipped" (slide 7) was measured on exp/online-model-trials @ 6cc2a63; Webber's README does not record a test count for 54eeb6d, so the slide must keep the "@ 6cc2a63" label.

## Assets Canvo needs (absolute paths)
Existing charts:
1. /workspace/data_review/recovery_index.png (slide 5)
2. /workspace/hypotheses/charts/H1_rainfall.png (slide 6 main; slide 14 thumbnail)
3. /workspace/tpr-test/experiments/online_replay/charts/rain_effect.png (slide 6, crop to left panel, remove caption)
4. /workspace/tpr-test/experiments/online_replay/charts/dq_accuracy_buckets.png (slide 4)
5. /workspace/arf_experiment/charts/yearly_wape_comparison.png (slide 9 main)
6. /workspace/arf_experiment/charts/monthly_wape_arf_vs_naive.png (slide 9 strip or backup)
7. /workspace/data_review/transform_results.png (slide 10)
8. /workspace/tpr-test/experiments/online_replay/charts/headline_wape.png (slide 11)
9. /workspace/tpr-test/experiments/online_replay/charts/covid_ab.png (slide 11 / 11b)
10. /workspace/tpr-test/experiments/online_replay/charts/ewa_ab.png (slide 11 / 11b)
11. /workspace/tpr-test/experiments/online_replay/charts/ewa_weights_2025.png (slide 11b, optional)
12. /workspace/hypotheses/charts/H3b_champion.png (slide 11b and slide 14 thumbnail)
13. /workspace/tpr-test/experiments/online_replay/charts/replay_2025_drift.png (slide 12)
14. /workspace/tpr-test/experiments/online_replay/charts/replay_2023_drift.png (slide 13, **PLACEHOLDER – to be updated**)
15. /workspace/hypotheses/charts/H2_rf_vs_naive.png, /workspace/hypotheses/charts/H3_ewa_vs_logdiff.png, /workspace/hypotheses/charts/H4_covid_base.png, /workspace/hypotheses/charts/H5_seasonality.png, /workspace/hypotheses/charts/H6_exp_lr_recent.png (slide 14 thumbnails)
Backup / Q&A only: /workspace/data_review/rain_signal.png, /workspace/data_review/error_share_2023.png, /workspace/data_review/error_share_2025.png, /workspace/tpr-test/experiments/online_replay/charts/ewa_weights_2023.png, /workspace/tpr-test/experiments/online_replay/charts/ewa_weights_2024.png

Build natively in Canva from numbers in this file: slide 2 timeline, slide 3 pipeline diagram, slide 4 before/after table, slide 7 protocol diagram, slide 12 loop diagram, all tables.
Note for Canvo: the box paths are on the shared box; if Canva needs uploads, upload these PNG files from those paths.

## Remaining dependency on Webber
- Slide 13 only: replace replay_2023_drift.png (same path) and its three bullets when Webber's 2020-2023 replay with a normal-period seed is committed; until then keep the red "PLACEHOLDER – to be updated" tag. If it does not land before the presentation, present the current chart with the tag and say so.
- Everything else is final.
