# Repository map

## Top level

| Path | Role |
|------|------|
| `config/` | YAML/CSV/json configuration — safe to edit with care |
| `src/` | Pipeline Python packages |
| `data/` | Raw, staging, core, manifest (mostly git-ignored) |
| `docs/` | Handbook markdown + `explorer.yaml` |
| `site/` | Generated HTML explorer |
| `tests/` | Pytest suite |

## `config/`

| File | Purpose |
|------|---------|
| `sources.yaml` | Date window, COVID window, HII/NSO/MOTS URLs and year categories |
| `data_contracts.yaml` | Required columns, grains, uniqueness, coverage floors for QA |
| `province_crosswalk.csv` | Thai province name → `province_id` |
| `mots_category_tree.json` | Built by MOTS crawler `--build-tree` |

## `src/` packages

| Package | Entry modules |
|---------|----------------|
| `src/ingest/` | `hii`, `nso`, `mots_crawler`, `mots_cleanup` |
| `src/staging/` | `python -m src.staging` → MOTS parsers + HII staging |
| `src/core/` | `build_core` → core Parquet |
| `src/qa/` | `checks`, `coverage_report`, consistency helpers |
| `src/catalog/` | `profile_mots` → `docs/file_type_catalog.md` |
| `src/common/` | paths, manifest, provinces, calendar, config loader |
| `src/docs/` | `build_site` → `site/index.html` |

## `data/` (rebuilt by pipeline)

See [data/README](../data/README.md). Summary:

- **`raw/`** — untouched downloads.
- **`staging/`** — parsed Parquet + MOTS quarantine CSVs.
- **`core/`** — modeling tables; **`province_month.parquet`** is the DS/ML entry point.
- **`manifest/`** — download provenance CSV.

## Documentation flow

```text
docs/*.md  +  docs/explorer.yaml
        │
        ▼
python -m src.docs.build_site
        │
        ▼
site/index.html  (+ assets)
```

Generated docs (refresh via pipeline, not by hand):

- `docs/coverage_report.md` ← `python -m src.qa.coverage_report`
- `docs/file_type_catalog.md` ← `python -m src.catalog.profile_mots`
