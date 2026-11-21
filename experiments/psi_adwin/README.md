# ADWIN input and delta comparison

Team proposal, not a final decision. The primary replay is unchanged: preset D (WAPE margin 0.005, confirm 2), Seasonal-2Y PSI reference, no reference refresh, lagged rain only, `is_covid` and `is_covid_lag12` kept, rollback after 2 served months. The Champion is `rf|logdiff|ext|d10_leaf3` fit through 2024-12. This run varies only what ADWIN sees and its delta. Settings are in `config.json`.

`river` is not installed. ADWIN is the local Bifet-Gavalda cut. It does not cut a window shorter than 32 points. The cut threshold assumes the stream lies on [0, 1]. A smaller delta raises that threshold, so it is harder to alarm, not easier.

Validation and data quality ran before any fit: 2 denominator warnings, no hard failures, DQ overall `0.923205266955267`. Seasonal naive WAPE on the 924 rows of 2025 is `0.05480419201387209`.

## Recommendation

No setting in this grid alarms on a middle number of 2025 months.

Province absolute error alarms in all 12 months at every delta from 0.002 down to 1e-6. The threshold stays near 1 while province errors are in the thousands, so lowering delta does not make the detector selective.

Monthly WAPE alarms in none of the 12 months of 2025. Without a seed the stream has 12 points and cannot cut. With either seed, 2025 stays inside the window that remains after the COVID-era errors.

The defensible setting is monthly WAPE at delta 0.002, seeded with the 2019-02..2024-12 out-of-sample series. During that seed it cuts once, when 2023-08 arrives, and drops history through 2021-07. It then raises no alarm in 2025. That matches a Champion whose 2025 WAPE is `0.04788460348726727`. Delta does not change the 2025 silence on this series, so the primary delta stays 0.002. Province absolute error should not be the ADWIN input.

PSI still alarms in 7 months. With a silent ADWIN, OR matches PSI, and both AND and the two-month gate stay quiet.

## Served forecast

Every one of the 115 replays kept the original Champion. Promotions: 0. Rollbacks: 0. Served WAPE `0.04788460348726727`, MAE `9136.952348350851`, RMSE `19797.47950344812`. All are below naive.

## (a) Province absolute error

One value per province, in `province_id` order, from the model that served. No seed. ADWIN months are January through December at every delta below. The five trigger rules therefore have the same alert months at every delta.

| Trigger | Alert months | Alerts |
|---|---|---|
| PSI only | Feb, Mar, May, Jun, Jul, Aug, Dec | 7 |
| ADWIN only | Jan–Dec | 12 |
| PSI or ADWIN | Jan–Dec | 12 |
| PSI and ADWIN | Feb, Mar, May, Jun, Jul, Aug, Dec | 7 |
| PSI-gated, N=2 | Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Dec | 9 |

PSI features on those months: Feb `rain_roll3`, Mar `rain_lag_1`, May `rain_roll3`, Jun both rain features, Jul both, Aug `rain_roll3`, Dec `rain_lag_1`. April and September enter the gate only because ADWIN is already true and PSI was true in the previous month.

| Delta | ADWIN months | Promotions | Rollbacks | Below naive |
|---|---|---|---|---|
| 0.002 | 12, Jan–Dec | 0 | 0 | yes |
| 1e-3 | 12, Jan–Dec | 0 | 0 | yes |
| 1e-4 | 12, Jan–Dec | 0 | 0 | yes |
| 1e-5 | 12, Jan–Dec | 0 | 0 | yes |
| 1e-6 | 12, Jan–Dec | 0 | 0 | yes |

## (b) Monthly WAPE

One WAPE per month, from the model that served. The same five trigger rules apply. Because ADWIN never alarms in 2025, the trigger months do not depend on delta or seed:

| Trigger | Alert months | Alerts |
|---|---|---|
| PSI only | Feb, Mar, May, Jun, Jul, Aug, Dec | 7 |
| ADWIN only | none | 0 |
| PSI or ADWIN | Feb, Mar, May, Jun, Jul, Aug, Dec | 7 |
| PSI and ADWIN | none | 0 |
| PSI-gated, N=2 | none | 0 |

| Seed | Points before 2025 | Cuts in the seed | Window entering 2025 | ADWIN months in 2025, every listed delta |
|---|---|---|---|---|
| none | 0 | 0 | 0 | none |
| 2019-2024 out of sample | 71 (2019-02..2024-12) | 1 | 41 | none |
| 2023-2024 out of sample | 24 | 0 | 24 | none |

Deltas compared: 0.002, 0.01, 0.05, 0.1, 0.2, 0.5. Promotions 0, rollbacks 0, served WAPE below naive, for every cell.

The 2019-2024 seed does cut once before 2025. Every delta drops history through 2021-07. The cut arrives later when delta is smaller, because the threshold is higher:

| Delta | Cut while adding | Window starts at |
|---|---|---|
| 0.002 | 2023-08 | 2021-08 |
| 0.01 | 2023-04 | 2021-08 |
| 0.05 | 2023-01 | 2021-08 |
| 0.1 | 2022-12 | 2021-08 |
| 0.2 | 2022-11 | 2021-08 |
| 0.5 | 2022-11 | 2021-08 |

Those cuts are seed state. They are not 2025 alerts. After the cut, later history is appended and the window that enters 2025 has 41 points at every delta. The 2023-2024 seed is only 24 points, so it cannot cut until the window reaches 32, which is 2025-08. It still does not cut.

## How the seed was computed

The warm-start Champion for 2025 is one fit through 2024-12. Scoring that same fit on 2019-2024 would be in sample. The seed is not that.

For each month t from 2019-02 through 2024-12, the same spec (`rf|logdiff|ext|d10_leaf3`) is fit on 2019-01 through t−1 and scored on t. The fit does not see t. 2019-01 has no prior modeling month, so it is omitted. 2019-02 through 2019-12 use 1 to 11 training months. StandardScaler warns on those short fits because some columns are constant. Each month of 2023-2024 has at least 48 training months.

Five seed months have WAPE above 1, so they sit outside the [0, 1] bound the cut assumes: 2020-03 (`1.7812299894593921`), 2020-04 (`12.834772322609977`), 2021-05 (`1.8273593841058138`), 2021-06 (`2.8511139703005464`), and 2021-07 (`4.381459878960756`). The series is in `results.json` under `oos_wape`.
