# Primary PSI and ADWIN replay

Decision date: 2026-11-21. DS/ML still own the final values. The settings below are the primary experiment config in `config.json`.

- Champion: `rf|logdiff|ext|d10_leaf3`, fit through 2024-12.
- ADWIN reads one monthly WAPE, delta `0.002`, seeded with the Champion's expanding out-of-sample WAPE for 2023-01..2024-12.
- Challengers train only when PSI and ADWIN both fire.
- PSI alone is a watch. It is logged and does not retrain.
- Preset D: WAPE margin 0.005, relative MAE and RMSE guardrails of 0, confirm 2 months.
- PSI reference: Seasonal-2Y, the same calendar month in the prior two years. It is not refreshed after a promotion.
- Rain is lagged only (`rain_lag_1`, `rain_roll3` through t−1). `is_covid` and `is_covid_lag12` stay in the model.
- Rollback compares the next 2 served months with the previous model's shadow forecast.

`river` is not installed. ADWIN is the local Bifet-Gavalda cut and does not cut a window shorter than 32 points. The seed has 24 months, so the first month that can be cut is 2025-08.

For each seed month t, the same Champion spec is fit on 2019-01 through t−1 and scored on t. The fit does not see t. Every month in the seed has at least 48 training months. The series is `oos_wape` in `results.json`. The seed produced no cut. The window that enters 2025 has 24 points.

Validation and data quality ran before any fit: 2 denominator warnings, no hard failures, DQ overall `0.923205266955267`.

## Primary result

The Champion served all 12 months. ADWIN alarmed in no month, so PSI and ADWIN never fired together. Challengers were not trained. Promotions: 0. Rollbacks: 0.

Served WAPE `0.04788460348726727`, MAE `9136.952348350851`, RMSE `19797.47950344812`, MASE `0.8737397948526759`, on 924 rows. Seasonal naive WAPE is `0.05480419201387209`. The served forecast is below that naive.

PSI watches, logged and not used for retraining:

| Month | Features |
|---|---|
| 2025-02 | `rain_roll3` |
| 2025-03 | `rain_lag_1` |
| 2025-05 | `rain_roll3` |
| 2025-06 | `rain_lag_1`, `rain_roll3` |
| 2025-07 | `rain_lag_1`, `rain_roll3` |
| 2025-08 | `rain_roll3` |
| 2025-12 | `rain_lag_1` |
