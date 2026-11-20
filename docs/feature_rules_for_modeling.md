# กฎ feature สำหรับโมเดลรายเดือน

เอกสารนี้สำหรับ **data scientist** ที่สร้าง feature และเทรนโมเดลจาก `data/model/province_month_unified` อ่านคู่กับ `docs/project_spec.md`, `data/model/README.md` และ `reports/data_quality_audit.md` (รายการ 2, 8, 9, 11)

ไปป์ไลน์ไม่สร้าง lag หรือ rolling mean ให้ สร้างเองตามกฎนี้ ตัวช่วยที่ล็อกกฎ “ใช้ได้แค่เดือนก่อน t” อยู่ใน `src/model/leakage.py`

ทำนายเดือน `t` หมายถึง forecast ที่ตัดสินใจก่อนเดือน `t` จบ ใช้ข้อมูลถึง `t-1` เท่านั้น

## 1. Target

| รายการ | ค่า |
|---|---|
| ตาราง | `data/model/province_month_unified.parquet` (และ `.csv`) |
| Grain | 77 จังหวัด × เดือน, `province_id` เป็นสตริง 2 หลัก |
| ช่วง | **2018-01 .. 2025-12** (96 เดือน, 7,392 แถว, panel สมดุล) |
| Target | **`guest_arrivals_total`** (ผู้เข้าพักแรม, คน) |
| ไม่ใช่ target | `visitors_total` ไม่มีในตารางนี้ |

`visitors_total` อยู่ใน `data/core/province_month.parquet` เท่านั้น และเป็น **null ทุกแถวก่อน 2023-01** (4,620 แถว = 77 × 60 เดือน) ตั้งแต่ 2023-01 ถึง 2025-12 ครบ 2,772 แถว ห้ามเติม 0 ห้ามใช้เป็น target ของโมเดลรายเดือนชุดนี้

`guest_arrivals_thai` และ `guest_arrivals_foreign` ของเดือน `t` เป็นส่วนแยกของ target เดือนเดียวกัน ไม่ใช่ feature ของเดือน `t` **ปี 2018 ทั้งสองคอลัมน์เป็น null ทั้ง 924 แถว** เพราะ core มีแต่ยอดรวม ห้ามเติม 0

## 2. ห้ามใช้ยอดไตรมาสที่ครอบเดือน t

รายการ 9 ใน audit: ยอด `guest_arrivals` รายไตรมาสเท่ากับผลรวม 3 เดือนในไฟล์เดียวกัน ดังนั้นยอดไตรมาสนั้นรู้ค่าของเดือนที่กำลังทำนาย

ตรวจกับข้อมูลใน repo นี้แล้ว ส่วนต่างเป็น 0 ทุกแถว:

| แหล่ง | สิ่งที่เทียบ | ผล |
|---|---|---|
| `data/clean/mots_domestic_province/province_quarter` | `guest_arrivals_total` / `_thai` / `_foreign` เทียบผลรวม 3 เดือนใน `province_month` | 2,156 แถว (77 × 28 ไตรมาส, 2019-Q1 .. 2025-Q4) เท่ากันพอดี |
| ตารางเดียวกัน | `rain_sum_mm` เทียบผลรวม `rain_avg_mm` 3 เดือน | เท่ากันพอดี |
| `data/staging/mots/fact_tourism_long.parquet` | แถว `layout = quarterly_domestic`, `metric = guest_arrivals`, `area_level = province` | 7,392 แถวไตรมาส (2018–2025, `total` / `thai` / `foreign`) เท่ากับผลรวม 3 เดือนพอดี มีแถวรายเดือน 22,176 แถวที่นั่งคู่กับยอดไตรมาสซึ่งรวมเดือนนั้น |

`province_quarter` ไม่มีปี 2018 ยอดไตรมาส 2018 อยู่ใน staging และเท่ากับผลรวมเดือนเช่นกัน

