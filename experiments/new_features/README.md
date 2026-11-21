# Five candidate features for the log-diff forest

Protocol B from `experiments/beat_seasonal_naive`: train 2019-01..2023-12, choose on 2024 WAPE, then score 2025 once. 2025 is not used to choose. The model is `rf|logdiff|ext|d10_leaf3` (300 trees, depth 10, leaf 3, `random_state=42`). The baseline feature set is the existing extended set. Seasonal naive on 2025 is WAPE `0.05480419201387209`. The untouched log-diff baseline is frozen WAPE `0.04788460348726727` and monthly WAPE `0.04829016837105007`. This run recomputes both numbers with absolute difference 0.

## Features

Every lag reads a month strictly before `t`. Holiday and festival columns are the calendar of `t`.

1. **Thai and foreign lags.** `lag_1`, `lag_12`, and `lag_1 / lag_13` for `guest_arrivals_thai` and `guest_arrivals_foreign`. A zero denominator becomes NaN, never infinity. A separate candidate fits two log-diff forests, one for each series, and adds the forecasts.
2. **Three-month year-over-year growth.** `log(sum of lags 1-3) - log(sum of lags 13-15)` when both sums are positive.
3. **Regional trend.** `log(sum of other provinces in the region at t-1) - log(the same sum at t-13)`. The province itself is left out. Regions are the six tourism regions already on the modeling table: `central`, `north`, `northeast`, `east`, `west`, `south`. Bangkok is `central`.
4. **Public holidays.** Count of Thailand public-holiday dates in the month, and count of long weekends. A long weekend is a run of at least three consecutive Saturdays, Sundays, or public holidays. A run that crosses a month boundary counts in both months. Dates come from `holidays` 0.109, country `TH`, which includes substitution days and bridge holidays. The library cites the Holidays Act and later Royal Gazette amendments.
5. **Festival flags.** National: Songkran (every April), New Year (January and December), Loy Krathong (month of the full moon of the 12th Thai lunar month), Chinese New Year (month of lunar new year). Province flags, one month each: Chiang Mai Flower Festival (50, February), Ubon Ratchathani Candle Festival (34, the month of Asarnha Bucha), Surin Elephant Round-up (32, November), Phi Ta Khon in Loei (42, June).

Loy Krathong dates used here are 2018-11-22, 2019-11-11, 2020-10-31, 2021-11-19, 2022-11-08, 2023-11-27, 2024-11-15, and 2025-11-05 (Wikipedia scheduled-date table; TAT newsroom confirms 2019-11-11 and 2020-10-31). Chinese New Year dates are 2018-02-16, 2019-02-05, 2020-01-25, 2021-02-12, 2022-02-01, 2023-01-22, 2024-02-10, and 2025-01-29 (Hong Kong Observatory lunar tables).

Gaps: Songkran the festival is flagged in April even in 2020, when the official public holidays were moved out of April. Those moved days are in the holiday count. Phi Ta Khon sometimes slips into July; the flag stays in June. Province flags are the usual month, not the locally announced days. 2018 Thai and foreign arrivals were not published, so their lags are missing until the source month is 2019-01 or later (`lag_13` is complete from 2020-02). The three-month log is complete from 2019-04. After that, a NaN ratio means a zero denominator. Training median-fills those cells from the fit window only.

## Validation and data quality

Validation runs first and blocks on a hard failure (`validation_report.json`). It checks schema, dtypes, unique province-month keys, 77 provinces, the six region labels, 2019-2025 feature coverage, 2018-2025 calendar coverage, no infinities, holiday counts in 0..31, and flags in {0, 1}. This run had 0 hard failures and 2 warnings: 42 Thai year-over-year ratios and 667 foreign ratios are NaN after warm-up because the lag-13 denominator is zero. Thai plus foreign matches total on all 6,468 rows from 2019-01 (0 mismatches above `max(1, 0.1% of total)`).

Data quality is a separate step (`dq_report.json`). Scores are 0 to 1. This run:

| Dimension | Score |
|---|---|
| Completeness | 1.0 |
| Accuracy | 0.5392316017316017 |
| Consistency | 1.0 |
| Uniqueness | 1.0 |
| Timeliness | 1.0 |
| Validity | 1.0 |
| Overall | 0.923205266955267 |

Accuracy is the weak score: 3,406 of 7,392 province-months (fraction `0.46076839826839827`) have a robust z-score above 3.5 or a year-over-year jump above 100% on arrivals or rain. Those rows are warnings. Hard stops live in `dq_config.json` and were not crossed. DS/ML can edit them. The current hard caps are: no missing total or rain, no missing Thai or foreign from 2019 onward, no duplicate keys, no month gaps, no invalid flags or holiday counts above 31, Thai/foreign mismatch fraction at most 0.01, and flagged-row fraction at most 0.50.

## 2024 selection

Lowest 2024 WAPE among all 32 feature subsets, plus the two-equation Thai/foreign model:

| Set | 2024 WAPE | MAE | RMSE |
|---|---|---|---|
| 3-month YoY + region + holidays | 0.058472241482920746 | 11036.69563418394 | 29549.442131843567 |
| that set plus festivals | 0.058558929363923096 | 11053.058060756464 | 29699.269722329554 |
| 3-month YoY + region | 0.05881635761296896 | 11101.64790715717 | 29697.779715019948 |
| region alone | 0.059549931109950036 | 11240.110658133613 | 30264.263138604176 |
| 3-month YoY alone | 0.06110595456851978 | 11533.811684062313 | 28639.32229331421 |
| baseline | 0.06309623983770205 | 11909.480072755445 | 30604.439471250524 |
| holidays alone | 0.0631456113165246 | 11918.79899009049 | 30562.285494252214 |
| festivals alone | 0.06346873772348045 | 11979.789431272411 | 30693.220385103 |
| Thai/foreign lags alone | 0.1067646397701948 | 20151.935409917714 | 64784.270680904585 |
| all five | 0.09311642848150035 | 17575.82151169556 | 50741.53092976845 |
| two separate log-diff models | 0.2625858513661918 | 49563.3491358156 | 141947.9998325529 |

