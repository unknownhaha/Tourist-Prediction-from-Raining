# Can a leakage-safe model beat seasonal naive?

Seasonal naive predicts `guest_lag_12`. Two selection protocols are recorded. Protocol A chooses on 2023 and keeps its 2024 score as the official test number. Protocol B, approved for the 2025 stream, trains through 2023 and chooses on 2024, then scores 2025 once.

`python experiments/beat_seasonal_naive/run.py` rewrites the result files. It needs the packages in the repo plus LightGBM 4.7.0 (used here with scikit-learn 1.9.1).

`python experiments/beat_seasonal_naive/run.py protocol-b` writes the Protocol B files and does not rewrite `results.json`.

## Protocol A

| Role | Months | What happens |
|---|---|---|
| Lag history | 2018 | Lags and the rain climatology only. Not trained on. |
| Train | 2019-01 .. 2022-12 | Fit every candidate. Includes COVID 2020-03 .. 2022-12. |
| Validation | 2023 | Select hyperparameters and the overall winner by WAPE. |
| Test | 2024 | Score every candidate once. Not used to choose. |
| Stream | 2025 | Refit the 2023 winner on 2019-01 .. 2024-12. |

Features for month `t` come from `build_forecast_features` plus lags 2, 3, and 13, year-over-year `guest_lag_1 / guest_lag_13`, month sine and cosine, a target-free province one-hot, and a rain anomaly. The anomaly is that province's same calendar month, using rain strictly before the row and no later than the fit's training end. The script checks that dropping month `t` and everything after it leaves the `t` features unchanged.

Targets are the level, `y / guest_lag_12`, `log1p(y)`, and `log(y) - log(guest_lag_12)`. Ratio and log-diff rows with a non-positive lag or, for the log-diff, a non-positive target, are left out of that fit. At predict time those rows fall back to the lag-12 value, or 0 when the lag is missing. Predictions are clipped at 0. A blend `alpha * model + (1 - alpha) * seasonal naive` picks `alpha` on a 0.05 grid using 2023 WAPE. Ties keep the smaller alpha.

2025 frozen means one fit on 2019-01 .. 2024-12. Lag inputs for month `t` still use revealed months before `t`. 2025 monthly refits on every month strictly before `t`, then predicts `t`.

## What 2023 selected

`rf|level|base|d10_leaf3`: random forest, level target, the six documented features, `n_estimators=300`, `max_depth=10`, `min_samples_leaf=3`, `random_state=42`, StandardScaler. Blend alpha stayed at 1, so the blend did not help on 2023. This is the current forest. Its 2024 WAPE matches `reports/baseline_test_2024.json` exactly (`0.09206712099170007`).

| Split | Model | WAPE | MAE | RMSE | MASE | n | Beats naive |
|---|---|---|---|---|---|---|---|
| 2023 | seasonal naive | 0.32715528816404066 | 55717.455627705625 | 170728.72135881853 | 1 | 924 | |
| 2023 | selected forest | 0.08568127995763093 | 14592.284113624344 | 43643.08777079416 | 0.26189789087153326 | 924 | yes |
| 2024 | seasonal naive | 0.11152113853639248 | 21049.72943722944 | 56692.98828924528 | 1 | 924 | |
| 2024 | selected forest | 0.09206712099170007 | 17377.763645297913 | 53929.89188551432 | 0.8255575776932728 | 924 | yes |
| 2025 frozen | seasonal naive | 0.05480419201387209 | 10457.292207792209 | 25420.935258304846 | 1 | 924 | |
| 2025 frozen | selected forest | 0.06274370496490428 | 11972.245788267925 | 31782.61704576488 | 1.1448705410896767 | 924 | no |
| 2025 monthly | seasonal naive | 0.05480419201387209 | 10457.292207792209 | 25420.935258304846 | 1 | 924 | |
| 2025 monthly | selected forest | 0.05961592738032414 | 11375.428593067638 | 29015.019625879766 | 1.0877986735984373 | 924 | no |

The selected forest beats seasonal naive on 2023 and on the 2024 test. It does not beat seasonal naive on 2025, frozen or monthly.

