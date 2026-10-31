# Data directories

| Path | Contents |
|------|----------|
| `raw/` | Untouched downloads from HII, MOTS, NSO |
| `staging/` | Parsed Parquet by source |
| `core/` | Dimension and fact tables for DS/ML/QA |
| `manifest/` | Download manifest CSV |

These folders are git-ignored. Rebuild with documented `python -m` commands in the project README.
