# Operator runbook

Run from the repository root (`e:\Tourist-Prediction-from-Raining` or your clone path).

## Prerequisites

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

- **Python 3.11+** (local/CI may use 3.14).
- **7-Zip** (`7z.exe`) on Windows for NSO RAR extraction.

## Full pipeline (cold start)

```powershell
python -m src.ingest.hii
python -m src.ingest.nso
python -m src.ingest.mots_crawler --build-tree
python -m src.ingest.mots_crawler --year-be 2561
python -m src.ingest.mots_crawler --all
python -m src.ingest.mots_cleanup
python -m src.staging
python -m src.catalog.profile_mots
python -m src.core.build_core
python -m src.qa.checks
python -m src.qa.coverage_report
python -m pytest tests/
python -m src.docs.build_site
```

`mots_cleanup` and `profile_mots` are optional but recommended after a full MOTS crawl. Regenerate the HTML handbook after coverage or markdown changes.

## Partial reruns

| Situation | Run |
|-----------|-----|
| Raw downloads already present; only parsers or crosswalk changed | `python -m src.staging` → `python -m src.core.build_core` → QA |
| Only `config/province_crosswalk.csv` updated | Staging → core → QA |
| Only HII CSV refreshed | `python -m src.ingest.hii` → core → QA |
| NSO RARs on disk, skip download | `python -m src.ingest.nso --stage-only` → core → QA |
| Contract thresholds or QA logic only | `python -m src.qa.checks` and pytest |
| Handbook or explorer content | `python -m src.docs.build_site` |

## Stage reference

### HII — `python -m src.ingest.hii`

Downloads the spatial rainfall CSV from HII, appends manifest rows, writes staging rainfall Parquet.

### NSO — `python -m src.ingest.nso`

Fetches configured MD survey rounds, extracts RARs, builds accommodation inventory staging. **`--stage-only`**: extract/stage existing RARs without new downloads.

### MOTS crawler — `python -m src.ingest.mots_crawler`

| Flag | Purpose |
|------|---------|
| `--build-tree` | Write `config/mots_category_tree.json` only |
| `--year-be 2561` | Crawl one Buddhist Era year (pilot) |
| `--all` | All years in `config/sources.yaml` |
| `--inventory-only` | Dry-run inventory |
| `--skip-posts` | Category attachments only (skip per-news Playwright) |

### Staging — `python -m src.staging`

Walks `data/raw/mots/` workbooks, dispatches layouts, dedupes, writes `data/staging/mots/fact_tourism_long.parquet`. Unmapped names go to **`unmapped_provinces.csv`**; unparsed files to **`quarantine.csv`**.

### Core — `python -m src.core.build_core`

Writes seven Parquet files under **`data/core/`**, including **`province_month.parquet`**.

### QA

- **`python -m src.qa.checks`** — validates grains, columns, coverage floors in `config/data_contracts.yaml`, manifest failures, quarantine counts.
- **`python -m src.qa.coverage_report`** — refreshes `docs/coverage_report.md`.
- **`python -m pytest tests/`** — regression tests.

## Do not hand-edit

- **`data/raw/`** — replace by re-ingest.
- **`data/manifest/manifest_downloads.csv`** — append-only log from ingest.
- **`site/`** — generated; edit `docs/` and run `build_site`.

## Troubleshooting

- Low tourism coverage: inspect **`data/staging/mots/quarantine/`** and re-run staging after fixing raw files or crosswalk.
- QA manifest errors: check latest status per URL in manifest summary output; re-crawl failed MOTS URLs if needed.
- **`guest_arrivals` / `visitors_total` floors**: defined in `config/data_contracts.yaml` under `province_month.coverage`.
