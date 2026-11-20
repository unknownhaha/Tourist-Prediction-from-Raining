# Log-diff forest as the frozen Champion

Exploratory run. The serving Champion starts as Protocol B's selected model, `rf|logdiff|ext|d10_leaf3` with alpha 1, fit on 2019-01..2024-12. 2025 is read from the raw MOTS workbooks and the HII rainfall file, then validated, cleaned, checked, and turned into features. Promotion uses the presets in `src/stream/champion_policy.py`.

Challengers on a trigger are `logdiff_expanding`, `logdiff_12m`, `rf_expanding`, `rf_12m`, and `linear_expanding`. The two log-diff windows use the extended lag features and the log-difference target. The other three stay level models on the six documented features. Each family has its own Page-Hinkley detector. Presets C–E seed the promoted family's detector with its own trailing 12 revealed-month WAPEs. `legacy` resets that detector with `initial_count=12` and no history.

PSI uses only `guest_lag_1`, `guest_lag_12`, `rain_lag_1`, and `rain_roll3`, binned on the 2023-01..2024-12 reference. Month sine/cosine, province indicators, extra lags, year-over-year growth, and the rain anomaly are not given PSI scores. `rain_lag_1` still needs two consecutive alarms. A promotion serves from the next month. Challengers train only through the previous month.

`no_promotion` leaves the log-diff Champion in place all year. Its served WAPE is `0.04788460348726727`, the same as the Protocol B frozen score (absolute difference 0).

Seasonal naive on these 924 rows is WAPE `0.05480419201387209`. Every preset below beats that, and every preset beats the earlier level-RF Champion runs (legacy `0.0601809721016076`, margin 0.005 confirm 1 `0.06028049862702164`, margin 0.005 confirm 2 `0.06254650120481828`, margin 0.01 confirm 2 `0.06390442846674334`).

| Preset | Triggers | Promotions | WAPE | MAE | RMSE | MASE | Bias | Provinces improved |
|---|---|---|---|---|---|---|---|---|
| no promotion | 7 | 0 | 0.04788460348726727 | 9136.952348350851 | 19797.47950344812 | 0.8737397948526759 | 0.0007047596632298002 | 49/77 |
| legacy | 7 | 5 | 0.05155689266358662 | 9837.668836946536 | 24537.403154943924 | 0.9407472452205206 | 0.008208421571799667 | 50/77 |
| 0.005, confirm 1 | 7 | 5 | 0.05155689266358662 | 9837.668836946536 | 24537.403154943924 | 0.9407472452205206 | 0.008208421571799667 | 50/77 |
| 0.005, confirm 2 | 7 | 1 | 0.04976558162114711 | 9495.865366856266 | 21908.424545879956 | 0.9080615878535421 | 0.0005060203268848159 | 48/77 |
| 0.01, confirm 2 | 7 | 0 | 0.04788460348726727 | 9136.952348350851 | 19797.47950344812 | 0.8737397948526759 | 0.0007047596632298002 | 49/77 |

Bias is `(sum(pred) - sum(actual)) / sum(actual)` on the served forecasts. The untouched log-diff Champion is about 0.07% high.

The same seven triggers fire in every run. Page-Hinkley is not among them.

| Month | Features |
|---|---|
| 2025-02 | `rain_roll3` |
| 2025-03 | `rain_lag_1` |
| 2025-05 | `rain_roll3` |
| 2025-06 | `rain_lag_1`, `rain_roll3` |
| 2025-07 | `rain_lag_1`, `rain_roll3` |
| 2025-08 | `rain_roll3` |
| 2025-12 | `rain_lag_1` |

`legacy` and margin 0.005 with one confirmation month make the same five promotions, effective the next month: 2025-03 `logdiff_12m`, 2025-05 `logdiff_expanding`, 2025-07 `rf_12m`, 2025-08 `rf_12m`, 2025-12 `logdiff_12m`. Margin 0.005 with two confirmation months promotes once, 2025-08 `rf_12m`. Margin 0.01 with two confirmation months does not promote.

The lowest served WAPE is the run that never promotes. Its monthly WAPE, 77 provinces each month:

| Month | Served | Seasonal naive |
|---|---|---|
| 2025-01 | 0.04465073560535522 | 0.06123703341180149 |
| 2025-02 | 0.04441309846003542 | 0.047233847282790585 |
| 2025-03 | 0.06265644376239807 | 0.059521535872140374 |
| 2025-04 | 0.054964210995049445 | 0.059211731559480604 |
| 2025-05 | 0.02787143320060826 | 0.056546329271376615 |
| 2025-06 | 0.05793754937771296 | 0.07776265108954325 |
| 2025-07 | 0.06684951294370736 | 0.04721169464766233 |
| 2025-08 | 0.03972160096677285 | 0.04930610533668116 |
| 2025-09 | 0.054507494690251235 | 0.06744558012965428 |
| 2025-10 | 0.04860183947829597 | 0.0452240276535178 |
| 2025-11 | 0.03681032903200419 | 0.044058379479706686 |
| 2025-12 | 0.038371020768902774 | 0.04532800304643122 |

March, July, and October still lose to the lag. Raw ingest wrote no DQ warnings, no cleaning actions, and no halts.

This is one year. Promoting on a trigger raised the served WAPE relative to leaving the log-diff Champion frozen.
