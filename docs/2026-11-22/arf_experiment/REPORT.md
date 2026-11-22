# Adaptive Random Forest (prequential) experiment

Work lives only in `/workspace/arf_experiment/`. The repo at `/workspace/tpr-test` was not modified, committed, or pushed.

**Question:** If we warm-start river’s `ARFRegressor` on 2019 and then do true incremental, predict-then-learn updates through 2025, does the model adapt well?

**Short verdict:** No. The drift detectors do fire during COVID and the 2022 reopening, and the log-diff ARF eventually comes down from the 2021–2022 blow-up. It is not competitive. The 2024-chosen config beats seasonal naive only in 2020 and 2023, loses to naive in 2021, 2022, 2024, and 2025, and on 2023–2025 it is worse than the current monthly RF logdiff (19.97 / 5.94 / 4.83) and far worse than the online EWA mix (6.50 / 5.59 / 4.54). Incremental Hoeffding-tree resets are not a substitute for a monthly batch refit, or for the EWA mix, on this data.

## Protocol

- **Data:** `data/model/province_month_unified.parquet` (77 provinces, 2018–2025). 2018 is lag history only.
- **Warm-start:** learn on 2019 only (924 rows). No 2019 month was scored.
- **Stream:** 2020-01 to 2025-12, month by month, all 77 provinces. Predict all 77 first, then learn all 77, so a shared model never sees a same-month label before predicting.
- **No leakage:** features for month *t* use only earlier months (same builders as the RF logdiff ext set). Within-month predictions are issued before any update. 2019 medians are the only imputation values, and they are never recomputed on later years.
- **Features (89):** `guest_lag_1`, `guest_lag_12`, `rain_lag_1`, `rain_roll3`, `is_covid`, `is_covid_lag12`, `guest_lag_2`, `guest_lag_3`, `yoy_growth`, `rain_anomaly` (baseline capped at *t*−1), `month_sin`, `month_cos`, plus 77 province one-hots. `StandardScaler` in a river pipeline, updated only on `learn_one`.
- **Targets:**
  - **(a) logdiff:** label `log(y) − log(lag12)`, prediction `lag12 * exp(d)`. If `lag12 <= 0`, prediction is 0 (same reconstruction as the batch RF). Learning is **skipped** when `y <= 0` or `lag12 <= 0`. That happened on 54 stream rows (27 months with `y = 0`, 27 later months whose lag-12 was those zeros: 2021-04, 2021-05, 2022-01). No overflows; fallback count was 0.
  - **(b) log1p level:** label `log1p(y)`, prediction `expm1(d)`. No rows skipped.
- **Configs:** `n_models` in {10, 30}, `grace_period` in {50, 20}, default ADWIN drift detector, seed 42. Nothing was chosen on 2025. The reported champion is the config with the lowest **2024** WAPE: `arf_logdiff_n10_g50`.
- **Software:** `/workspace/tpr-test/.venv/bin/python`, river 0.27.0. Total wall time for all eight runs: **212 seconds** (~3.5 minutes).

WAPE is `sum(|y − pred|) / sum(|y|)` and is reported in percent. Seasonal naive is `guest_lag_12`. The 2023–2025 naive scores from this run (32.72 / 11.15 / 5.48) match the published references exactly.

## Yearly WAPE (%)

| Model | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|
| Seasonal naive | 127.07 | 137.45 | 66.46 | 32.72 | 11.15 | 5.48 |
| RF logdiff monthly (reference) | — | — | — | 19.97 | 5.94 | 4.83 |
| Online EWA mix (reference) | — | — | — | 6.50 | 5.59 | 4.54 |
| **ARF logdiff n=10, grace=50 (best on 2024)** | **48.06** | **148.26** | **86.60** | **22.24** | **13.44** | **10.80** |
| ARF logdiff n=10, grace=20 | 46.37 | 173.38 | 91.65 | 24.74 | 17.83 | 14.14 |
| ARF logdiff n=30, grace=50 | 47.76 | 126.57 | 88.36 | 19.73 | 14.41 | 11.31 |
| ARF logdiff n=30, grace=20 | 47.95 | 177.60 | 93.67 | 21.70 | 14.45 | 11.35 |
| ARF log1p n=30, grace=50 (best level on 2024) | 67.45 | 76.39 | 52.32 | 46.90 | 41.64 | 36.49 |
| ARF log1p n=30, grace=20 | 69.29 | 76.87 | 54.39 | 48.61 | 43.13 | 38.32 |
| ARF log1p n=10, grace=50 | 61.32 | 81.26 | 56.07 | 49.07 | 43.30 | 39.54 |
| ARF log1p n=10, grace=20 | 62.81 | 77.37 | 61.97 | 53.27 | 45.10 | 40.85 |

The n=30, grace=50 logdiff model is slightly better than the chosen config in 2021 and 2023 (19.73, almost matching RF logdiff’s 19.97) but worse on the 2024 selection year, so it was not chosen.

## Drift detections / tree resets

These are `ARFRegressor.n_drifts_detected()` increments (default ADWIN, one detector per base tree). A detection resets that tree. Warnings are `n_warnings_detected()` (background-tree starts).

Tree resets by year:

