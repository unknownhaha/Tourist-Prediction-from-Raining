# Tourist Prediction from Raining — data pipeline

Province × month datasets (2018-01–2025-08 CE) combining HII rainfall, MOTS tourism attachments, and NSO accommodation surveys.

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
python -m src.qa.checks
python -m src.qa.coverage_report
python -m pytest tests/
```

**Modeling start table:** `data/core/province_month.parquet` — `guest_arrivals`, `visitors_total`, rainfall, `is_covid` (see `docs/handoff_ds_ml_qa.md` and `docs/coverage_report.md`).

## Documentation

- **Handbook (Markdown):** [docs/index.md](docs/index.md) — Thai: [docs/th/index.md](docs/th/index.md)
- **Interactive explorer:** open [site/index.html](site/index.html) in a browser after `python -m src.docs.build_site` (switch **English / ไทย** in the header)

Tested with **Python 3.11+** (CI/local may use 3.14).