## 2024 errors for the selected forest

49 of 77 provinces have a smaller total absolute error than seasonal naive. Central, east, northeast, and south improve. North and west do not.

| Region | n | WAPE model | WAPE naive |
|---|---|---|---|
| central | 120 | 0.0466170353042638 | 0.08795685599723675 |
| east | 96 | 0.15078544351791842 | 0.15752792333331686 |
| north | 204 | 0.08182914528862509 | 0.06396069394131611 |
| northeast | 240 | 0.08955548687789311 | 0.09250774343682075 |
| south | 168 | 0.11104816926591668 | 0.16552017745847059 |
| west | 96 | 0.07807892839928919 | 0.0640795919445092 |

The ten provinces with the largest seasonal-naive absolute error in 2024:

| Province | Region | WAPE model | WAPE naive | Improved |
|---|---|---|---|---|
| Chon Buri | east | 0.18406490068803263 | 0.17202880156249958 | no |
| Bangkok | central | 0.04265319129886815 | 0.08674960069079889 | yes |
| Phuket | south | 0.16438068161699307 | 0.16921148200611294 | yes |
| Surat Thani | south | 0.12766626228258526 | 0.16034144025924224 | yes |
| Krabi | south | 0.07718019423799297 | 0.22235468821116955 | yes |
| Songkhla | south | 0.04090667825993251 | 0.14713584034817606 | yes |
| Rayong | east | 0.09068916636193151 | 0.14413847806284802 | yes |
| Chiang Mai | north | 0.08102789961415316 | 0.06442861691151107 | no |
| Nakhon Ratchasima | northeast | 0.08002861911377386 | 0.0882522904607584 | yes |
| Phang Nga | south | 0.08606583770011163 | 0.22164529071638414 | yes |

Chon Buri is the largest share of absolute error, and the selected forest is worse there than the lag-12 forecast.

## Other candidates

Every candidate's 2023 and 2024 scores are in `results.csv`. `selected_hyperparams` marks the 2023 winner inside each family, target, and feature set. `selected_overall` marks the one configuration above.

The lowest 2024 WAPE in the file was not selected. It is `rf|logdiff|ext|d10_leaf3` blended with alpha 0.55: 2024 WAPE `0.0699506589220892`, MAE `13203.258714807665`, RMSE `33260.78735524387`, MASE `0.6272412552465318`. Its 2023 WAPE is `0.16344198082743663`, well behind the selected forest, because 2023 seasonal naive is weak (WAPE `0.32715528816404066`: the lag-12 month is still 2022). That 2024 score was not used to choose a 2025 model.

16 of the 32 family winners beat 2024 seasonal naive. Ridge on the log targets with a small penalty produced WAPEs above 10 and was not selected.

## Protocol B

Train 2019-01 .. 2023-12. Select the variant, hyperparameters, and blend alpha by 2024 WAPE. Refit that configuration on 2019-01 .. 2024-12 and score 2025 once, frozen and monthly. The same 88 candidates and the same leakage rules are used. Rain anomaly and the scaler are fit inside each training window only. This does not replace the Protocol A 2024 test WAPE of `0.09206712099170007`.

2024 seasonal naive on these rows is WAPE `0.11152113853639248`. The five lowest 2024 validation scores:

| Rank | Config | Blend | Alpha | WAPE | MAE | RMSE | MASE |
|---|---|---|---|---|---|---|---|
| 1 | `rf\|logdiff\|ext\|d10_leaf3` | no | 1 | 0.06309623983770205 | 11909.480072755445 | 30604.439471250524 | 0.5657782969738241 |
| 2 | `rf\|logdiff\|ext\|d16_leaf1` | no | 1 | 0.06508154453828106 | 12284.20837400939 | 31962.310653190427 | 0.583580345326578 |
| 3 | `rf\|log1p\|ext\|d16_leaf1` | yes | 0.8 | 0.0653815986326132 | 12340.84389248702 | 30720.035601693125 | 0.5862709033523483 |
| 4 | `rf\|ratio\|ext\|d6_leaf3` | yes | 0.45 | 0.06693726829371087 | 12634.478138779496 | 32952.18799982379 | 0.6002204530208177 |
| 5 | `rf\|log1p\|ext\|d10_leaf3` | yes | 0.75 | 0.06731761270396523 | 12706.26853685012 | 31464.048700425774 | 0.6036309670744403 |

