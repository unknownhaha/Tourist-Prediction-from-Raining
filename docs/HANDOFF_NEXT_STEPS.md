# Handoff: next modeling steps

สรุปภาษาไทย: PR #4 และ #5 อยู่บน `main` แล้ว (ตาราง unified ถึง 2025-12 และกฎ leakage) การตัดสินวันที่ 2026-11-20 คือ (1) ให้คะแนน test ปี 2024 ด้วยโมเดลที่เทรน 2019-01..2022-12 และเลือก hyperparameter จากปี 2023 แล้วแช่แข็งคะแนนนั้น (2) refit configuration เดิมบน 2019-01..2024-12 โดยไม่จูนเพิ่ม (3) ใช้โมเดลที่ refit เป็น warm start ของสาย 2025 (4) เทียบ metric ปี 2025 กับ baseline ปี 2024 ห้ามจูนบนปี 2024 หรือ 2025 ห้ามใช้ข้อมูลเดือน `t` ทำนายเดือน `t` สาย replay ยังไม่ถูกสร้าง รอทีมสั่งให้ลงมือ อย่า merge เข้า `main` โดยไม่มีการรีวิว

This file is the instruction sheet for the next coding assistant. Read it before editing models or the replay feeder. The decision below is closed.

## Current state

- `main` is `2e45adf` (merge of PR #5). PR #4 and PR #5 are merged.
- PR #4 added `data/model/province_month_unified.parquet` and `.csv`: 7,392 rows, 77 provinces × 96 months, 2018-01..2025-12. Target is `guest_arrivals_total`. `date_window.end` in `config/sources.yaml` is `2025-12`.
- PR #5 added leakage rules and `src/model/leakage.py` (`build_forecast_features`, `assert_no_future_leakage`, `TIME_SPLIT`).
- The 2026-11-20 split and warm-start decision are on branch `docs/stream-2025-replay-design` (PR #7) until that PR is merged. If you cloned `main` and this file is absent, check out that branch. Do not re-decide the split.

`TIME_SPLIT` in `src/model/leakage.py`:

| Role | Range | Use |
|---|---|---|
| `lag_history` | 2018-01 .. 2018-12 | Lag history only. Not trained on. |
| `train` | 2019-01 .. 2022-12 | Fit. Includes COVID 2020-03 .. 2022-12. Use `is_covid`. |
| `validation` | 2023-01 .. 2023-12 | Hyperparameter selection only. |
| `test` | 2024-01 .. 2024-12 | Held-out score. Already how the team evaluated the model. |
| `online_stream` | 2025-01 .. 2025-12 | Month-by-month replay. 12 months. |

Lag 12 of every 2025 month is the same calendar month in 2024, which is outside the COVID window, so `is_covid_lag12` is false for the whole stream. Keep the column.

## Decided warm start (do these in order)

1. **Freeze the 2024 baseline.** Fit on rows with `year_month` in 2019-01 .. 2022-12. Hyperparameters come only from 2023-01 .. 2023-12. Score 2024-01 .. 2024-12 with that fit. Do not fit on 2024 before this score. Write WAPE, MAE, RMSE, and MASE, overall and per province, to `reports/baseline_test_2024.json` using the schema below.
2. **Refit the same configuration.** Same algorithm, same hyperparameters, no search. Training rows are 2019-01 .. 2024-12. There is no remaining slice for checking a new tune.
3. **Warm-start the stream from that refit.** The 2025 online model starts from step 2, not from the 2019–2022 weights used in step 1.
4. **Compare the 2025 stream to the frozen 2024 baseline.** Report the same metrics. The 2024 file is the reference, not a number to beat by retuning.

2018 rows stay out of every loss. Their values may appear only as lags. Features for month `t` use data through `t-1` only (`build_forecast_features` / `assert_no_future_leakage`).

## Where to save the 2024 baseline

Path: `reports/baseline_test_2024.json`. Do not put predictions for 2025 in this file. One file, overwritten only if the frozen score is recomputed with the same protocol. Do not create it until the 2024 evaluation has actually been run.

```json
{
  "decision_date": "2026-11-20",
  "split_role": "test",
  "train": {"start": "2019-01", "end": "2022-12"},
  "validation_for_hyperparameters": {"start": "2023-01", "end": "2023-12"},
  "test": {"start": "2024-01", "end": "2024-12"},
  "tuned_on_test": false,
  "model_version": "",
  "hyperparameters": {},
  "metrics": {
    "overall": {"wape": 0.0, "mae": 0.0, "rmse": 0.0, "mase": 0.0},
    "per_province": [
      {
        "province_id": "10",
        "wape": 0.0,
        "mae": 0.0,
        "rmse": 0.0,
        "mase": 0.0,
        "n_months": 12
      }
    ]
  }
}
```

`province_id` is a 2-digit string. `per_province` has 77 objects, `province_id` sorted ascending. WAPE is `sum(|y-ŷ|)/sum(|y|)`. MASE divides MAE by the MAE of the seasonal naive `guest_lag_12` on the same rows. Do not store MAPE as a primary field. The 27 zero guest-arrival rows are in 2020-04, 2020-05, and 2021-01, not in 2024, but MAPE stays off this report so it matches the feature rules.

## What must not be done

- Do not tune hyperparameters on 2024 or on 2025. 2023 is the only selection year.
- Do not replace the frozen 2024 score with a score from the refit that has seen 2024.
- Do not feed month `t` into features for month `t`: no same-month rain unless a run is explicitly labeled a nowcast (still not decided), no same-month visitors, no Thai/foreign split of month `t`, no quarter total that contains `t`.
- Do not drop 2018 or COVID rows before computing lags.
- Do not fill 2018 Thai/foreign nulls or pre-2023 `visitors_total` with 0.
- Do not shuffle, and do not use random K-fold.
- Do not implement the 2025 replay until the team gives a go-ahead.
- Do not merge to `main` without review.

## Next build item

Build the 2025 replay feeder described in `docs/stream_2025_replay_pipeline.md`. It is not in the repo. Wait for the team to say to build it.

When that go-ahead arrives, the feeder replays 2025-01 .. 2025-12 one month at a time: features through `t-1`, `predict` all 77 provinces, reveal `y_t`, log, then `learn`. The model loaded at the start is the step-2 refit (2019-01 .. 2024-12). Proposed command, not implemented:

```powershell
python -m src.stream.replay --start 2025-01 --end 2025-12 --seed 2025 --model-path models/warm_start.joblib --log data/stream/replay_2025.parquet
```

Proposed modules, also not created: `src/stream/replay.py`, `contracts.py`, `metrics.py`, `drift.py`, and `tests/test_stream_replay.py`. Call `build_forecast_features` and `assert_no_future_leakage` from `src/model/leakage.py`. Do not copy the lag formulas.

## Files to read first

| Path | Why |
|---|---|
| `docs/feature_rules_for_modeling.md` | Target, leakage, split, metrics. Thai, English terms. |
| `docs/stream_2025_replay_pipeline.md` | Feeder design, model contract, log schema, test plan. Thai. |
| `src/model/leakage.py` | `TIME_SPLIT`, `build_forecast_features`, `assert_no_future_leakage`. |
| `tests/test_feature_leakage.py` | Split boundaries and the no-future-month checks. |
| `data/model/province_month_unified.parquet` | Modeling table. Also `.csv`. Column notes in `data/model/README.md`. |
| `data/model/README.md` | Column dictionary, 2018 nulls, 27 COVID zeros. |
| `config/sources.yaml` | `date_window` 2018-01 .. 2025-12, `covid_window` 2020-03 .. 2022-12. |
| `docs/project_spec.md` | Online model and streaming replay are in scope. The batch pipeline does not train. |
| `reports/baseline_test_2024.json` | Create only after the frozen 2024 score exists. |

Rebuild commands, if the table must be regenerated, are in the feature-rules doc: `python -m src.core.build_core` then `python scripts/build_unified_province_month.py`, from the repo root. Paths in code use `pathlib`.
