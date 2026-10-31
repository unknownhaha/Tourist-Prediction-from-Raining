# Handoff: Data Science, ML, QA

## Start table

Use **`data/core/province_month.parquet`**: complete province × month spine (2018-01–2025-08), HII rainfall, optional `visitors_unknown` from MOTS when parsed, and non-null **`is_covid`**.

## Other tables

- **`fact_tourism_long`**: all MOTS metrics and types; use for exploration and custom pivots.
- **`fact_accommodation_nso`**: sparse NSO survey snapshots — do not treat as monthly without an approved join rule.
- **`dim_month` / `dim_province`**: dimensions for joins.

## QA

Run `python -m src.qa.checks` before modeling. Review manifest failures and MOTS quarantine list.

## No feature engineering

This pipeline does not add lags, rolling means, or model features.
