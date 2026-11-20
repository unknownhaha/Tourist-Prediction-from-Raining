# Project handbook

Province × month datasets (**2018-01** through **2025-12** CE) combining HII rainfall, MOTS tourism attachments, and NSO accommodation survey metadata.

## Start by role

| Role | Start here |
|------|------------|
| Pipeline operator | [Operator runbook](pipeline_operators.md) |
| Data science / ML | [Feature rules](feature_rules_for_modeling.md) and [2025 replay design](stream_2025_replay_pipeline.md) (Thai) then [National picture](national_picture.md), [handoff](handoff_ds_ml_qa.md), and [Data dictionary](data_dictionary.md) |
| QA / analyst | [Handoff](handoff_ds_ml_qa.md) plus [Coverage report](coverage_report.md) |
| Stakeholder / PM | [National picture](national_picture.md) (chart) |

## Interactive explorer

Open **`site/index.html`** in a browser (double-click or drag into Chrome/Edge). Use **English / ไทย** in the header to switch languages. Thai prose lives under [`docs/th/`](th/index.md). Regenerate after handbook edits:

```powershell
python -m src.docs.build_site
```

## Quick reference

- **Modeling table:** `data/model/province_month_unified.parquet` — feature rules: [feature_rules_for_modeling.md](feature_rules_for_modeling.md) — 2025 replay design: [stream_2025_replay_pipeline.md](stream_2025_replay_pipeline.md)
- **Pipeline table:** `data/core/province_month.parquet`
- **Run order:** see [Operator runbook](pipeline_operators.md) or [README](../README.md)
- **Contracts:** `config/data_contracts.yaml`
- **Sources & dates:** `config/sources.yaml`
