# Handoff: Data Science, ML, QA

## Start table

Use **`data/core/province_month.parquet`**: full **77 × 92** spine (2018-01–2025-08 CE), HII **`avg_rain_mm`**, MOTS **`guest_arrivals`** (monthly, chiefly from domestic quarterly workbooks), **`visitors_total`** (monthly, from MOTS `(R)` province reports, 2023-01–2025-08), and **`is_covid`**.

Sub-area sheets (Pattaya, Koh Chang, etc.) are **not** rolled into this table; they remain in the long table with `area_level = sub_area`.

## Other tables

- **`fact_tourism_long`**: all MOTS metrics, tourist types, and quarterly rows; use for exploration and custom pivots.
- **`fact_tourism_province_month`**: province-level monthly facts only (one row per metric × tourist_type).
- **`fact_accommodation_nso`**: sparse NSO survey snapshots — do not treat as monthly without an approved join rule.
- **`dim_month` / `dim_province`**: dimensions (`region`, `province_en` on province).

## QA

```powershell
python -m src.qa.checks
python -m src.qa.coverage_report
python -m pytest tests/
```

Review **`data/staging/mots/quarantine/quarantine.csv`**, **`unmapped_provinces.csv`**, and **`conflicts.csv`** when coverage looks low. Manifest summaries use **latest status per URL** (not every historical attempt).

Optional: move off-topic 2018 crawler downloads (site menu mistaken as children):

```powershell
python -m src.ingest.mots_cleanup
```

## No feature engineering

This pipeline does not add lags, rolling means, or model features.
