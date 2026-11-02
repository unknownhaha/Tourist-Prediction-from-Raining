# แผนที่ repository

## ระดับบนสุด

| Path | หน้าที่ |
|------|---------|
| `config/` | การตั้งค่า YAML/CSV/json |
| `src/` | แพ็กเกจ Python ของไปปไลน์ |
| `data/` | raw, staging, core, manifest (ส่วนใหญ่ไม่ commit) |
| `docs/` | markdown คู่มือ + `explorer.yaml` |
| `docs/th/` | คู่มือภาษาไทย |
| `site/` | HTML ที่ generate |
| `tests/` | pytest |

## `config/`

| ไฟล์ | ใช้ทำอะไร |
|------|-----------|
| `sources.yaml` | ช่วงวันที่ URL แหล่งข้อมูล |
| `data_contracts.yaml` | คอลัมน์  grain เกณฑ์ QA |
| `province_crosswalk.csv` | ชื่อจังหวัด → `province_id` |
| `mots_category_tree.json` | จาก crawler `--build-tree` |

## `src/`

| แพ็กเกจ | จุดเข้า |
|---------|---------|
| `src/ingest/` | hii, nso, mots_crawler, mots_cleanup |
| `src/staging/` | `python -m src.staging` |
| `src/core/` | `build_core` |
| `src/qa/` | checks, coverage_report |
| `src/catalog/` | profile_mots |
| `src/common/` | paths, manifest, provinces |
| `src/docs/` | `build_site` |

## `data/`

ดู [data/README](../../data/README.md)

## การไหลของเอกสาร

```text
docs/*.md, docs/th/*.md  +  explorer.yaml
        │
        ▼
python -m src.docs.build_site
        │
        ▼
site/index.html
```

เอกสารที่ generate:

- `docs/coverage_report.md` ← `python -m src.qa.coverage_report`
- `docs/file_type_catalog.md` ← `python -m src.catalog.profile_mots`
