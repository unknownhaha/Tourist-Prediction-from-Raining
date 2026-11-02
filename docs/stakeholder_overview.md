# Stakeholder overview

This project builds a **province-by-month** dataset for analyzing tourism and rainfall across all **77** Thai provinces over a fixed calendar window.

## What we combine

| Source | What it provides | In the main modeling table? |
|--------|------------------|----------------------------|
| **HII** (Hydro-Informatics Institute) | Monthly average rainfall by province | Yes — `avg_rain_mm` on every spine row |
| **MOTS** (Ministry of Tourism and Sports) | Tourism statistics from published Excel/PDF attachments | Partially — see below |
| **NSO** (National Statistical Office) | Accommodation survey rounds (MD survey) | Separate table only; **not** a monthly tourism series |

## The main deliverable for modeling

**`province_month.parquet`** is a complete grid: every province × every month in the window (**2018-01** to **2025-08**), with columns for rainfall, two tourism measures where available, and a COVID-era flag.

- **`guest_arrivals`** — from MOTS domestic quarterly workbooks, spread to months where the pipeline can parse them; coverage is strong across the window when sources publish.
- **`visitors_total`** — from MOTS monthly province reports; typically populated from **2023-01** onward (see the live [Coverage report](coverage_report.md)).
- **`is_covid`** — project-defined flag for **2020-03** through **2022-12**. It marks a disruption window for analysis; it is **not** a claim about causation or policy impact.

Sub-areas (e.g. Pattaya, Koh Chang) appear in the long tourism table but are **not** rolled into `province_month`.

## What this pipeline does not do

- It does **not** train or deploy prediction models.
- It does **not** add lags, rolling means, or other ML features.
- It does **not** guarantee that every tourism cell is filled — gaps reflect missing or unparsed source publications. The [Coverage report](coverage_report.md) is the authoritative snapshot of fill rates after each build.

## Data quality and provenance

Downloads are logged in **`data/manifest/manifest_downloads.csv`**. QA summarizes **latest status per URL**, not every historical retry. Files that cannot be parsed safely are listed under **`data/staging/mots/quarantine/`**.

For technical run instructions, operators use the [Operator runbook](pipeline_operators.md). For table definitions, see the [Data dictionary](data_dictionary.md).