กฎ: โมเดลที่ทำนายเดือน `t` **ห้ามใช้ยอดของไตรมาสที่บรรจุ `t`** และห้ามใช้ aggregate อื่นที่รวมเดือน `t` (ผลรวมทั้งประเทศของเดือน `t`, rolling ที่คลุม `t`, ผลรวมข้ามจังหวัดของเดือน `t`) ใช้ได้เฉพาะเดือน หรือไตรมาสทั้งก้อน ที่จบก่อน `t`

ตัวอย่างเชียงใหม่ (`province_id` `50`) 2024-Q2 จากตาราง unified ซึ่งตรงกับ `province_quarter`:

| เดือน | `guest_arrivals_total` |
|---|---:|
| 2024-04 | 617,125 |
| 2024-05 | 613,044 |
| 2024-06 | 580,513 |
| 2024-Q2 | **1,810,682** = ผลรวมพอดี |

ทำนาย **2024-05** แล้วใส่ยอด 2024-Q2 คือใส่ทั้งค่าเดือน พ.ค. และค่าเดือน มิ.ย. ที่ยังไม่เกิด ใช้ **2024-Q1** ได้ เพราะเดือนสุดท้ายคือ 2024-03 จะใช้ 2024-Q2 ได้เมื่อทำนายตั้งแต่ **2024-07** เป็นต้นไป

คอลัมน์อื่นใน `province_quarter` (`visitors_*`, `revenue_*`, `occupancy_rate_pct_total`, `rain_sum_mm`) ครอบ 3 เดือนเดียวกัน แม้ไม่ใช่ผลรวมของ target ก็ยังมีข้อมูลเดือน `t` ปนอยู่ ใช้กฎ “ไตรมาสต้องจบก่อน `t`” กับทุกคอลัมน์

ฟังก์ชัน `quarter_allowed_as_of` ใน `src/model/leakage.py` เป็นตัวตรวจนี้

## 3. กฎ leakage ทั่วไป

| สิ่งที่ใส่ในโมเดลของเดือน t | กฎ |
|---|---|
| Lag 1, 3, 12 ของ target หรือของฝน | แหล่งคือ `t-1`, `t-3`, `t-12` เท่านั้น |
| Rolling window | หน้าต่างจบที่ `t-1` เช่นค่าเฉลี่ย 3 เดือนคือ `t-3`, `t-2`, `t-1` |
| เดือนในปฏิทิน, `is_covid` ของเดือน t | ใช้ได้ รู้จากปฏิทิน ไม่ได้คำนวณจาก target |
| ฝนเดือนเดียวกับ t (`rain_avg_mm`, `rain_min_mm`, `rain_max_mm`) | **ค่าเริ่มต้นคือห้าม** ใช้ได้เฉพาะเมื่อทีมประกาศว่ารันนี้เป็น **nowcast** และแยกคะแนนออกจาก forecast |
| `visitors` ของเดือน t | ห้าม ทั้ง forecast และ nowcast เผยแพร่คู่กับผู้เข้าพัก |
| `guest_arrivals_thai` / `_foreign` ของเดือน t | ห้าม เป็นส่วนของ target |
| Scaler, encoder, target encoding | fit บนแถวของ training fold เท่านั้น ห้าม fit ทั้งแผง 2018–2025 |

**ฝนเดือนเดียวกันยังเป็น pending decision** (audit รายการ 2) ทุกแถว 7,392 มีฝนและ `guest_arrivals` ของเดือนเดียวกัน ใน core มี `visitors_total` เดือนเดียวกันอีก 2,772 แถว เอกสารนี้ไม่ได้เลือก nowcast ให้ ถ้ายังไม่มีการประกาศจากทีม ให้เทรนแบบ forecast (ฝนถึง `t-1`)

`visitors` ก่อน 2023-01 เป็น null ทั้งจังหวัด ถ้าจะใช้ lag ของ visitors ได้เฉพาะเดือนที่มีค่าจริง และต้องเป็นเดือนก่อน `t` ห้ามเติม 0

