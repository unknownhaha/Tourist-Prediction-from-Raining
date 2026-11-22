# Tourist Prediction from Raining — data pipeline

Province × month datasets (2018-01–2025-12 CE) combining HII rainfall, MOTS tourism attachments, and NSO accommodation surveys.

## Setup

```powershell
cd e:\Tourist-Prediction-from-Raining
pip install -r requirements.txt
python -m playwright install chromium
```

NSO RAR extraction needs **7-Zip** (`7z.exe`) on Windows.

## Run order

```powershell
python -m src.ingest.hii
python -m src.ingest.nso              # or --stage-only after RARs exist
python -m src.ingest.mots_crawler --build-tree
python -m src.ingest.mots_crawler --year-be 2561   # pilot
python -m src.ingest.mots_crawler --all
python -m src.ingest.mots_cleanup   # optional: move off-topic 2018 menu downloads
python -m src.staging
python -m src.catalog.profile_mots
python -m src.core.build_core
python scripts/build_unified_province_month.py
python -m src.qa.checks
python -m src.qa.coverage_report
python -m pytest tests/
```

**Modeling start table:** `data/model/province_month_unified.parquet` — target `guest_arrivals_total`, rainfall, `is_covid` (see `data/model/README.md`). Rebuild with `python scripts/build_unified_province_month.py`.

**Feature rules (Thai, for data scientists):** [docs/feature_rules_for_modeling.md](docs/feature_rules_for_modeling.md) — lags through t−1 only, no quarter total that contains the month being predicted.

**2025 replay design (Thai):** [docs/stream_2025_replay_pipeline.md](docs/stream_2025_replay_pipeline.md) — month-by-month feeder for the online model.

**Next-build handoff (English):** [docs/HANDOFF_NEXT_STEPS.md](docs/HANDOFF_NEXT_STEPS.md) — frozen 2024 baseline, then refit through 2024 as the 2025 warm start.

### Interactive 2025 training studio

For monthly replay from the original 2025 sources use:

```powershell
python -m src.stream.studio_server --raw-2025 --port 8767
```

Open <http://127.0.0.1:8767/>. Train pins history through December 2024. Each Step Month reads the selected month's guest grid from the six MOTS workbooks and the matching HII rain records from the original CSV. These are annual/multi-year containers. File downloads are cached under `data/raw`; `config/raw_stream_2025.json` pins exact revisions and province sheets. Missing files are fetched from their source URLs. A checksum mismatch or incomplete batch halts the month. Previously accepted months and model checkpoints are committed together in `outputs/studio_2025_raw_rain_lag_2m/`.

The raw Studio uses a Champion-only policy in `outputs/studio_2025_raw_rain_lag_2m/`. The earlier raw replay remains in its original directory. Each month the deployed Champion makes 77 official forecasts. For rain_lag_1, PSI must reach 0.15 in two consecutive months to raise an alarm. A month below threshold resets the count. Other features raise an alarm immediately at 0.15. Any PSI alarm or Champion Page-Hinkley > 3 starts a competition among an expanding random forest, a 12-month random forest and expanding linear regression. All three fit through the preceding month. Their WAPE on the newly revealed month is compared to the Champion's already recorded WAPE. A strictly better candidate is deployed as fitted for the next month; ties keep the incumbent. Only the Champion is scored in months without alarms. Seasonal lag remains a predictor and the scale for MASE. A new Champion starts a fresh Page-Hinkley accumulator with its model family's historical WAPE calibration. Step Month displays progress from the backend and records the comparison and deployment version. This reconstructs historical months from the available releases; original publication dates are not simulated.

Run from the repository root. The Studio serves `site/studio.html` by default. To use a different location, pass `--page PATH`.

```powershell
python -m src.stream.studio_server
```

Open <http://127.0.0.1:8765>. Train evaluates the frozen 2024 holdout before refitting through December 2024. Step Month and Auto-run use the same transaction for each batch of 77 provinces. A fixed random forest, a monthly expanding-window random forest, and monthly linear regression run alongside the seasonal-naive benchmark. The UI offers province and nationwide scores, drift events, a manual Champion strategy registry, and JSON export.

Open the [presentation lab](http://127.0.0.1:8765/presentation) to play the observed stream or compare nine simulated streams. Select a change in March, June, or September, then step through the months or pause automatically on the selected model's first alarm. The self-contained `site/drift-lab.html` also opens offline. Rebuild it with `python -m src.stream.build_drift_lab` after changing the data or models.

The studio compares four features with calendar-month quartiles from 2023–2024 (PSI ≥ 0.15). Each learned model has an upward Page-Hinkley detector on standardized monthly WAPE (2023 calibration, δ = 0.5, λ = 3). PSI and performance alarms are recorded independently. Rollback reselects a previously promoted model strategy for future months. It preserves completed predictions and the learning history of all three strategies.

The pinned source snapshot, SQLite checkpoint, quality reports, feature tables, and lineage are under `outputs/studio_2025/`. Reloading or restarting resumes that run. For a separate exercise starting in January, keep the completed run and launch:

```powershell
python -m src.stream.studio_server --output-dir outputs/studio_2025_practice --port 8766
```

Replay 2025 with the shipped seasonal-naive baseline (no extra packages; the real model implements `predict` / `learn` in `src/stream/model.py`):

```powershell
python -m src.stream.replay --start 2025-01 --end 2025-12 --seed 2025 --output-dir outputs/stream_2025
```

Logs, the data-quality reports, lineage, and the 2023-01..2024-12 reference set are written under `outputs/stream_2025/`. That directory is gitignored. Add `--drift page-hinkley` for the optional error hook. `--model online-linear` is a deterministic numpy step, not a tuned model.

The pipeline table `data/core/province_month.parquet` is still produced by `python -m src.core.build_core` and keeps its original column names (see `docs/handoff_ds_ml_qa.md` and `docs/coverage_report.md`).

## Documentation

- **Handbook (Markdown):** [docs/index.md](docs/index.md) — Thai: [docs/th/index.md](docs/th/index.md)
- **Modeling feature rules (Thai):** [docs/feature_rules_for_modeling.md](docs/feature_rules_for_modeling.md)
- **2025 replay design (Thai):** [docs/stream_2025_replay_pipeline.md](docs/stream_2025_replay_pipeline.md)
- **Handoff for the next build (English):** [docs/HANDOFF_NEXT_STEPS.md](docs/HANDOFF_NEXT_STEPS.md)
- **Interactive explorer:** open [site/index.html](site/index.html) in a browser after `python -m src.docs.build_site` (switch **English / ไทย** in the header)

Tested with **Python 3.11+** (CI/local may use 3.14).

## 2025 online replay (exp/online-replay-2025)

`experiments/online_replay/` holds the month-by-month 2025 replay of the EWA Champion: the seasonally adjusted `exp_lr_recent` features, DQ v2, the COVID and EWA A/B tests chosen on 2024, the PSI/ADWIN drift log, the 2023 drift demo, the rain-effect report and the slide charts. See `experiments/online_replay/README.md`. Run it with `./.venv/bin/python experiments/online_replay/run.py --stage all`.
