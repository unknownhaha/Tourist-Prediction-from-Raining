# Data dictionary

## Conventions

- **Dates** in core tables use Gregorian **CE** `year_month` (`YYYY-MM`) unless labeled `year_be` (Buddhist Era).
- **`is_covid`**: `true` for `2020-03` through `2022-12` inclusive (34 months); `false` otherwise. Stored in `dim_month` and joined to facts and `province_month`. Project-defined disruption window, not causation.

## Core tables (`data/core/`)

| Table | Grain | Description |
|-------|-------|-------------|
| `dim_province` | province_id | 77 Thai provinces |
| `dim_month` | year_month | 2018-01 … 2025-08 with `year_be`, `is_covid` |
| `fact_rainfall` | province_id × year_month | HII min/max/avg rainfall (mm) |
| `fact_tourism_long` | row per metric | All parsed MOTS metrics (long format) |
| `fact_tourism_province_month` | province × month × metric × tourist_type | Province-month tourism only |
| `fact_accommodation_nso` | survey file | NSO MD round inventory (not monthly) |
| `province_month` | province × month | **Start here for DS/ML**: full 77×92 spine, rainfall, primary `visitors_unknown`, `is_covid` |

## Staging (`data/staging/`)

- `hii/fact_rainfall.parquet`
- `mots/fact_tourism_long.parquet`, `mots/quarantine/quarantine.csv`
- `nso/fact_accommodation_nso_inventory.parquet`

## Manifest

`data/manifest/manifest_downloads.csv` — provenance for every download (URL, sha256, status).
