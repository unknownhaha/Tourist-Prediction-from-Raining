# ส่งต่อ: Data Science, ML, QA

## ตารางเริ่มต้น

ใช้ **`data/core/province_month.parquet`**: โครงสร้าง **77 × 92** เต็ม (2018-01–2025-08 ค.ศ.) ฝน HII **`avg_rain_mm`** MOTS **`guest_arrivals`** (รายเดือน จากไฟล์รายไตรมาสภายในประเทศเป็นหลัก) **`visitors_total`** (รายเดือน จากรายงาน `(R)` ของ MOTS โดยทั่วไป 2023-01–2025-08) และ **`is_covid`**

แผ่นพื้นที่ย่อย (พัทยา เกาะช้าง ฯลฯ) **ไม่** รวมในตารางนี้ ยังอยู่ในตาราง long ที่ `area_level = sub_area`

## ตารางอื่น

- **`fact_tourism_long`**: ทุก metric ของ MOTS ประเภทนักท่องเที่ยว และแถวรายไตรมาส
- **`fact_tourism_province_month`**: ข้อเท็จจริงรายเดือนระดับจังหวัด (หนึ่งแถวต่อ metric × tourist_type)
- **`fact_accommodation_nso`**: snapshot สำรวจ NSO — อย่าใช้เป็นรายเดือนโดยไม่มีกฎ join ที่อนุมัติ
- **`dim_month` / `dim_province`**: มิติ (`region`, `province_en` บนจังหวัด)

## QA

```powershell
python -m src.qa.checks
python -m src.qa.coverage_report
python -m pytest tests/
```

ตรวจ **`data/staging/mots/quarantine/quarantine.csv`**, **`unmapped_provinces.csv`**, และ **`conflicts.csv`** เมื่อความครอบคลุมต่ำ สรุป manifest ใช้ **สถานะล่าสุดต่อ URL**

ทางเลือก: ย้ายไฟล์ crawl 2018 ที่ไม่เกี่ยวข้อง:

```powershell
python -m src.ingest.mots_cleanup
```

## ไม่มี feature engineering

ไปปไลน์นี้ไม่สร้าง lag, ค่าเฉลี่ยเคลื่อนที่ หรือฟีเจอร์โมเดล