**Audit รายการ 8:** แถวรายเดือนปี 2023 ของ `visitors`, `guests`, `occupancy_pct`, `revenue_million_baht` ใน staging มาจากบล็อกปีก่อนในไฟล์ 2024 (`from_prior_year_column = true`) `guest_arrivals` ปี 2023 **ไม่ได้**มาจากบล็อกนั้น (`from_prior_year_column = false`, 2,772 แถว) target ในตาราง unified จึงไม่ใช่คอลัมน์ปีก่อน อย่าดึง visitors / occupancy / revenue / `guests` ปี 2023 จากบล็อกนั้นมาเป็นตัวเลขปฐมภูมิ

NSO ยังเป็น pending decision ใน `docs/project_spec.md` (แนวโน้มคือไม่ใช้) อย่า join เข้าตารางรายเดือนจนกว่าทีมจะรับ

## 4. COVID (2020-03 .. 2022-12)

`is_covid` เป็นจริงช่วง **2020-03 .. 2022-12** (34 เดือนต่อจังหวัด) ตาม `covid_window` ใน `config/sources.yaml` **เก็บแถวเหล่านี้ไว้** อย่าลบเพราะเป็นช่วงโควิด

ทางเลือกด้านล่าง **ยังเป็น pending decision ของทีม** เอกสารนี้ไม่ได้เลือกให้:

1. ใส่ `is_covid` เป็น feature (ธงปฏิทิน ใช้กับเดือน `t` ได้)
2. ใส่ `sample_weight` ให้แถวช่วงนี้
3. ตัดออกจาก training loss

ถ้าเลือกข้อ 3 ให้ **คำนวณ lag บนแผงเต็มก่อน แล้วค่อยตัดแถว** ถ้า drop ก่อน `shift` เดือนแรกหลังช่วงที่ถูกตัดจะดึงค่าจากเดือนที่ผิด

แถวที่ `guest_arrivals_total = 0` มี **27 แถว เป็นยอดจริง** ทั้งหมดอยู่ใน 2020-04, 2020-05 หรือ 2021-01 และ `is_covid = true` เก็บเป็น 0 ไม่ใช่ค่าหาย อย่าตัดทิ้งก่อนสร้าง lag

## 5. การแบ่งชุดข้อมูล

เดินตามเวลาเท่านั้น **ห้าม shuffle ห้าม random K-fold**

`docs/project_spec.md` กำหนดว่าโมเดลรันแบบ online มี streaming replay ปี 2025 คือสาย replay นั้น ไม่ใช่ test fold ที่สุ่ม

| ชุด | ช่วง | วิธีใช้ |
|---|---|---|
| ประวัติของ lag | 2018-01 .. 2019-12 | ไม่ใช้เป็น test เป็นอดีตของ origin แรก (lag 12 ของต้นปี 2019 ต้องมีปี 2018) |
| Backtest | 2020-01 .. 2023-12 | Rolling-origin, expanding window, ก้าวละ 3 เดือน |
| Holdout | 2024-01 .. 2024-12 | ล็อก วัดครั้งเดียวหลังเลือกโมเดลจาก backtest |
| Online replay | 2025-01 .. 2025-12 | เดินทีละเดือน ห้ามเทรนบนทั้งปีแล้วค่อยให้คะแนนย้อนหลัง |

Origin ของ backtest คือเดือนแรกของแต่ละบล็อก 3 เดือน: `2020-01`, `2020-04`, `2020-07`, `2020-10`, และแบบเดียวกันจนถึง `2023-10` (16 origin)

ที่ origin `T`:

- เทรนเฉพาะแถวที่ `year_month < T`
- ให้คะแนน `T`, `T+1`, `T+2`
- Feature ของเดือน `t` ในบล็อกใช้ข้อมูลถึง `t-1` ได้ รวม actual ของเดือนก่อนในบล็อก เพราะพอขึ้นเดือนใหม่ค่าเดือนก่อนถือว่ารู้แล้ว (one-step)
- **ห้าม refit ภายในบล็อก 3 เดือน** refit ได้ที่ origin ถัดไป
- Scaler และ encoder fit ใหม่ทุก origin บนแถวที่เทรนเท่านั้น

Holdout 2024 เทรนด้วย `year_month < 2024-01` ห้ามเอาคะแนนปี 2024 ไปจูน feature หรือ hyperparameter

ปี 2025: หลังแต่ละเดือนถูก replay แล้ว เดือนนั้นเข้า history ของเดือนถัดไปได้ Retrain เมื่อตรวจพบ drift ตาม project spec ห้ามใช้เดือน 2025 ที่ยังไม่ถึง

`scripts/model_readiness_check.py` แบ่ง train ≤ 2024 / test = 2025 และรายงาน MAPE เป็นการตรวจความพร้อมของข้อมูล **ไม่ใช่** โปรโตคอลในหน้านี้

## 6. Metrics

| Metric | บทบาท |
|---|---|
| **WAPE** | ตัวหลัก: `sum(\|y − ŷ\|) / sum(\|y\|)` |
| MAE | รายงานคู่กับ WAPE |
| RMSE | รายงานบนสเกลจำนวนคน สเกลนี้ถูกกรุงเทพฯ ดึง อย่าใช้แทน WAPE รายจังหวัด |
| MASE | MAE ของโมเดล หาร MAE ของ seasonal naive บนชุดเดียวกัน |
| MAPE | **ห้ามใช้เป็นตัวหลัก** แถวที่เป็น 0 ทำให้หารด้วยศูนย์ |

Seasonal naive ภายในจังหวัดคือ `ŷ_t = y_{t-12}` แถวที่ยังไม่มี `t-12` ไม่เข้าตัวหารของ MASE ตั้งแต่ 2019-01 เป็นต้นไป lag 12 มีค่า เพราะมียอดรวมปี 2018

รายงาน **WAPE รวมทั้งประเทศ และ WAPE รายจังหวัด**

Audit รายการ 11: กรุงเทพมหานคร (`province_id` `10`) เป็น **19.5%** ของ `guest_arrivals_total` ทั้งประเทศในช่วง 2018-01 .. 2025-12 จังหวัดที่มากที่สุดต่อค่ามัธยฐานของยอดจังหวัด ≈ 32.2 เท่า จังหวัดที่น้อยที่สุด ≈ 0.09 เท่า อย่าตัดจังหวัดใหญ่ทิ้งเพื่อให้ตัวอย่างสมดุล คะแนนที่พูลอย่างเดียวจะถูกกรุงเทพฯ ครอบงำ

## 7. Feature ที่อนุญาตและที่ห้าม

สถานะด้านล่างคือ **forecast** ของเดือน `t` แถว nowcast ใช้เฉพาะเมื่อทีมประกาศ และต้องไม่ปนกับตารางคะแนน forecast

| Feature | สถานะ | หมายเหตุ |
|---|---|---|
| `lag_1`, `lag_3`, `lag_12` ของ `guest_arrivals_total` | อนุญาต | `build_allowed_lag_frame` |
| Rolling ที่จบที่ `t-1` | อนุญาต | `rolling_source_months` |
| เดือนในปฏิทิน, `is_covid` ของเดือน `t` | อนุญาต | รู้ก่อนเดือนจบ |
| ไตรมาสที่เดือนสุดท้าย `< t` | อนุญาต | `quarter_allowed_as_of` |
| ฝน lag ถึง `t-1` | อนุญาต | |
| ฝนของเดือน `t` | ห้าม | nowcast เท่านั้น ยังไม่ตัดสิน |
| visitors ของเดือน `t` | ห้าม | เผยแพร่คู่กับ target |
| visitors ก่อน 2023-01 | null | ห้ามเติม 0 |
| `guest_arrivals_thai` / `_foreign` ของเดือน `t` | ห้าม | ส่วนของ target |
| thai / foreign ปี 2018 | null | ห้ามเติม 0 |
| ยอดไตรมาสที่ครอบ `t` | ห้าม | รายการ 9 |
| ผลรวมประเทศหรือข้ามจังหวัดของเดือน `t` | ห้าม | aggregate ที่รวม `t` |
| Target encoding ที่ fit ทั้งแผง | ห้าม | fit บน training fold |
| Join NSO | ห้าม | รอการตัดสินของทีม |