The selected configuration is rank 1: random forest, log-difference target, extended features, depth 10, leaf 3. The 2024 blend search kept alpha at 1.

| 2025 mode | WAPE | MAE | RMSE | MASE | Beats naive `0.05480419201387209` |
|---|---|---|---|---|---|
| Frozen | 0.04788460348726727 | 9136.952348350851 | 19797.47950344812 | 0.8737397948526759 | yes |
| Monthly | 0.04829016837105007 | 9214.338955891064 | 20876.888872928965 | 0.8811400478055913 | yes |
| Protocol A forest, frozen | 0.06274370496490428 | 11972.245788267925 | 31782.61704576488 | 1.1448705410896767 | no |
| Protocol A forest, monthly | 0.05961592738032414 | 11375.428593067638 | 29015.019625879766 | 1.0877986735984373 | no |

Frozen improves 49 of 77 provinces on total absolute error. Monthly improves 52 of 77. January frozen and monthly match, because both fits stop at 2024-12.

Per-month 2025 WAPE, 77 provinces each month:

| Month | Frozen | Monthly | Seasonal naive |
|---|---|---|---|
| 2025-01 | 0.04465073560535522 | 0.04465073560535522 | 0.06123703341180149 |
| 2025-02 | 0.04441309846003542 | 0.04510397067773263 | 0.047233847282790585 |
| 2025-03 | 0.06265644376239807 | 0.0656205981142112 | 0.059521535872140374 |
| 2025-04 | 0.054964210995049445 | 0.05619193943182779 | 0.059211731559480604 |
| 2025-05 | 0.02787143320060826 | 0.026717262521231827 | 0.056546329271376615 |
| 2025-06 | 0.05793754937771296 | 0.05696375987088142 | 0.07776265108954325 |
| 2025-07 | 0.06684951294370736 | 0.07332630544957626 | 0.04721169464766233 |
| 2025-08 | 0.03972160096677285 | 0.03940751170359358 | 0.04930610533668116 |
| 2025-09 | 0.054507494690251235 | 0.05410075985201961 | 0.06744558012965428 |
| 2025-10 | 0.04860183947829597 | 0.046464523927117585 | 0.0452240276535178 |
| 2025-11 | 0.03681032903200419 | 0.03750831765003393 | 0.044058379479706686 |
| 2025-12 | 0.038371020768902774 | 0.036438883662675586 | 0.04532800304643122 |

March, July, and October are months where the selected forecast loses to seasonal naive. July is the largest gap.

Sanity check, not used for selection. The previous run's lowest 2024 score was this same forest blended at alpha 0.55, fit only through 2022-12. Refitting that blend on 2019-01 .. 2024-12 and scoring 2025 gives frozen WAPE `0.04411923949121837` (MAE `8418.476076217932`, RMSE `19650.787673023104`, MASE `0.8050340287847115`) and monthly WAPE `0.04456836631015275` (MAE `8504.174819532387`, RMSE `19986.708955633956`, MASE `0.8132291467570865`). Both beat seasonal naive. Protocol B did not select that alpha: with training extended through 2023, the unblended forest won 2024.

## Caveats

2023 is a poor year for choosing a model that has to work in 2025. Lag 12 of 2023 is 2022, inside the COVID window, so seasonal naive looks much worse than it does in 2024 (lag 12 is 2023) or 2025 (lag 12 is 2024, WAPE `0.05480419201387209`). Protocol A therefore prefers a level forest over models that stay close to the lag. Training itself includes COVID months. 2024 and 2025 are one year each.

Protocol B spends 2024 on selection, so that year is no longer an untouched test. The official untouched test number remains the Protocol A forest's 2024 WAPE, `0.09206712099170007`. Protocol B's 2025 result is one year, and three of the twelve months still lose to seasonal naive. The alpha-0.55 sanity score is lower still, and it was not the selected configuration.