The selected set is **3-month YoY + regional trend + holiday counts**.

## 2025

Naive WAPE is `0.05480419201387209`.

| Set | Frozen WAPE | MAE | RMSE | Provinces | Monthly WAPE | MAE | RMSE | Provinces |
|---|---|---|---|---|---|---|---|---|
| baseline | 0.04788460348726727 | 9136.952348350851 | 19797.47950344812 | 49/77 | 0.04829016837105007 | 9214.338955891064 | 20876.888872928965 | 52/77 |
| 3-month YoY | 0.046857369405632926 | 8940.94385770958 | 19884.310691296167 | 50/77 | 0.04696888340079562 | 8962.222055413498 | 20009.71408889552 | 49/77 |
| region | 0.053578012414517776 | 10223.322544186256 | 23255.7500928162 | 45/77 | 0.05115494216342848 | 9760.97189682637 | 23250.122615455424 | 48/77 |
| holidays | 0.04798032124227173 | 9155.216435399194 | 20047.923403854045 | 49/77 | 0.048096165211998804 | 9177.320843792626 | 20439.05275374947 | 50/77 |
| festivals | 0.048027892405090124 | 9164.293579536827 | 19877.697369992515 | 48/77 | 0.0483204254263261 | 9220.112362208025 | 20723.195661122496 | 51/77 |
| Thai/foreign lags | 0.07221375754593805 | 13779.244549201394 | 39066.807354071236 | 32/77 | 0.05455009593278017 | 10408.807650841518 | 27392.69349066128 | 42/77 |
| all five | 0.06284945984100998 | 11992.425077501059 | 28437.94842175555 | 37/77 | 0.05134855948956368 | 9797.916387411593 | 23496.38382895364 | 50/77 |
| selected: YoY3 + region + holidays | 0.05361698766137748 | 10230.75948523594 | 23102.218929112943 | 45/77 | 0.04989817046005171 | 9521.164895614327 | 22619.60472092634 | 51/77 |
| two models, summed | 0.0884787808726898 | 16882.80461357287 | 52673.90765648513 | 32/77 | 0.07100700743848956 | 13548.982263933522 | 44277.238135073676 | 40/77 |

March, July, and October, the months the baseline loses to naive:

| Month | Naive | Baseline frozen | Selected frozen | 3-month YoY frozen |
|---|---|---|---|---|
| 2025-03 | 0.059521535872140374 | 0.06265644376239807 | 0.06512486193115964 | 0.05574147992417274 |
| 2025-07 | 0.04721169464766233 | 0.06684951294370736 | 0.07046254017130284 | 0.05687363070621815 |
| 2025-10 | 0.0452240276535178 | 0.04860183947829597 | 0.0454587454300711 | 0.04499461079947227 |

The selected set still loses March, July, and October. The 3-month YoY feature alone wins March and October and still loses July.

## Champion replay of the selected set

Same raw-equivalent rows, PSI only on `guest_lag_1`, `guest_lag_12`, `rain_lag_1`, and `rain_roll3`. Challengers are `logdiff_expanding` and `logdiff_12m` with the selected columns, plus `rf_expanding`, `rf_12m`, and `linear_expanding` on the six base features.

| Preset | Promotions | Served WAPE | MAE | RMSE | MASE |
|---|---|---|---|---|---|
| legacy | 5 | 0.04988478444511697 | 9518.610684621946 | 24274.672034559764 | 0.9102366554823047 |
| 0.005, confirm 2 | 2 | 0.05267428349698934 | 10050.880309022303 | 24332.166958645 | 0.9611360292230268 |
| 0.01, confirm 2 | 1 | 0.049975928570366236 | 9536.002068669975 | 22432.36601154518 | 0.9118997422262202 |

Legacy promotions, effective the next month: March `logdiff_12m`, May `logdiff_12m`, July `rf_12m`, August `rf_12m`, December `logdiff_12m`. Confirm-2 at margin 0.005 promotes June `logdiff_12m` and August `rf_12m`. Margin 0.01 confirm 2 promotes only June `logdiff_12m`. All three beat naive and all three are worse than the untouched baseline frozen WAPE.

## Recommendation

Keep the current log-diff forest. The set chosen on 2024 beats naive in 2025 (frozen `0.05361698766137748`, monthly `0.04989817046005171`) but is worse than the baseline on both 2025 scores. Thai/foreign lags, the summed two-model variant, festivals, and the full set of five do not help. Holiday counts barely move the baseline. The regional trend helps 2024 and hurts 2025.

The 3-month YoY log is the only addition that improves both 2025 numbers (frozen `0.046857369405632926`, monthly `0.04696888340079562`). It was not selected, because its 2024 WAPE (`0.06110595456851978`) is worse than the three-feature set. It still loses July to naive. One year is not enough to replace the baseline with it.

## Caveats

2024 was spent on selection, so it is not an untouched test of these features. The untouched log-diff 2024 test remains the Protocol A number, `0.09206712099170007`. 2025 is one year. Province festival flags are coarse. The accuracy DQ score is low because recovery years produce many year-over-year jumps; that threshold is a warning cap, not a claim that those rows are wrong.
