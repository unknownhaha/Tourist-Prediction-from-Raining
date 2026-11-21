# Rolling-origin backtest: 2023 and 2024

Run: `./.venv/bin/python experiments/backtest/run.py` (about 2.5 minutes on the box). Outputs: `results.csv`, `results.json`, `monthly_wape.csv`, `validation_report.json`, `dq_report.json`.

## Protocol

| Target year | Frozen fit window | Monthly refit |
|---|---|---|
| 2023 | 2019-01..2022-12 | expanding, 2019-01..t-1 |
| 2024 | 2019-01..2023-12 | expanding, 2019-01..t-1 |
| 2025 (recomputed check) | 2019-01..2024-12 | expanding, 2019-01..t-1 |

2018 is lag history only. `is_covid` and `is_covid_lag12` are kept. Every feature for month t reads months before t. Frozen models never see a target-year label. Monthly refit sees target-year months strictly before t, the same way the 2025 replay does.

Models, no retuning:

- **RF logdiff**: `rf|logdiff|ext|d10_leaf3` (300 trees, depth 10, leaf 3, `random_state=42`, StandardScaler, extended lag features).
- **RF level**: `rf|level|base|d10_leaf3` with the six documented features (`guest_lag_1`, `guest_lag_12`, `rain_lag_1`, `rain_roll3`, `is_covid`, `is_covid_lag12`).
- **RF logdiff + YoY3**: the log-diff forest plus `yoy_3m_log` from `experiments/new_features`.
- **Seasonal naive**: `guest_lag_12`.

Code reused: `experiments/beat_seasonal_naive/run.py` (feature table, `fit_spec`, rain anomaly capped at the training cutoff), `experiments/new_features` (YoY3 feature, validation, DQ). The 2025 rows recompute the earlier numbers: logdiff 0.047885 / 0.048290, naive 0.054804, level 0.062744 / 0.059616, YoY3 0.046857 / 0.046969.

**2024 was the selection year for RF logdiff (Protocol B), so its 2024 score is optimistic.** 2023 is untouched by any selection of the log-diff forest.

## Validation and DQ (run first)

Validation: 0 hard failures, 2 warnings (Thai/foreign YoY ratios NaN where lag-13 is zero). DQ: completeness 1.0, accuracy 0.5392316017316017, consistency 1.0, uniqueness 1.0, timeliness 1.0, validity 1.0, overall 0.923205266955267. No hard stop was crossed.

## Results

MASE is MAE over seasonal-naive MAE on the same rows. Bias is `sum(pred)/sum(actual) - 1`. A province beats naive when its summed absolute error over the 12 months is lower.

### 2023

| Model | Mode | WAPE | MAE | RMSE | MASE | Bias | Provinces beating naive |
|---|---|---|---|---|---|---|---|
| RF logdiff | frozen | 0.257652 | 43880.4 | 127412.1 | 0.7876 | +0.1625 | 37/77 |
| RF logdiff | monthly | 0.199670 | 34005.6 | 107191.3 | 0.6103 | +0.0688 | 42/77 |
| RF level (6 features) | frozen | 0.085681 | 14592.3 | 43643.1 | 0.2619 | -0.0433 | 75/77 |
| RF level (6 features) | monthly | 0.074883 | 12753.3 | 39752.3 | 0.2289 | -0.0336 | 75/77 |
| RF logdiff + YoY3 | frozen | 0.272031 | 46329.3 | 132847.3 | 0.8315 | +0.1828 | 35/77 |
| RF logdiff + YoY3 | monthly | 0.202377 | 34466.7 | 107506.2 | 0.6186 | +0.0726 | 42/77 |
| Seasonal naive | — | 0.327155 | 55717.5 | 170728.7 | 1.0000 | -0.3245 | — |

### 2024

| Model | Mode | WAPE | MAE | RMSE | MASE | Bias | Provinces beating naive |
|---|---|---|---|---|---|---|---|
| RF logdiff | frozen | 0.063096 | 11909.5 | 30604.4 | 0.5658 | -0.0288 | 55/77 |
| RF logdiff | monthly | 0.059361 | 11204.4 | 30295.9 | 0.5323 | -0.0189 | 56/77 |
| RF level (6 features) | frozen | 0.085397 | 16118.7 | 49962.8 | 0.7657 | -0.0375 | 53/77 |
| RF level (6 features) | monthly | 0.069977 | 13208.3 | 36532.1 | 0.6275 | -0.0329 | 54/77 |
| RF logdiff + YoY3 | frozen | 0.061106 | 11533.8 | 28639.3 | 0.5479 | -0.0262 | 58/77 |
| RF logdiff + YoY3 | monthly | 0.056739 | 10709.6 | 28137.5 | 0.5088 | -0.0149 | 58/77 |
| Seasonal naive | — | 0.111521 | 21049.7 | 56693.0 | 1.0000 | -0.0977 | — |

