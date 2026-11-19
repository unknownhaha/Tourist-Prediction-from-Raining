# QA report – province-level tourism (MOTS) + rainfall (HII)

- Duplicate rows dropped from re-uploaded workbooks: quarterly 2,204, monthly 684
- Rainfall: 8,085 input rows, 0 unmapped, 0 province-months built from more than one row
- province_quarter: 2,156 rows, 77 provinces, 28 quarters
- province_month: 6,468 rows, 77 provinces, 84 months
- is_covid (2020-03 … 2022-12): quarterly 924 True / 1,232 False; monthly 2,618 True / 3,850 False
- Province values taken from the province sheet: 62,524; rebuilt from sub-areas: 0

## Sub-areas folded into their province

- ชลบุรี (sub_area): บางแสน ชลบุรี, พัทยา ชลบุรี
- ตราด (sub_area): เกาะกูด ตราด, เกาะช้าง ตราด, เกาะหมาก ตราด, เมือง ตราด
- นครราชสีมา (sub_area): ปากช่อง นครราชสีมา, วังน้ำเขียว, ในเมือง
- นครราชสีมา (subtotal): เมือง+วังน้ำเขียว, เมือง+วังน้ำเขียว1
- ประจวบคีรีขันธ์ (sub_area): 1.1เมือง ประจวบ, 1.1เมือง ประจวบคีรีขันธ์, 1.2หัวหิน ประจวบคีรีขันธ์, 1.2หัวหิน ประจวบฯ
- สุราษฎร์ธานี (sub_area): สุราษฎร์ธานี_เกาะพงัน, สุราษฎร์ธานี_เกาะสมุย, สุราษฎร์ธานี_เกาะเต่า, สุราษฎร์ธานี_เมือง
- เพชรบุรี (sub_area): 1.1อำเภอเมืองและอื่นๆ, 1.1อำเภอเมืองและอื่นๆจ.เพชรบุรี, 1.2 อำเภอชะอำ
- แม่ฮ่องสอน (sub_area): ปาย, เมือง แม่ฮองสอน

`subtotal` sheets are sums of other sub-areas and are not added again.

## Roll-up check: sum of sub-areas vs province sheet (additive metrics, all quarters)

| province | metric | rows | max abs diff | max diff % |
|---|---|---:|---:|---:|
| ชลบุรี | excursionists | 84 | 0.00 | 0.000 |
| ชลบุรี | guest_arrivals | 84 | 0.00 | 0.000 |
| ชลบุรี | revenue_visitor | 84 | 0.00 | 0.000 |
| ชลบุรี | rooms | 28 | 0.00 | 0.000 |
| ชลบุรี | tourists | 84 | 0.00 | 0.000 |
| ชลบุรี | visitors | 84 | 0.00 | 0.000 |
| ตราด | excursionists | 84 | 0.00 | 0.000 |
| ตราด | guest_arrivals | 84 | 0.00 | 0.000 |
| ตราด | revenue_visitor | 84 | 0.00 | 0.000 |
| ตราด | rooms | 28 | 0.00 | 0.000 |
| ตราด | tourists | 84 | 0.00 | 0.000 |
| ตราด | visitors | 84 | 0.00 | 0.000 |
| นครราชสีมา | excursionists | 84 | 0.00 | 0.000 |
| นครราชสีมา | guest_arrivals | 84 | 0.00 | 0.000 |
| นครราชสีมา | revenue_visitor | 84 | 0.00 | 0.000 |
| นครราชสีมา | rooms | 28 | 0.00 | 0.000 |
| นครราชสีมา | tourists | 84 | 0.00 | 0.000 |
| นครราชสีมา | visitors | 84 | 0.00 | 0.000 |
| ประจวบคีรีขันธ์ | excursionists | 84 | 0.00 | 0.000 |
| ประจวบคีรีขันธ์ | guest_arrivals | 84 | 0.00 | 0.000 |
| ประจวบคีรีขันธ์ | revenue_visitor | 84 | 0.00 | 0.000 |
| ประจวบคีรีขันธ์ | rooms | 28 | 0.00 | 0.000 |
| ประจวบคีรีขันธ์ | tourists | 84 | 0.00 | 0.000 |
| ประจวบคีรีขันธ์ | visitors | 84 | 0.00 | 0.000 |
| สุราษฎร์ธานี | excursionists | 84 | 0.00 | 0.000 |
| สุราษฎร์ธานี | guest_arrivals | 84 | 0.00 | 0.000 |
| สุราษฎร์ธานี | revenue_visitor | 84 | 0.00 | 0.000 |
| สุราษฎร์ธานี | rooms | 28 | 0.00 | 0.000 |
| สุราษฎร์ธานี | tourists | 84 | 0.00 | 0.000 |
| สุราษฎร์ธานี | visitors | 84 | 0.00 | 0.000 |
| เพชรบุรี | excursionists | 84 | 0.00 | 0.000 |
| เพชรบุรี | guest_arrivals | 84 | 0.00 | 0.000 |
| เพชรบุรี | revenue_visitor | 84 | 0.00 | 0.000 |
| เพชรบุรี | rooms | 28 | 0.00 | 0.000 |
| เพชรบุรี | tourists | 84 | 0.00 | 0.000 |
| เพชรบุรี | visitors | 84 | 0.00 | 0.000 |
| แม่ฮ่องสอน | excursionists | 84 | 0.00 | 0.000 |
| แม่ฮ่องสอน | guest_arrivals | 84 | 0.00 | 0.000 |
| แม่ฮ่องสอน | revenue_visitor | 84 | 0.00 | 0.000 |
| แม่ฮ่องสอน | rooms | 28 | 0.00 | 0.000 |
| แม่ฮ่องสอน | tourists | 84 | 0.00 | 0.000 |
| แม่ฮ่องสอน | visitors | 84 | 0.00 | 0.000 |

## Ignored sheets that contain tourism tables (region / country totals)

ประเทศ, ประเทศ2562, ประเทศ2566, ประเทศ2567, ภาคกลาง, ภาคกลาง2562, ภาคกลาง2566, ภาคกลาง2567, ภาคตะวันตก, ภาคตะวันตก2562, ภาคตะวันตก_2562Q1-Q4, ภาคตะวันออก, ภาคตะวันออก2562, ภาคตะวันออก2566, ภาคตะวันออก2567, ภาคตะวันออกเฉียงเหนือ, ภาคตะวันออกเฉียงเหนือ2566, ภาคตะวันออกเฉียงเหนือ2567, ภาคอีสาน, ภาคอีสาน2566, ภาคเหนือ, ภาคเหนือ2562, ภาคเหนือ2563, ภาคเหนือ2564, ภาคเหนือ2566, ภาคเหนือ2567, ภาคใต้, ภาคใต้2566, ภาคใต้2567, รวมปี ภาคใต้_2562 Q1-Q4, รวมภาคกลาง(ไม่รวมกทม.), รวมภาคกลาง(ไม่รวมกทม.)2566, รวมภาคกลาง62(ไม่รวมกทม.), รวมภาคอีสาน2562, รวมภาคอีสาน2562_Q1-Q4, รวมภาคเหนือ2562_Q1-Q4, รวมภาคใต้2562, สรุป61

## Notes

- v2022_11aeb83a-6afc-4c36-82aa-6ce48b273d4c.xlsx: monthly (R) report – different layout, not part of the Q1-Q4 series