| Config | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| ARF logdiff n=10, grace=50 | 25 | 21 | 13 | 4 | 1 | 1 | 65 |
| ARF logdiff n=10, grace=20 | 21 | 24 | 13 | 0 | 2 | 0 | 60 |
| ARF logdiff n=30, grace=50 | 51 | 62 | 37 | 4 | 3 | 1 | 158 |
| ARF logdiff n=30, grace=20 | 55 | 60 | 38 | 4 | 3 | 4 | 164 |
| ARF log1p n=10, grace=50 | 21 | 10 | 2 | 1 | 0 | 0 | 34 |
| ARF log1p n=10, grace=20 | 16 | 11 | 1 | 2 | 0 | 0 | 30 |
| ARF log1p n=30, grace=50 | 51 | 33 | 8 | 5 | 0 | 1 | 98 |
| ARF log1p n=30, grace=20 | 46 | 29 | 8 | 5 | 2 | 0 | 90 |

The detectors behave as one would hope: most resets fall in 2020–2022, and 2024–2025 are almost quiet. That is evidence of *reaction*, not of *useful adaptation*. Resets in 2022 did not stop the logdiff blow-up in 2022-09 to 2022-12, and once things calmed down the model still lost to lag-12.

## Recovery after COVID start (2020-03) and reopening (2022)

Figures below are for the 2024-chosen model, `arf_logdiff_n10_g50`. Monthly WAPE is unstable when national arrivals are near zero, so 2020-04 and 2021-05 should be read with care.

**COVID start**

- 2020-01 (pre-shock): ARF 4.60%, naive 4.48%. The 2019 warm-start was enough for a normal month.
- 2020-02: ARF 30.01%, naive 32.10%. Already disrupted.
- 2020-03: ARF 159.71%, naive 208.25%. Crash begins; ARF beats naive but both fail.
- 2020-04: ARF 1,593.77%, naive 4,725.97%. Many provinces recorded zero arrivals, so the WAPE denominator collapses. ARF is still better than naive that month.
- May–Dec 2020: ARF monthly WAPE falls from ~89% to the 28–44% range and stays below naive. 25 tree resets in 2020.
- It does **not** return to a usable level quickly. Monthly WAPE first falls below 15%, and first falls back under the Jan–Feb 2020 mean (17.3%), in **2023-08 — 41 months after 2020-03**. It never stably returns to the 4.60% of January 2020.

**2021 trough:** yearly WAPE 148.26%, worse than naive (137.45%). The worst month is 2021-05, ARF 1,944.42% vs naive 67.40% — a real model failure, not just a small denominator (naive is well-behaved that month). 21 resets in 2021 did not prevent it.

**Reopening in 2022**

- Jan–Aug 2022: ARF is often better than naive (except 2022-01), but monthly WAPE stays in the 38–72% range.
- Sep–Dec 2022: ARF blows up again (117.52%, 237.07%, 121.64%, 91.62%) while naive is 37–85%. This is the familiar logdiff / COVID-base problem: lag-12 is still a 2021 trough, while 2022 arrivals have recovered, so a bad ratio is scaled by a bad base. 13 tree resets in 2022 did not stop it.
- 2023 then improves month by month, from 50.35% in January to 14–18% by August–December. Yearly 22.24% beats naive (32.72%) but loses to monthly RF logdiff (19.97) and to the EWA mix (6.50).
- 2024–2025: only one reset per year. Monthly WAPE hovers between about 6% and 21% and is consistently worse than naive (11.15% and 5.48% yearly) and more than twice the current models.

The level-target ARF (best: n=30, grace=50) is smoother during reopening — it does not explode in late 2022 — but it is unusable as a forecast: 2020-01 is already 48.31%, and by 2025 it is still 36.49%. After a 2019-only warm start, incremental Hoeffding trees never lock onto province scale on the log1p target. Logdiff looks better early only because lag-12 carries the scale.

## Charts

- Monthly WAPE, chosen ARF vs naive, COVID window shaded, drift counts underneath: `/workspace/arf_experiment/charts/monthly_wape_arf_vs_naive.png`
- Yearly WAPE bar comparison (2020–2022 vs naive; 2023–2025 vs naive, RF logdiff, and EWA mix): `/workspace/arf_experiment/charts/yearly_wape_comparison.png`

The monthly axis is capped at 250%. 2020-04 and 2021-05 exceed the cap and are annotated.

## Caveats

- This is one shared national ARF, not 77 per-province models, and `n_models` is 10 or 30 versus 300 trees in the batch RF.
- The comparison to RF logdiff and the EWA mix is not the same training protocol. Those models refit (or reweight) a batch RF each month on all history through *t*−1. ARF is a single incremental model from 2019 with tree-level resets only. That difference is the point of the trial.
- Logdiff learning skipped 54 COVID-zero rows; predictions at `lag12 = 0` are 0.
- Monthly WAPE at near-zero totals (especially 2020-04) is a poor guide; yearly WAPE is the fairer summary.
- No multi-seed run; only seed 42. No ARF hyperparameter search beyond the four requested configs. Leaf prediction stayed at the river default (`adaptive`).
- Rain features are in the model, as in the ext set; this trial does not re-test whether rain has signal.
- 2023 is still the only clearly abnormal post-COVID year in the later window, and even there ARF does not beat the current monthly RF.

## Files

| Path | What |
|---|---|
| `run.py` | Experiment runner |
| `chart.py` | Chart renderer (matplotlib via `/workspace/data_review/pylib` and `/workspace/.plotenv`) |
| `results/yearly_wape.csv` / `yearly_wape_pct.csv` | Yearly scores |
| `results/monthly_wape.csv` | Monthly WAPE and drift counts |
| `results/drift_counts_by_year.csv` | Tree resets by year |
| `results/predictions.csv` | Province-month predictions |
| `results/summary.json` | Config metadata, timings, recovery stats |
| `charts/monthly_wape_arf_vs_naive.png` | Slide chart 1 |
| `charts/yearly_wape_comparison.png` | Slide chart 2 |