### 2025

| Model | Mode | WAPE | MAE | RMSE | MASE | Bias | Provinces beating naive |
|---|---|---|---|---|---|---|---|
| RF logdiff | frozen | 0.047885 | 9137.0 | 19797.5 | 0.8737 | +0.0007 | 49/77 |
| RF logdiff | monthly | 0.048290 | 9214.3 | 20876.9 | 0.8811 | +0.0014 | 52/77 |
| RF level (6 features) | frozen | 0.062744 | 11972.2 | 31782.6 | 1.1449 | +0.0012 | 27/77 |
| RF level (6 features) | monthly | 0.059616 | 11375.4 | 29015.0 | 1.0878 | -0.0015 | 32/77 |
| RF logdiff + YoY3 | frozen | 0.046857 | 8940.9 | 19884.3 | 0.8550 | +0.0041 | 50/77 |
| RF logdiff + YoY3 | monthly | 0.046969 | 8962.2 | 20009.7 | 0.8570 | +0.0044 | 49/77 |
| Seasonal naive | — | 0.054804 | 10457.3 | 25420.9 | 1.0000 | -0.0108 | — |

### Per-month WAPE 2023

| Month | logdiff frozen | logdiff monthly | level frozen | level monthly | YoY3 frozen | YoY3 monthly | naive |
|---|---|---|---|---|---|---|---|
| 2023-01 | 0.2186 | 0.2186 | 0.1591 | 0.1591 | 0.2435 | 0.2435 | 0.4369 |
| 2023-02 | 0.2689 | 0.2419 | 0.0905 | 0.0790 | 0.2700 | 0.2351 | 0.4084 |
| 2023-03 | 0.1908 | 0.1359 | 0.1253 | 0.0960 | 0.1918 | 0.1321 | 0.4145 |
| 2023-04 | 0.3044 | 0.3918 | 0.1230 | 0.0957 | 0.3202 | 0.3994 | 0.3982 |
| 2023-05 | 0.6922 | 0.4616 | 0.0709 | 0.0438 | 0.7351 | 0.4700 | 0.3818 |
| 2023-06 | 0.2643 | 0.4777 | 0.0635 | 0.0370 | 0.2421 | 0.4691 | 0.3614 |
| 2023-07 | 0.2755 | 0.1026 | 0.0578 | 0.0450 | 0.3083 | 0.1064 | 0.3130 |
| 2023-08 | 0.1714 | 0.0740 | 0.0459 | 0.0473 | 0.1772 | 0.0707 | 0.3349 |
| 2023-09 | 0.2967 | 0.1175 | 0.0770 | 0.0819 | 0.3265 | 0.1190 | 0.2882 |
| 2023-10 | 0.1670 | 0.0951 | 0.0710 | 0.0554 | 0.1813 | 0.0954 | 0.2369 |
| 2023-11 | 0.1272 | 0.0832 | 0.0554 | 0.0625 | 0.1463 | 0.0868 | 0.2048 |
| 2023-12 | 0.1567 | 0.0423 | 0.0861 | 0.0890 | 0.1646 | 0.0455 | 0.1804 |

### Per-month WAPE 2024

