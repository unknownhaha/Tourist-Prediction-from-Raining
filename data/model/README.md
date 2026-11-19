# Unified monthly modeling table / ตารางโมเดลรายเดือนชุดเดียว

**File:** `province_month_unified.parquet` / `.csv` (CSV is UTF-8 with BOM, Excel-safe on Windows)

This is the **single source of truth** for monthly modeling (one row per province × month). The training **target is `guest_arrivals_total`** (hotel/accommodation guest arrivals). `visitors_total` is **not** in this table.

ตารางนี้เป็น**แหล่งข้อมูลหลัก**สำหรับโมเดลรายเดือน (1 แถวต่อจังหวัดต่อเดือน) ตัวแปรเป้าคือ **`guest_arrivals_total`** (ผู้เข้าพักแรม) ไม่มีคอลัมน์ `visitors_total`

Rebuild from the project root:

```
python scripts/build_unified_province_month.py
```

## Coverage / ขอบเขต

| | |
|---|---|
| Grain | 77 provinces × month (`YYYY-MM`, Gregorian) |
| Window | **2018-01 through 2025-12** (96 months) |
| Rows | **7,392** (= 77 × 96), balanced panel, no missing province-months, no duplicate keys |
| COVID flag | `is_covid` is true for **2020-03 through 2022-12** (`covid_window` in `config/sources.yaml`) |

## Column dictionary / พจนานุกรมคอลัมน์

| Column | Type | Meaning / ความหมาย |
|---|---|---|
| `province_id` | 2-digit zero-padded string | Official province code (10 = Bangkok, 50 = Chiang Mai). รหัสจังหวัด 2 หลัก |
| `province_th`, `province_en` | text | Standard Thai / English province name |
| `region` | text | Tourism region: `central` `north` `northeast` `east` `west` `south` (Bangkok is `central`) |
| `year_month` | text `YYYY-MM` | Calendar month |
| `year`, `month` | int | CE year and month 1–12 |
| `guest_arrivals_total` | float (person counts) | Guest arrivals, **target**. ผู้เข้าพักแรมรวม — ตัวแปรเป้า |
| `guest_arrivals_thai`, `guest_arrivals_foreign` | float (person counts) | Thai / foreign split of guest arrivals. แยกคนไทย / ชาวต่างชาติ |
| `rain_avg_mm`, `rain_min_mm`, `rain_max_mm` | float (mm) | Provincial monthly rainfall from HII (mean, min, max). ฝนรายเดือนจาก HII |
| `is_covid` | bool | In the project COVID window. อยู่ในช่วงโควิดของโปรเจกต์ |

Units: tourist counts are **people**; rainfall is **millimetres**.

## Lineage / แหล่งที่มา

1. **2019-01 … 2025-12** comes from `data/clean/mots_domestic_province/province_month` (MOTS “สถิตินักท่องเที่ยวภายในประเทศ Q1-Q4” workbooks). Values must match that table exactly.
2. **2018-01 … 2018-12** is prepended from `data/core/province_month.parquet` so lag-12 features for early 2019 have a history year. Only the **total** is available for 2018.
3. **Rain for every month** is joined from `data/staging/hii/fact_rainfall.parquet` (HII `spatial-rain-hii.csv`). The builder checks that it matches the clean table for 2019+ and the core table for 2018.

`data/core/province_month.parquet` is still produced by `python -m src.core.build_core` and **keeps its original column names** (`guest_arrivals`, `avg_rain_mm`, `min_rain_mm`, `max_rain_mm`, `visitors_total`, `year_ce`) so existing pipeline scripts, QA contracts, and tests do not break. Use **this unified table** for modeling.

`config/sources.yaml` `date_window` is **2018-01 … 2025-12**. Core is rebuilt to the same window.

## Important notes / ข้อควรรู้

- **2018 has no Thai/foreign split.** `guest_arrivals_thai` and `guest_arrivals_foreign` are **null** for all 924 rows in 2018 (77 × 12). The core extract published only the total. Do not fill these with 0. ข้อมูลปี 2018 มีแค่ยอดรวม ไม่มีแยกคนไทย/ต่างชาติ และ**ห้ามเติม 0 แทนค่าว่าง**
- **COVID zeros are real.** 27 rows have `guest_arrivals_total = 0`, all in 2020-04, 2020-05, or 2021-01, all with `is_covid = True`. These are published lockdown zeros, not missing data. Keep them as zero. ค่า 0 ช่วงโควิดเป็นยอดจริง ไม่ใช่ข้อมูลหาย
- **`visitors` is a different measure** (tourists + excursionists) and is not the monthly target. MOTS publishes visitors monthly only from 2023, on the monthly (R) sheets still stored in staging/core. ผู้เยี่ยมเยือน (`visitors`) นับคนละแบบและไม่มีในตารางนี้
- Same-month rainfall is contemporaneous with the target; for a forecast, use lagged rain.
- 2025 MOTS figures may still be revised.

## Checks the builder runs

- Balanced 77 × 96 panel, unique keys, 2-digit `province_id`
- No nulls in `guest_arrivals_total` or the three rain columns
- 2019+ guest arrivals and rain equal the clean table exactly
- 2018 totals equal core exactly; 2018 thai/foreign are null
- Rain equals staging HII for every row
- `is_covid` matches `covid_window`
