# PSI and ADWIN replay with rollback

Team proposal, not a final decision. The primary settings are in `config.json`.

The Champion is `rf|logdiff|ext|d10_leaf3`, fit on 2019-01..2024-12. Rain features are `rain_lag_1` and `rain_roll3` (through t−1). There is no same-month rain. `is_covid` and `is_covid_lag12` stay in the model. Challengers are Expanding, Rolling 12M, Seasonal-2Y, and the 3-month YoY log-diff model. Promotion uses preset D: WAPE margin 0.005, relative MAE and RMSE guardrails of 0, and 2 confirming trigger months. A promotion serves from the next month.

`river` is not installed. ADWIN is the local cut in `adwin.py` with delta `0.002`. Each month it sees the 77 province absolute errors of the model that actually served, in `province_id` order. A window shorter than 32 points is not cut. PSI uses the four base features against the same calendar month in the prior two years, threshold 0.15. `rain_lag_1` still needs two consecutive months. For every month of 2025 those two prior years are 2023 and 2024, so this Seasonal-2Y reference produces the same alerts as the fixed 2023-2024 reference (`seasonal_2y_matches_fixed_2023_2024_alerts` is true). The reference is not refreshed after a promotion in the primary runs. Rollback compares the new model with the previous model's shadow forecast over the next 2 served months and restores the previous model on the following month if the new absolute error is larger.

Validation and data quality run before any fit. This run matches the new-features reports: validation has 2 denominator warnings and no hard failures; the DQ overall score is `0.923205266955267`.

Seasonal naive WAPE on the 924 rows is `0.05480419201387209`.

## Primary results

Preset D, Seasonal-2Y reference, no refresh, rollback k=2. Every trigger rule kept the original Champion. Served WAPE is `0.04788460348726727`, MAE `9136.952348350851`, RMSE `19797.47950344812`, MASE `0.8737397948526759`. That is below naive. There were no promotions and no rollbacks.

| Trigger | Alerts | What fired |
|---|---|---|
| PSI only | 7 | Feb `rain_roll3`; Mar `rain_lag_1`; May `rain_roll3`; Jun both rain features; Jul both; Aug `rain_roll3`; Dec `rain_lag_1` |
| ADWIN only | 12 | ADWIN in every month. PSI features, when present, match the PSI row. |
| PSI or ADWIN | 12 | Same months as ADWIN only |
| PSI and ADWIN | 7 | Same months and features as PSI only. ADWIN was also true in each of those months. |
| PSI-gated, N=2 | 9 | The 7 PSI months, plus April and September, where ADWIN fired inside the two-month gate |

No challenger beat the Champion by 0.005 WAPE in two consecutive trigger months. The largest one-month gap was March, where Rolling 12M reached WAPE `0.04756850766547559` against the Champion's `0.06265644376239807`. February had not cleared the margin, so March did not confirm. In July the YoY3 model improved WAPE by `0.007724318150638933`, and in August by `0.004943681302320153`, which is under 0.005, so that pair did not confirm either.

## Comparisons

Preset E (margin 0.01, confirm 2), the fixed 2023-2024 reference, and turning refresh on all produced the same served WAPE, `0.04788460348726727`, with no promotions and no rollbacks. Refresh never changed a later alert because nothing was promoted, so the reference end stayed `2024-12`. Alert counts follow the trigger rule only: 7 for PSI and for PSI-and-ADWIN, 12 for ADWIN and for PSI-or-ADWIN, 9 for the two-month gate.

## Recommendation

Keep the log-diff Champion frozen. Under preset D it already beats the 5.48% naive target, and none of the challengers, including YoY3, confirmed a promotion. ADWIN with delta 0.002 on raw absolute error alarmed in all 12 months, so it did not separate a drift month from a normal one. That delta is an experiment setting. A scale-free error, or one ADWIN per province, is a later choice. Rollback did not get a case to act on.