| Month | logdiff frozen | logdiff monthly | level frozen | level monthly | YoY3 frozen | YoY3 monthly | naive |
|---|---|---|---|---|---|---|---|
| 2024-01 | 0.0540 | 0.0540 | 0.0927 | 0.0927 | 0.0549 | 0.0549 | 0.1298 |
| 2024-02 | 0.0779 | 0.0909 | 0.1020 | 0.0875 | 0.0767 | 0.0859 | 0.1561 |
| 2024-03 | 0.0781 | 0.0760 | 0.1135 | 0.0954 | 0.0812 | 0.0813 | 0.1103 |
| 2024-04 | 0.0678 | 0.0649 | 0.0983 | 0.0785 | 0.0664 | 0.0581 | 0.1237 |
| 2024-05 | 0.0446 | 0.0337 | 0.0604 | 0.0322 | 0.0440 | 0.0340 | 0.1120 |
| 2024-06 | 0.0562 | 0.0561 | 0.0550 | 0.0685 | 0.0548 | 0.0549 | 0.1014 |
| 2024-07 | 0.0735 | 0.0632 | 0.0796 | 0.0657 | 0.0647 | 0.0538 | 0.1150 |
| 2024-08 | 0.0552 | 0.0514 | 0.0732 | 0.0475 | 0.0522 | 0.0511 | 0.0994 |
| 2024-09 | 0.0852 | 0.0740 | 0.1088 | 0.0729 | 0.0840 | 0.0741 | 0.1288 |
| 2024-10 | 0.0644 | 0.0555 | 0.0841 | 0.0655 | 0.0551 | 0.0468 | 0.1006 |
| 2024-11 | 0.0583 | 0.0578 | 0.0689 | 0.0549 | 0.0590 | 0.0534 | 0.0834 |
| 2024-12 | 0.0457 | 0.0377 | 0.0869 | 0.0750 | 0.0434 | 0.0357 | 0.0809 |
## Summary (WAPE)

| Model | 2023 frozen | 2023 monthly | 2024 frozen* | 2024 monthly* | 2025 frozen | 2025 monthly |
|---|---|---|---|---|---|---|
| RF logdiff | 25.77% | 19.97% | 6.31% | 5.94% | 4.79% | 4.83% |
| RF level | 8.57% | 7.49% | 8.54% | 7.00% | 6.27% | 5.96% |
| RF logdiff + YoY3 | 27.20% | 20.24% | 6.11% | 5.67% | 4.69% | 4.70% |
| Seasonal naive | 32.72% | 32.72% | 11.15% | 11.15% | 5.48% | 5.48% |

\* 2024 was the selection year for RF logdiff.

## Why log-diff struggles in 2023

The 2023 label is `log(y_2023) - log(y_2022)`, and 2022 was still a COVID year. National log ratios by month in the training window were extreme: 2020-04 -3.877, 2021-04 +2.298, 2022-07 +2.532, 2022-08 +2.623. The training labels 2019-01..2022-12 have std 1.671, 1%/99% quantiles -5.487 / +4.362, and 44.76% of rows with |ratio| above 2x. The 2023 actual log ratios are much calmer: mean 0.2837, std 0.2521, median 0.2284, 1%/99% quantiles -0.1188 / 1.196, 7.79% of rows above 2x.

The frozen model also meets an unseen regime. Every 2023 row has `is_covid = 0` and `is_covid_lag12 = 1`, a combination that never appears in 2019-2022. The nearest training rows are the 2022 recovery months (`is_covid = 1`, `is_covid_lag12 = 1`, ratios around +2), so the forest over-predicts. Its predicted log ratios have mean 0.3699 versus the actual 0.2837, and the frozen bias is +16.25%. The worst month is May: predicted median log ratio 0.7281 versus actual 0.2451, WAPE 0.6922 versus naive 0.3818. June swings the other way (predicted median -0.1433 versus 0.2060). Monthly refit cuts the bias to +6.88% once 2023 months enter the window, and from July on its monthly WAPE is 4-12%.

Largest 2023 ratios are Phuket and Krabi, foreign-led reopening: Phuket 2023-02 4.060x (875,685 vs 215,692), Phuket 2023-03 3.830x, Krabi 2023-03 3.651x. Worst provinces by frozen absolute error: Bangkok (19.0% of all error, WAPE 0.2422 vs naive 0.4470), Chon Buri (11.7%, 0.2986 vs 0.4061), Prachuap Khiri Khan (6.7%, 0.4827 vs 0.1486), Phuket (6.6%, 0.2493 vs 0.5416), Chiang Mai (5.8%, 0.3046 vs 0.2541). Full lists are in `results.json` under `logdiff_2023_analysis`.

The level forest does not divide by last year, so the 2022 base does not hurt it. It wins 2023 by a wide margin (8.57% frozen).

## Leakage test

`tests/test_backtest_leakage.py`: a target month must be after the cutoff; hiding every 2023 label leaves frozen 2023 forecasts unchanged; poisoning all arrivals and rain from 2023-01 leaves the 2023-01 forecast unchanged and does change the 2023-02 refit forecast (which may read 2023-01).