## Checklist ก่อนเทรน

- [ ] Target คือ `guest_arrivals_total` และไม่มี `visitors_total` ของเดือน `t` ใน feature
- [ ] ทุกคอลัมน์ที่มาจากค่าจริงชี้ไปเดือน `< t` แล้วเรียก `assert_no_future_leakage`
- [ ] Lag มาจาก `build_allowed_lag_frame` หรือเดือนจาก `source_months_for_lags` / `rolling_source_months`
- [ ] ไม่มีคอลัมน์ของไตรมาสที่ `quarter_allowed_as_of` เป็นเท็จ
- [ ] ไม่มีฝนของเดือน `t` เว้นแต่รายงานเขียนว่าเป็น nowcast
- [ ] ไม่ได้เติม 0 ให้ thai/foreign ปี 2018 หรือ visitors ก่อน 2023
- [ ] ถ้าตัดแถว COVID ออกจาก loss คำนวณ lag บนแผงเต็มก่อน
- [ ] 27 แถวที่เป็น 0 ยังเป็น 0
- [ ] Scaler, encoder, target encoding fit บน `year_month < origin` เท่านั้น
- [ ] ไม่ shuffle และไม่ใช้ random K-fold
- [ ] Backtest คือ 16 origin ปี 2020–2023 ก้าวละ 3 เดือน และไม่ refit ในบล็อก
- [ ] Holdout 2024 ยังไม่ถูกใช้จูนโมเดล
- [ ] ปี 2025 เดินทีละเดือน
- [ ] รายงาน WAPE รวมและรายจังหวัด, MAE, RMSE, MASE และไม่ได้ใช้ MAPE เป็นตัวหลัก

## โค้ดที่ใช้ล็อกกฎ

รันจาก root ของโปรเจกต์ (Windows หรือ POSIX path ใช้ `pathlib` ในโมดูลนี้ ไม่มี separator ฝังในโค้ด)

```python
from src.model.leakage import (
    assert_no_future_leakage,
    build_allowed_lag_frame,
    quarter_allowed_as_of,
    quarter_label,
    rolling_source_months,
    source_months_for_lags,
)

target = "2024-05"
source_months_for_lags(target)
# {'lag_1': '2024-04', 'lag_3': '2024-02', 'lag_12': '2023-05'}

rolling_source_months(target, 3)
# ['2024-02', '2024-03', '2024-04']  จบที่ t-1 ไม่มี 2024-05

quarter_label(target)  # '2024-Q2'
quarter_allowed_as_of("2024-Q2", target)  # False
quarter_allowed_as_of("2024-Q1", target)  # True

feats = build_allowed_lag_frame(panel, target)  # คอลัมน์ province_id, lag_1, lag_3, lag_12
assert_no_future_leakage(["2024-04", "2024-02", "2023-05"], target)
```

`panel` ต้องมี `province_id`, `year_month`, `guest_arrivals_total` และห้ามซ้ำคีย์จังหวัด-เดือน ค่าเดือน `t` ใน `panel` จะไม่ถูกคัดลอกเข้า lag ถ้าประวัติไม่พอ (เช่น lag 12 ของ 2018-01) ช่องนั้นเป็น null
