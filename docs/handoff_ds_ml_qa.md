# Handoff: Data Science, ML, QA

## Start table

Use **`data/core/province_month.parquet`**: full **77 × 92** spine (2018-01–2025-08 CE), HII **`avg_rain_mm`**, MOTS **`guest_arrivals`** (monthly, chiefly from domestic quarterly workbooks), **`visitors_total`** (monthly, from MOTS `(R)` province reports, 2023-01–2025-08), and **`is_covid`**.

Sub-area sheets (Pattaya, Koh Chang, etc.) are **not** rolled into this table; they remain in the long table with `area_level = sub_area` and a null `province_id`.

When the same province-month is published in more than one workbook, the pipeline keeps the **first published value**: the column issued for that year. A later workbook that repeats the month as last year's comparison does not replace it. `conflicts.csv` is empty after this rule. Do not average the copies or switch to the later revision unless the pipeline owner changes the rule.

The modeling window ends at **2025-08**. September–December 2025, including 2025-Q3 and 2025-Q4, stay in `fact_tourism_long` only.

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

## For data science, ML, and QA

- Predict from **`guest_arrivals`** when you need the full 2018-01–2025-08 window. Where both series exist, it matches the monthly guest count. Use **`visitors_total`** only from **2023-01** onward; earlier cells are missing, not zero.
- A `guest_arrivals` value of **0** is a published zero (27 cells, mostly 2020-04). Keep it as zero.
- Model the **77-province** grid first. The 14 resort sheets are exploration rows in the long table, not columns on `province_month`.
- **`is_covid`** marks 2020-03 through 2022-12. It is a window label, not a claim that COVID caused the change.
- Leave **`fact_accommodation_nso`** off the monthly table until you write a join rule and the pipeline owner accepts it.
- **`region`** is the six-region tourism split. Bangkok is `central`. A different map is a change to `dim_province`.
- **`unmapped_provinces.csv`** lists province names that failed to match. Resort sheets are not listed. An empty file means every province matched.

## For the pipeline owner

You own the duplicate-month rule, the 2025-08 window, whether resort rows gain a parent `province_id`, NSO re-extraction, and `python -m src.ingest.mots_cleanup`. Other roles need a copy of **`data/core/`** from you when those Parquet files are not in git.

## No feature engineering

This pipeline does not add lags, rolling means, or model features.
