# คู่มือผู้ปฏิบัติการ

รันจาก root ของ repository

## สิ่งที่ต้องมี

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

- **Python 3.11+**
- **7-Zip** (`7z.exe`) บน Windows สำหรับไฟล์ RAR ของ NSO

## ไปปไลน์เต็ม (เริ่มใหม่)

```powershell
python -m src.ingest.hii
python -m src.ingest.nso
python -m src.ingest.mots_crawler --build-tree
python -m src.ingest.mots_crawler --year-be 2561
python -m src.ingest.mots_crawler --all
python -m src.ingest.mots_cleanup
python -m src.staging
python -m src.catalog.profile_mots
python -m src.core.build_core
python -m src.qa.checks
python -m src.qa.coverage_report
python -m pytest tests/
python -m src.docs.build_site
```

`mots_cleanup` และ `profile_mots` เป็นทางเลือก แต่แนะนำหลัง crawl MOTS เต็ม

## รันบางส่วน

| สถานการณ์ | รัน |
|-----------|-----|
| มี raw แล้ว เปลี่ยน parser หรือ crosswalk | `python -m src.staging` → `python -m src.core.build_core` → QA |
| อัปเดต `config/province_crosswalk.csv` เท่านั้น | staging → core → QA |
| อัปเดต CSV HII | `python -m src.ingest.hii` → core → QA |
| มี RAR NSO แล้ว | `python -m src.ingest.nso --stage-only` → core → QA |
| เปลี่ยนเฉพาะสัญญา/QA | `python -m src.qa.checks` และ pytest |
| แก้คู่มือ | `python -m src.docs.build_site` |

## อ้างอิงขั้นตอน

### HII — `python -m src.ingest.hii`

ดาวน์โหลด CSV ฝน บันทึก manifest เขียน staging rainfall

### NSO — `python -m src.ingest.nso`

ดึงรอบสำรวจ MD แตก RAR **`--stage-only`**: แตก/ stage โดยไม่ดาวน์โหลดใหม่

### MOTS crawler — `python -m src.ingest.mots_crawler`

| Flag | ใช้เมื่อ |
|------|----------|
| `--build-tree` | สร้าง `config/mots_category_tree.json` |
| `--year-be 2561` | crawl หนึ่งปี พ.ศ. (ทดลอง) |
| `--all` | ทุกปีใน `config/sources.yaml` |
| `--inventory-only` | นับสต็อกอย่างเดียว |
| `--skip-posts` | แนบจากหมวดเท่านั้น (เร็วกว่า) |

### Staging — `python -m src.staging`

อ่าน workbook ใน `data/raw/mots/` เขียน `fact_tourism_long.parquet` และ quarantine

### Core — `python -m src.core.build_core`

เขียน Parquet 7 ไฟล์ใน `data/core/` รวม **`province_month.parquet`**

### QA

- **`python -m src.qa.checks`** — ตรวจสัญญา manifest quarantine
- **`python -m src.qa.coverage_report`** — อัปเดต `docs/coverage_report.md`
- **`python -m pytest tests/`**

## ห้ามแก้ด้วยมือ

- **`data/raw/`**, **`data/manifest/manifest_downloads.csv`**, **`site/`** (แก้ `docs/` แล้วรัน build_site)

## แก้ปัญหา

- ความครอบคลุมท่องเที่ยวต่ำ: ดู **`data/staging/mots/quarantine/`**
- QA manifest ล้ม: ดูสถานะล่าสุดต่อ URL
- เกณฑ์ `guest_arrivals` / `visitors_total`: ใน `config/data_contracts.yaml`
