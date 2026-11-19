# Project spec

This is the project scope. The handbook and `config/sources.yaml` still describe the pipeline as built. Where they disagree with this page, this page is the team decision and the config is the data that has actually been ingested.

## Goal

Predict monthly tourist numbers for each Thai province from rainfall.

The model runs online: detect drift, and retrain when drift is detected.

## Grain

One row per province × month.

- **77** Thai provinces.
- One calendar month per row (`YYYY-MM`, Gregorian).

The modeling table is `data/core/province_month.parquet`. It carries HII rainfall (`avg_rain_mm`) and MOTS tourism (`guest_arrivals`, `visitors_total`). Which of those tourism series is the training label is a modeling choice; both are province × month counts. `guest_arrivals` covers the ingested window. `visitors_total` is filled from 2023-01 onward.

## Official data range

**2018-01 through 2025-12.**

That end month is the team decision. The ingested window is shorter:

- `config/sources.yaml` `date_window.end` is **2025-08**.
- `province_month` therefore stops at **2025-08** (77 × 92).
- **September–December 2025 still have to be ingested** into that configured window and rebuilt into core.

Staged files can already hold months after 2025-08 (MOTS rows through 2025-12, HII rainfall past the window). Those rows are not in `province_month` until `date_window.end` moves to 2025-12 and core is rebuilt. This spec does not change the config.

## Sources

| Source | Role | Status |
| --- | --- | --- |
| HII rainfall | Monthly rainfall by province. Feature source for the model. | In scope |
| MOTS tourism | Monthly tourist numbers by province. Label source for the model. | In scope |
| NSO accommodation | Accommodation survey workbooks. | **Pending team decision, likely dropped.** Do not join it to `province_month` unless the team accepts it. |

Sub-area MOTS sheets (Pattaya, Koh Chang, and the other resort sheets) stay out of the 77-province grain.

## Platform

The team runs on **Windows**.

Backslash paths in stored lineage (`source_file`, NSO `file_path`, the manifest) are accepted. Do not treat them as a cleaning defect. New and changed code uses `pathlib` so path handling is not hard-coded to one separator.

## Roles

**Data Engineer** owns:

- ingestion
- cleaning
- streaming replay
- the reference set
- the retrain dataset

The reference set and the retrain dataset are what online learning uses when drift is detected. The Data Engineer produces them from the province × month grain above. This page does not move ingestion or those datasets to another role.

## Out of scope for the current pipeline code

The batch pipeline under `src/` downloads, stages, and builds the province × month table. It does not train a model, detect drift, or write a retrain dataset. Those stay with the Data Engineer scope above when that work is built. This document does not change pipeline logic.
