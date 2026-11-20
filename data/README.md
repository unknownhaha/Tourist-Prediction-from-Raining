# Data directories

| Path | Contents |
|------|----------|
| `raw/` | Untouched downloads from HII, MOTS, NSO |
| `staging/` | Parsed Parquet by source |
| `core/` | Dimension and fact tables built by the batch pipeline |
| `model/` | Unified modeling table (`province_month_unified`) — start here for DS/ML |
| `manifest/` | Download manifest CSV |

`raw/` is git-ignored. Rebuild core and the unified table with the commands in the project README (`python -m src.core.build_core`, then `python scripts/build_unified_province_month.py`).
