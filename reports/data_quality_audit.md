# Data quality audit

_Generated 2026-11-18 16:46 UTC by `python -m src.qa.data_quality_audit`. Read-only: no source values, Parquet tables, or pipeline code were changed._

## How to regenerate

```bash
python -m src.qa.data_quality_audit
```

Use `--skip-network` to skip the connectivity probe. The script overwrites this file.

## What was audited

- Modeling window in `config/sources.yaml`: **2018-01** through **2025-08** (92 months). Expected province-month grid: **77 × 92 = 7084**.
- COVID window used by `is_covid`: **2020-03** through **2022-12**.
- This audit reads files already on disk. It does not download sources or rebuild Parquet.
- `data/raw/` file count: **0**. Raw HII, MOTS, and NSO downloads are not in this checkout (the directory is gitignored).

### Connectivity from this run

A short GET (8s timeout, 64-byte read) of the configured source URLs. This does not save files and is not a substitute for ingest.

- HII rainfall CSV: `TimeoutError: The read operation timed out` — `https://data.hii.or.th/dataset/38cbfb06-e174-4653-accd-d491121ba752/resource/120aba77-b0a7-44c0-8043-91124dac45ed/download/spatial-rain-hii.csv`
- NSO survey file API (first round): `HTTP 200` — `https://www.nso.go.th/nsoweb/nso/getSurveyFile?code=MD&subcode=sg&type_menu=2`
- MOTS category RSS (2018): `HTTP 200` — `https://www.mots.go.th/api/rss/category/497`

## Summary by dimension

Each finding has one primary dimension. Counts are the number of finding rows, not the number of bad cells.

| Dimension | Findings | High | Medium | Low | Info | First item |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Completeness | 5 | 2 | 0 | 1 | 2 | visitors_total is null for every province-month before 2023-01 |
| Uniqueness | 1 | 0 | 0 | 1 | 0 | Province crosswalk contains exact duplicate rows |
| Validity | 2 | 0 | 0 | 2 | 0 | The visitors_total coverage floor passes even though an entire period is null |
| Accuracy / plausibility | 7 | 0 | 0 | 1 | 6 | A few province-months have extreme but internally consistent rainfall |
| Consistency | 2 | 0 | 1 | 0 | 1 | MOTS province labels vary (abbreviations, spelling, year suffixes, รวม prefixes) even though province_id is filled |
| Timeliness / freshness | 3 | 0 | 1 | 2 | 0 | The modeling table stops before the rainfall extract and well before the audit clock |
| Integrity | 2 | 1 | 0 | 1 | 0 | Join match rates onto the province-month spine |
| Conformity / format | 4 | 0 | 3 | 1 | 0 | MOTS source_file uses Windows backslashes, so the path does not resolve on Linux |
| Temporal / stationarity | 2 | 0 | 0 | 0 | 2 | COVID-period guest_arrivals drop is a real level shift |
| Data leakage | 3 | 1 | 2 | 0 | 0 | Same-month rainfall and visitors are contemporaneous with guest_arrivals |
| Coverage imbalance | 1 | 0 | 1 | 0 | 0 | Guest volume is highly uneven across provinces; visitor coverage is not |
| Lineage / reproducibility | 7 | 3 | 2 | 1 | 1 | Raw source files are not in the checkout, so workbook- and CSV-level cleaning cannot be rechecked here |

## Top issues

- **[high]** visitors_total is null for every province-month before 2023-01 — `data/core/province_month.parquet` / `visitors_total` — 4620 nulls (65.22% of 7084); 4620 of 4620 rows before 2023-01; 0 nulls from 2023-01 through the window end
- **[high]** Same-month rainfall and visitors are contemporaneous with guest_arrivals — `data/core/province_month.parquet` / `avg_rain_mm, visitors_total, guest_arrivals` — 7084 rows have same-month rain and guests; 2464 also have same-month visitors_total
- **[high]** NSO accommodation never becomes province-level measures — `data/staging/nso/fact_accommodation_nso_inventory.parquet` / `file_path, status` — 338 workbook rows; survey years CE [2018, 2020, 2022]; status counts {'extracted': 338}; 0 numeric measure columns; 0 rows joined to province_month
- **[high]** Join match rates onto the province-month spine — `data/core/province_month.parquet` / `province_id, year_month` — rainfall 7084/7084 (100.00%); guest_arrivals 7084/7084 (100.00%); visitors_total 2464/7084 (34.78%); NSO province_id/year_month columns present 0/2; staged rainfall rows outside the spine months 1001; staged MOTS rows outside the spine months 3541; rainfall province ids missing from the spine 0
- **[high]** Raw source files are not in the checkout, so workbook- and CSV-level cleaning cannot be rechecked here — `data/raw/` / `(directory)` — 0 files on disk
- **[high]** Every MOTS workbook cited by the staged extract is missing from this checkout — `data/staging/mots/fact_tourism_long.parquet` / `source_file` — 51 of 51 distinct source paths are absent
- **[high]** NSO workbooks listed in the inventory are not in this checkout — `data/staging/nso/fact_accommodation_nso_inventory.parquet` / `file_path` — 338 of 338 paths missing under the repo root
- **[medium]** All 2023 monthly visitors, guests, occupancy, and revenue come from a prior-year block in the 2024 workbook — `data/staging/mots/fact_tourism_long.parquet` / `from_prior_year_column` — 7392 rows; years [2023]; metrics ['guests', 'occupancy_pct', 'revenue_million_baht', 'visitors']
- **[medium]** Quarterly guest_arrivals equal the sum of the three months in the same workbook — `data/staging/mots/fact_tourism_long.parquet` / `quarter, value` — 22176 monthly rows sit next to a quarter total that includes them
- **[medium]** The modeling table stops before the rainfall extract and well before the audit clock — `data/core/province_month.parquet` / `year_month` — province_month ends 2025-08 (15 months before 2026-11); staged HII ends 2026-09 (13 months later)
- **[medium]** Guest volume is highly uneven across provinces; visitor coverage is not — `data/core/province_month.parquet` / `guest_arrivals` — กรุงเทพมหานคร is 19.5% of national guest_arrivals; largest/median province total 31.8x; smallest/median 0.09x; visitors_total non-null months per province min 32, max 32
- **[medium]** MOTS province labels vary (abbreviations, spelling, year suffixes, รวม prefixes) even though province_id is filled — `data/staging/mots/fact_tourism_long.parquet` / `province_th_raw` — 4656 of 66384 province rows (7.01%); 13 distinct non-canonical labels
- **[medium]** MOTS source_file uses Windows backslashes, so the path does not resolve on Linux — `data/staging/mots/fact_tourism_long.parquet` / `source_file` — 73080 of 73080 staged rows
- **[medium]** Core MOTS lineage paths also use Windows backslashes — `data/core/fact_tourism_long.parquet` / `source_file` — 69539 of 69539 rows
- **[medium]** NSO inventory paths use Windows backslashes and the archives are not on disk — `data/staging/nso/fact_accommodation_nso_inventory.parquet` / `file_path` — 338 of 338 paths
- **[medium]** Latest manifest status is still error for three MOTS news pages — `data/manifest/manifest_downloads.csv` / `status, news_url` — 3 latest rows with status=error
- **[medium]** The configured HII rainfall CSV did not return a body in this run — `config/sources.yaml` / `hii.csv_url` — 1 URL

## Findings

Severity: **high** = would bias or invent the modeling table if handled wrongly; **medium** = dirty labels, paths, or provenance that should be fixed before the next rebuild; **low** = small type or coverage decisions; **info** = a real pattern that should be kept.

| Dimension | Severity | Issue | File path | Column | Count | Example | Recommended fix |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Completeness | high | visitors_total is null for every province-month before 2023-01 | `data/core/province_month.parquet` | `visitors_total` | 4620 nulls (65.22% of 7084); 4620 of 4620 rows before 2023-01; 0 nulls from 2023-01 through the window end | กรุงเทพมหานคร 2018-01: visitors_total is null, guest_arrivals=3228292 | Keep the nulls. The monthly (R) visitor series starts at 2023-01 and is complete after that. Use guest_arrivals for 2018-01 onward. Do not fill visitors_total with 0. |
| Data leakage | high | Same-month rainfall and visitors are contemporaneous with guest_arrivals | `data/core/province_month.parquet` | `avg_rain_mm, visitors_total, guest_arrivals` | 7084 rows have same-month rain and guests; 2464 also have same-month visitors_total | a row for 2024-12 contains that month's rain, visitors, and guest_arrivals together | For a forecast of month t decided before t ends, use features from months through t-1 only. Same-month avg_rain_mm is valid only for a nowcast, and the training label must say so. Do not use same-month visitors_total to predict same-month guest_arrivals; they are published together. |
| Completeness | high | NSO accommodation never becomes province-level measures | `data/staging/nso/fact_accommodation_nso_inventory.parquet` | `file_path, status` | 338 workbook rows; survey years CE [2018, 2020, 2022]; status counts {'extracted': 338}; 0 numeric measure columns; 0 rows joined to province_month | data\raw\nso\MD_2561\20230430150850_82998\ตารางสถิติในรูปแบบ  Excel\1. Whole Kingdom\regtab01n.xls | Parse province tables out of the Excel workbooks after the RAR archives are restored. Survey years are 2018, 2020, and 2022 only, so do not spread one survey across every month. Agree a join rule before adding columns to province_month. |
| Integrity | high | Join match rates onto the province-month spine | `data/core/province_month.parquet` | `province_id, year_month` | rainfall 7084/7084 (100.00%); guest_arrivals 7084/7084 (100.00%); visitors_total 2464/7084 (34.78%); NSO province_id/year_month columns present 0/2; staged rainfall rows outside the spine months 1001; staged MOTS rows outside the spine months 3541; rainfall province ids missing from the spine 0 | visitors_total matches only 2023-01 onward; NSO inventory has no province_id and cannot be joined | Keep the left-join spine so unmatched visitors stay null. Do not inner-join or the pre-2023 rows disappear. Parse NSO to province_id before any accommodation join. Sub-area MOTS rows stay off this spine until a parent-province rule exists. |
| Lineage / reproducibility | high | Raw source files are not in the checkout, so workbook- and CSV-level cleaning cannot be rechecked here | `data/raw/` | `(directory)` | 0 files on disk | Manifest paths such as data/raw/hii/spatial-rain-hii.csv and data/raw/mots/.../*.xlsx are absent | Restore `data/raw/` from the ingest machine before changing parsers. Audit staged Parquet in the meantime. Do not invent replacement raw rows. |
| Lineage / reproducibility | high | Every MOTS workbook cited by the staged extract is missing from this checkout | `data/staging/mots/fact_tourism_long.parquet` | `source_file` | 51 of 51 distinct source paths are absent | data/raw/mots/2018/531/_cat_531/article_20191002150247.xlsx | Re-run the MOTS crawler where Playwright can reach www.mots.go.th if the workbooks themselves need re-audit. Staged values are still auditable. |
| Lineage / reproducibility | high | NSO workbooks listed in the inventory are not in this checkout | `data/staging/nso/fact_accommodation_nso_inventory.parquet` | `file_path` | 338 of 338 paths missing under the repo root | data/raw/nso/MD_2561/20230430150850_82998/ตารางสถิติในรูปแบบ  Excel/1. Whole Kingdom/regtab01n.xls | Re-download the three MD RAR archives and extract them with 7-Zip or UnRAR before any cell-level NSO audit. |
| Data leakage | medium | All 2023 monthly visitors, guests, occupancy, and revenue come from a prior-year block in the 2024 workbook | `data/staging/mots/fact_tourism_long.parquet` | `from_prior_year_column` | 7392 rows; years [2023]; metrics ['guests', 'occupancy_pct', 'revenue_million_baht', 'visitors'] | data\raw\mots\2024\760\_cat_760\v2022_17579087754404Lij4Liy4Lii4LiH4Liy4LiZ4Lij4Liy4Lii4LmA4LiU4Li34Lit4LiZ4Lih4LiB4Lij4Liy4LiE4LihIC0g4LiY4Lix4LiZ4Lin4Liy4LiE4LihIOC4m+C4tSAyNTY3Lnhsc3g=.xlsx | Keep the 2023 values — they are the only monthly copy, and guest totals match guest_arrivals. Carry the prior-year flag onto province_month for visitors_total in 2023 so it is not treated as a primary 2023 release. guest_arrivals itself is not from a prior-year column. Search for a standalone 2023 (R) workbook before replacing these cells. |
| Data leakage | medium | Quarterly guest_arrivals equal the sum of the three months in the same workbook | `data/staging/mots/fact_tourism_long.parquet` | `quarter, value` | 22176 monthly rows sit next to a quarter total that includes them | 2018-Q1 total is the sum of Jan+Feb+Mar guest_arrivals in the same source file (0 mismatches) | Do not feed the quarter total into a model that predicts one of that quarter's months. The audit already checks that the quarter matches the monthly sum within 0.1%. |
| Timeliness / freshness | medium | The modeling table stops before the rainfall extract and well before the audit clock | `data/core/province_month.parquet` | `year_month` | province_month ends 2025-08 (15 months before 2026-11); staged HII ends 2026-09 (13 months later) | province_month ends 2025-08 (15 months before 2026-11); HII staged rainfall ends 2026-09 (2 months before 2026-11); MOTS monthly (R) staged ends 2025-12 (11 months before 2026-11); NSO survey year ends 2022-12 (47 months before 2026-11) | Treat 2025-08 as a declared label window, not as 'latest available'. Extend config/sources.yaml and rebuild core when models need the rainfall already staged through the HII end month. NSO stays a biennial survey (2018, 2020, 2022); do not expect a monthly accommodation series from it. |
| Coverage imbalance | medium | Guest volume is highly uneven across provinces; visitor coverage is not | `data/core/province_month.parquet` | `guest_arrivals` | กรุงเทพมหานคร is 19.5% of national guest_arrivals; largest/median province total 31.8x; smallest/median 0.09x; visitors_total non-null months per province min 32, max 32 | กรุงเทพมหานคร 195,585,211 vs median province 6,145,083 | Fit province effects or a log target. Score errors per province, not only on the pooled sum, or Bangkok dominates. Do not drop large provinces to balance the sample. The missing visitors_total cells are the same 60 months in every province. |
| Consistency | medium | MOTS province labels vary (abbreviations, spelling, year suffixes, รวม prefixes) even though province_id is filled | `data/staging/mots/fact_tourism_long.parquet` | `province_th_raw` | 4656 of 66384 province rows (7.01%); 13 distinct non-canonical labels | 1.รวมนครราชสีมา (576 rows); รวมแม่ฮ่องสอน (576 rows); รวมสุราษฎร์ธานี (576 rows) | Add canonical `province_th` from `dim_province` beside `province_th_raw`. Keep the raw label for lineage. Do not create new province ids for aliases such as กรุงเทพฯ, ศรีษะเกษ, or ประจวบศิรีขันธ์. |
| Conformity / format | medium | MOTS source_file uses Windows backslashes, so the path does not resolve on Linux | `data/staging/mots/fact_tourism_long.parquet` | `source_file` | 73080 of 73080 staged rows | data\raw\mots\2018\531\_cat_531\article_20191002150247.xlsx | Write POSIX paths in the manifest and Parquet (`data/raw/mots/...`). The same pattern is copied into `data/core/fact_tourism_long.parquet`. |
| Conformity / format | medium | Core MOTS lineage paths also use Windows backslashes | `data/core/fact_tourism_long.parquet` | `source_file` | 69539 of 69539 rows | data\raw\mots\2018\531\_cat_531\article_20191002150247.xlsx | Normalize separators when core tables are next rebuilt. Do not hand-edit the Parquet. |
| Conformity / format | medium | NSO inventory paths use Windows backslashes and the archives are not on disk | `data/staging/nso/fact_accommodation_nso_inventory.parquet` | `file_path` | 338 of 338 paths | data\raw\nso\MD_2561\20230430150850_82998\ตารางสถิติในรูปแบบ  Excel\1. Whole Kingdom\regtab01n.xls | Store POSIX paths. The same paths are copied to `data/core/fact_accommodation_nso.parquet`. |
| Lineage / reproducibility | medium | Latest manifest status is still error for three MOTS news pages | `data/manifest/manifest_downloads.csv` | `status, news_url` | 3 latest rows with status=error | https://www.mots.go.th/news/1000011; https://www.mots.go.th/news/12013; https://www.mots.go.th/news/1000003 | Retry these three news URLs and confirm they are not province-month workbooks. Historical error rows that later succeeded are not current failures. Modeled guest_arrivals and visitors_total are already complete inside their published windows. |
| Lineage / reproducibility | medium | The configured HII rainfall CSV did not return a body in this run | `config/sources.yaml` | `hii.csv_url` | 1 URL | TimeoutError: The read operation timed out | Retry the download from a network that can read data.hii.or.th before refreshing rainfall. The staged extract already on disk remains the audited copy. Do not replace it with an empty file. |
| Conformity / format | low | Rainfall carries sub-0.01 mm binary residue from the upstream file | `data/core/province_month.parquet` | `avg_rain_mm` | 6805 of 7084 values differ from a 2-decimal rounding | 142.119137 mm | Optional: round millimeters to 2 decimals at staging for display. Leave full precision for modeling. |
| Accuracy / plausibility | low | A few province-months have extreme but internally consistent rainfall | `data/core/province_month.parquet` | `avg_rain_mm` | 61 per-province \|z\|>3 flags in 46 provinces; per-province IQR flags 102; max avg_rain_mm=1452.6 | นราธิวาส 2024-11: min=1264.1, avg=1452.6, max=1642.3 mm | Keep the values. They sit inside min_rain_mm..max_rain_mm, are not negative, and match known wet-season peaks in the far south. Add an extreme-weather indicator if a model needs it. Do not clip to a global IQR fence. |
| Uniqueness | low | Province crosswalk contains exact duplicate rows | `config/province_crosswalk.csv` | `alias` | 4 extra copies; aliases ['กรุงเทพมหานคร', 'กรุงเทพฯ', 'ประจวบคีรีขันธ์', 'ภูเก็ต'] | กรุงเทพมหานคร | Drop exact duplicate rows. They map to the same province_id, so this is not a conflicting code. Keep genuine aliases (กรุงเทพฯ, ศรีษะเกษ, ประจวบศิรีขันธ์). |
| Validity | low | The visitors_total coverage floor passes even though an entire period is null | `config/data_contracts.yaml` | `province_month.coverage.visitors_total_min_non_null` | floor 2400, actual non-null 2464, nulls before 2023-01 4620, nulls from 2023-01 onward 0 | a hole inside 2023-01..2025-08 would still pass as long as 2400 cells stay filled | Add a contract rule that visitors_total is null exactly when year_month < 2023-01, and non-null on every later spine row. |
| Validity | low | Staged HII province_id is an integer while core and MOTS use text ids | `data/staging/hii/fact_rainfall.parquet` | `province_id` | dtype=int64; 77 distinct ids | staging value 10 (int) vs core text id '10' | Cast province_id to a stripped string in `src/staging/hii.py` so joins do not depend on a later cast in build_core. |
| Timeliness / freshness | low | Staged HII rainfall continues past the modeling window and is dropped by build_core | `data/staging/hii/fact_rainfall.parquet` | `year_month` | 1001 rows (77 provinces × 13 months) | 2025-09 … 2026-09 | Keep the staged rows. Extend `config/sources.yaml` date_window and rebuild core only if models should use months after 2025-08. Do not delete the extra months from staging. |
| Timeliness / freshness | low | Staged MOTS has province-months after 2025-08 that core drops | `data/staging/mots/fact_tourism_long.parquet` | `year_month` | 3541 rows; months 2025-09 … 2025-12 | 2025-09=883, 2025-10=886, 2025-11=886, 2025-12=886 | Keep them in staging. Extend the date window only if 2025-09 through 2025-12 should enter province_month. |
| Completeness | low | The monthly guests metric has no Thai/foreign split | `data/staging/mots/fact_tourism_long.parquet` | `tourist_type` | 2772 guests rows, all tourist_type=total | metric=guests, tourist_type=total | Confirm the (R) sheet publishes only จำนวนผู้เข้าพัก as a total. Do not split it with the visitor Thai/foreign ratio. |
| Integrity | low | Resort and sub-area sheets have a null province_id by design | `data/staging/mots/fact_tourism_long.parquet` | `province_id, province_th_raw` | 6696 rows, 14 labels | 1.1อำเภอเมืองและอื่นๆจ.เพชรบุรี, 1.1เมือง ประจวบคีรีขันธ์, 1.2หัวหิน ประจวบคีรีขันธ์, บางแสน ชลบุรี | Leave these rows off the 77-province spine. Map a parent province only after an approved rule (the label often contains the parent, for example พัทยา ชลบุรี). |
| Lineage / reproducibility | low | A null-URL NSO extract_failed row is still the latest status for its own manifest key | `data/manifest/manifest_downloads.csv` | `status, file_url` | 1 latest non-ok NSO rows; inventory status is extracted for every workbook | RAR extraction requires 7-Zip (7z.exe) or UnRAR on PATH. Install from https://www.7-zip.org/ | Key manifest summaries by local_path when file_url is null. Do not re-extract solely because of this row: the three RAR downloads have status ok and the inventory lists 338 extracted workbooks. |
| Completeness | info | Quarantine holds national MOTS workbooks that are out of the province grid on purpose | `data/staging/mots/quarantine/quarantine.csv` | `reason` | 57 files; reasons {'out_of_scope_national': 57} | data\raw\mots\2018\500\_cat_500\article_20180221145729.xlsx | Leave these files quarantined. Do not parse national totals into the 77-province table. |
| Completeness | info | Null year_month on quarterly MOTS rows is the quarterly grain, not a missing month | `data/staging/mots/fact_tourism_long.parquet` | `year_month, quarter` | 25380 rows with quarter set and year_month null | 2018-Q1 guest_arrivals | Do not forward-fill quarterly rows onto months. Use the monthly guest_arrivals rows, which already sum to the quarter within 0.1%. |
| Temporal / stationarity | info | COVID-period guest_arrivals drop is a real level shift | `data/core/province_month.parquet` | `guest_arrivals, is_covid` | is_covid true on 2618 of 7084 rows; national sum 2020-04=307,277 vs 2024-12=16,492,784 | Median province-month guest_arrivals 2019=76,612, 2021=15,051 | Keep is_covid as a model feature. Do not winsorize or drop 2020-03 through 2022-12 to make the series look stationary. |
| Accuracy / plausibility | info | Per-province IQR flags on guest_arrivals are COVID troughs and pre-2020 seasonal peaks | `data/core/province_month.parquet` | `guest_arrivals` | 388 per-province IQR flags across 43 provinces; 358 inside is_covid; 30 outside is_covid | Outside-COVID example (high side): สุโขทัย 2019-12 | Do not cap or delete these rows. A global IQR treats Bangkok as an outlier because the cross-province distribution is skewed. Global IQR flags on guest_arrivals: 910. Global \|z\|>3: 142. Use per-province models instead of a global cap. Non-COVID flags observed on the high side: 30. |
| Accuracy / plausibility | info | guest_arrivals zeros are published COVID-shutdown months, not missing values | `data/core/province_month.parquet` | `guest_arrivals` | 27 zeros; 22 of them are 2020-04; 0 fall outside is_covid | 2020-04 accounts for 22 zeros (for example สมุทรปราการ 2020-04) | Keep 0. It is a published zero, not a null. Do not impute these cells. |
| Consistency | info | guest_arrivals and visitors_total measure different things and should not be forced equal | `data/core/province_month.parquet` | `guest_arrivals, visitors_total` | 2464 overlapping rows; ratio guest/visitors min=0.062, median=0.453, max=0.948 | ฉะเชิงเทรา 2023-05: guest_arrivals=28245, visitors_total=458012, ratio=0.062 | Keep both columns. Where both exist, the monthly guests metric matches guest_arrivals exactly; visitors are a wider count. |
| Accuracy / plausibility | info | Month-to-month guest_arrivals moves of more than 5x sit inside the COVID window | `data/core/province_month.parquet` | `guest_arrivals` | 172 province-months with previous > 1000 and ratio > 5 or < 0.2; 0 of them fall outside 2020-03..2022-12 | สมุทรปราการ 2020-04: 19967 -> 0 (ratio 0.000) | Keep the jumps. They are the shutdown and reopening, not corrupted cells. A drift detector trained on 2018-2019 will alarm through 2021; start the reference window at 2023-01 for post-COVID monitoring, or condition on is_covid. |
| Temporal / stationarity | info | Rainfall is strongly seasonal; guest_arrivals move much less across calendar months | `data/core/province_month.parquet` | `avg_rain_mm, guest_arrivals, month` | rainfall median index month 12=0.02 vs month 9=2.45; guest_arrivals median index 0.83..1.28; guest median pre=75,154, covid=31,640 (0.42x pre), post=80,280 (1.07x pre) | driest calendar month index 0.02, wettest 2.45 | Put calendar month, or a wet-season flag, in any rainfall model. For drift detection, compare a month with the same calendar month in the reference set. The COVID level shift (about 0.42x the pre-2020 median) is a structural break, not a sensor fault. A reference set of only 2018-2019 will flag 2020-2022 as drift by design. |
| Accuracy / plausibility | info | Revenue per visitor stays in a baht range, with no thousands-vs-persons break | `data/core/fact_tourism_province_month.parquet` | `value` | 2464 province-months; baht per visitor min 957, median 2,412, max 43,149; 0 outside 50..500,000 baht | low หนองบัวลำภู 2023-02 957 baht; high ภูเก็ต 2024-07 43,149 baht | Keep million_baht as the revenue unit. Phuket peak months near 40,000 baht per visitor are resort spend, not a unit error. Do not multiply or divide the column by 1,000. |
| Accuracy / plausibility | info | Per-province \|z\|>3 on visitors_total is rare; global fences only flag large provinces | `data/core/province_month.parquet` | `visitors_total` | 5 per-province \|z\|>3 flags; global IQR flags 234 of 2464 non-null rows | กรุงเทพมหานคร 2024-12 | Do not winsorize visitors_total. Bangkok's magnitude is the real size of the capital, not a bad row. |
| Accuracy / plausibility | info | Zero rainfall is a dry-season measurement (min = max = avg = 0), not a null coded as zero | `data/core/province_month.parquet` | `avg_rain_mm` | 200 province-months | month 01=112, month 12=76, month 02=11, month 03=1 | Keep numeric 0. Do not convert dry-season zeros to null. |
| Lineage / reproducibility | info | Core dedupe is deterministic only while duplicate keys stay at zero | `src/core/build_core.py` | `from_prior_year_column` | 0 duplicate province-month-metric-type keys in the published fact table | build_fact_tourism_province_month sorts by from_prior_year_column and keeps the first row | Add source_file and sheet as tie-breaks before drop_duplicates. Today the output does not depend on parquet row order because no key is duplicated. A second current-year copy of the same key would. |

## Prioritized cleaning to-do

1. **[high / Completeness]** visitors_total is null for every province-month before 2023-01 (`data/core/province_month.parquet`, `visitors_total`, 4620 nulls (65.22% of 7084); 4620 of 4620 rows before 2023-01; 0 nulls from 2023-01 through the window end). Keep the nulls. The monthly (R) visitor series starts at 2023-01 and is complete after that. Use guest_arrivals for 2018-01 onward. Do not fill visitors_total with 0.
2. **[high / Data leakage]** Same-month rainfall and visitors are contemporaneous with guest_arrivals (`data/core/province_month.parquet`, `avg_rain_mm, visitors_total, guest_arrivals`, 7084 rows have same-month rain and guests; 2464 also have same-month visitors_total). For a forecast of month t decided before t ends, use features from months through t-1 only. Same-month avg_rain_mm is valid only for a nowcast, and the training label must say so. Do not use same-month visitors_total to predict same-month guest_arrivals; they are published together.
3. **[high / Completeness]** NSO accommodation never becomes province-level measures (`data/staging/nso/fact_accommodation_nso_inventory.parquet`, `file_path, status`, 338 workbook rows; survey years CE [2018, 2020, 2022]; status counts {'extracted': 338}; 0 numeric measure columns; 0 rows joined to province_month). Parse province tables out of the Excel workbooks after the RAR archives are restored. Survey years are 2018, 2020, and 2022 only, so do not spread one survey across every month. Agree a join rule before adding columns to province_month.
4. **[high / Integrity]** Join match rates onto the province-month spine (`data/core/province_month.parquet`, `province_id, year_month`, rainfall 7084/7084 (100.00%); guest_arrivals 7084/7084 (100.00%); visitors_total 2464/7084 (34.78%); NSO province_id/year_month columns present 0/2; staged rainfall rows outside the spine months 1001; staged MOTS rows outside the spine months 3541; rainfall province ids missing from the spine 0). Keep the left-join spine so unmatched visitors stay null. Do not inner-join or the pre-2023 rows disappear. Parse NSO to province_id before any accommodation join. Sub-area MOTS rows stay off this spine until a parent-province rule exists.
5. **[high / Lineage / reproducibility]** Raw source files are not in the checkout, so workbook- and CSV-level cleaning cannot be rechecked here (`data/raw/`, `(directory)`, 0 files on disk). Restore `data/raw/` from the ingest machine before changing parsers. Audit staged Parquet in the meantime. Do not invent replacement raw rows.
6. **[high / Lineage / reproducibility]** Every MOTS workbook cited by the staged extract is missing from this checkout (`data/staging/mots/fact_tourism_long.parquet`, `source_file`, 51 of 51 distinct source paths are absent). Re-run the MOTS crawler where Playwright can reach www.mots.go.th if the workbooks themselves need re-audit. Staged values are still auditable.
7. **[high / Lineage / reproducibility]** NSO workbooks listed in the inventory are not in this checkout (`data/staging/nso/fact_accommodation_nso_inventory.parquet`, `file_path`, 338 of 338 paths missing under the repo root). Re-download the three MD RAR archives and extract them with 7-Zip or UnRAR before any cell-level NSO audit.
8. **[medium / Data leakage]** All 2023 monthly visitors, guests, occupancy, and revenue come from a prior-year block in the 2024 workbook (`data/staging/mots/fact_tourism_long.parquet`, `from_prior_year_column`, 7392 rows; years [2023]; metrics ['guests', 'occupancy_pct', 'revenue_million_baht', 'visitors']). Keep the 2023 values — they are the only monthly copy, and guest totals match guest_arrivals. Carry the prior-year flag onto province_month for visitors_total in 2023 so it is not treated as a primary 2023 release. guest_arrivals itself is not from a prior-year column. Search for a standalone 2023 (R) workbook before replacing these cells.
9. **[medium / Data leakage]** Quarterly guest_arrivals equal the sum of the three months in the same workbook (`data/staging/mots/fact_tourism_long.parquet`, `quarter, value`, 22176 monthly rows sit next to a quarter total that includes them). Do not feed the quarter total into a model that predicts one of that quarter's months. The audit already checks that the quarter matches the monthly sum within 0.1%.
10. **[medium / Timeliness / freshness]** The modeling table stops before the rainfall extract and well before the audit clock (`data/core/province_month.parquet`, `year_month`, province_month ends 2025-08 (15 months before 2026-11); staged HII ends 2026-09 (13 months later)). Treat 2025-08 as a declared label window, not as 'latest available'. Extend config/sources.yaml and rebuild core when models need the rainfall already staged through the HII end month. NSO stays a biennial survey (2018, 2020, 2022); do not expect a monthly accommodation series from it.
11. **[medium / Coverage imbalance]** Guest volume is highly uneven across provinces; visitor coverage is not (`data/core/province_month.parquet`, `guest_arrivals`, กรุงเทพมหานคร is 19.5% of national guest_arrivals; largest/median province total 31.8x; smallest/median 0.09x; visitors_total non-null months per province min 32, max 32). Fit province effects or a log target. Score errors per province, not only on the pooled sum, or Bangkok dominates. Do not drop large provinces to balance the sample. The missing visitors_total cells are the same 60 months in every province.
12. **[medium / Consistency]** MOTS province labels vary (abbreviations, spelling, year suffixes, รวม prefixes) even though province_id is filled (`data/staging/mots/fact_tourism_long.parquet`, `province_th_raw`, 4656 of 66384 province rows (7.01%); 13 distinct non-canonical labels). Add canonical `province_th` from `dim_province` beside `province_th_raw`. Keep the raw label for lineage. Do not create new province ids for aliases such as กรุงเทพฯ, ศรีษะเกษ, or ประจวบศิรีขันธ์.
13. **[medium / Conformity / format]** MOTS source_file uses Windows backslashes, so the path does not resolve on Linux (`data/staging/mots/fact_tourism_long.parquet`, `source_file`, 73080 of 73080 staged rows). Write POSIX paths in the manifest and Parquet (`data/raw/mots/...`). The same pattern is copied into `data/core/fact_tourism_long.parquet`.
14. **[medium / Conformity / format]** Core MOTS lineage paths also use Windows backslashes (`data/core/fact_tourism_long.parquet`, `source_file`, 69539 of 69539 rows). Normalize separators when core tables are next rebuilt. Do not hand-edit the Parquet.
15. **[medium / Conformity / format]** NSO inventory paths use Windows backslashes and the archives are not on disk (`data/staging/nso/fact_accommodation_nso_inventory.parquet`, `file_path`, 338 of 338 paths). Store POSIX paths. The same paths are copied to `data/core/fact_accommodation_nso.parquet`.
16. **[medium / Lineage / reproducibility]** Latest manifest status is still error for three MOTS news pages (`data/manifest/manifest_downloads.csv`, `status, news_url`, 3 latest rows with status=error). Retry these three news URLs and confirm they are not province-month workbooks. Historical error rows that later succeeded are not current failures. Modeled guest_arrivals and visitors_total are already complete inside their published windows.
17. **[medium / Lineage / reproducibility]** The configured HII rainfall CSV did not return a body in this run (`config/sources.yaml`, `hii.csv_url`, 1 URL). Retry the download from a network that can read data.hii.or.th before refreshing rainfall. The staged extract already on disk remains the audited copy. Do not replace it with an empty file.
18. **[low / Uniqueness]** Province crosswalk contains exact duplicate rows (`config/province_crosswalk.csv`, `alias`, 4 extra copies; aliases ['กรุงเทพมหานคร', 'กรุงเทพฯ', 'ประจวบคีรีขันธ์', 'ภูเก็ต']). Drop exact duplicate rows. They map to the same province_id, so this is not a conflicting code. Keep genuine aliases (กรุงเทพฯ, ศรีษะเกษ, ประจวบศิรีขันธ์).
19. **[low / Validity]** The visitors_total coverage floor passes even though an entire period is null (`config/data_contracts.yaml`, `province_month.coverage.visitors_total_min_non_null`, floor 2400, actual non-null 2464, nulls before 2023-01 4620, nulls from 2023-01 onward 0). Add a contract rule that visitors_total is null exactly when year_month < 2023-01, and non-null on every later spine row.
20. **[low / Validity]** Staged HII province_id is an integer while core and MOTS use text ids (`data/staging/hii/fact_rainfall.parquet`, `province_id`, dtype=int64; 77 distinct ids). Cast province_id to a stripped string in `src/staging/hii.py` so joins do not depend on a later cast in build_core.
21. **[low / Timeliness / freshness]** Staged HII rainfall continues past the modeling window and is dropped by build_core (`data/staging/hii/fact_rainfall.parquet`, `year_month`, 1001 rows (77 provinces × 13 months)). Keep the staged rows. Extend `config/sources.yaml` date_window and rebuild core only if models should use months after 2025-08. Do not delete the extra months from staging.
22. **[low / Timeliness / freshness]** Staged MOTS has province-months after 2025-08 that core drops (`data/staging/mots/fact_tourism_long.parquet`, `year_month`, 3541 rows; months 2025-09 … 2025-12). Keep them in staging. Extend the date window only if 2025-09 through 2025-12 should enter province_month.
23. **[low / Completeness]** The monthly guests metric has no Thai/foreign split (`data/staging/mots/fact_tourism_long.parquet`, `tourist_type`, 2772 guests rows, all tourist_type=total). Confirm the (R) sheet publishes only จำนวนผู้เข้าพัก as a total. Do not split it with the visitor Thai/foreign ratio.
24. **[low / Integrity]** Resort and sub-area sheets have a null province_id by design (`data/staging/mots/fact_tourism_long.parquet`, `province_id, province_th_raw`, 6696 rows, 14 labels). Leave these rows off the 77-province spine. Map a parent province only after an approved rule (the label often contains the parent, for example พัทยา ชลบุรี).
25. **[low / Lineage / reproducibility]** A null-URL NSO extract_failed row is still the latest status for its own manifest key (`data/manifest/manifest_downloads.csv`, `status, file_url`, 1 latest non-ok NSO rows; inventory status is extracted for every workbook). Key manifest summaries by local_path when file_url is null. Do not re-extract solely because of this row: the three RAR downloads have status ok and the inventory lists 338 extracted workbooks.

## Patterns to keep

These are quantified so they are not cleaned away.

- **Rainfall carries sub-0.01 mm binary residue from the upstream file** (`data/core/province_month.parquet`, `avg_rain_mm`, 6805 of 7084 values differ from a 2-decimal rounding). Optional: round millimeters to 2 decimals at staging for display. Leave full precision for modeling.
- **A few province-months have extreme but internally consistent rainfall** (`data/core/province_month.parquet`, `avg_rain_mm`, 61 per-province |z|>3 flags in 46 provinces; per-province IQR flags 102; max avg_rain_mm=1452.6). Keep the values. They sit inside min_rain_mm..max_rain_mm, are not negative, and match known wet-season peaks in the far south. Add an extreme-weather indicator if a model needs it. Do not clip to a global IQR fence.
- **Quarantine holds national MOTS workbooks that are out of the province grid on purpose** (`data/staging/mots/quarantine/quarantine.csv`, `reason`, 57 files; reasons {'out_of_scope_national': 57}). Leave these files quarantined. Do not parse national totals into the 77-province table.
- **Null year_month on quarterly MOTS rows is the quarterly grain, not a missing month** (`data/staging/mots/fact_tourism_long.parquet`, `year_month, quarter`, 25380 rows with quarter set and year_month null). Do not forward-fill quarterly rows onto months. Use the monthly guest_arrivals rows, which already sum to the quarter within 0.1%.
- **COVID-period guest_arrivals drop is a real level shift** (`data/core/province_month.parquet`, `guest_arrivals, is_covid`, is_covid true on 2618 of 7084 rows; national sum 2020-04=307,277 vs 2024-12=16,492,784). Keep is_covid as a model feature. Do not winsorize or drop 2020-03 through 2022-12 to make the series look stationary.
- **Per-province IQR flags on guest_arrivals are COVID troughs and pre-2020 seasonal peaks** (`data/core/province_month.parquet`, `guest_arrivals`, 388 per-province IQR flags across 43 provinces; 358 inside is_covid; 30 outside is_covid). Do not cap or delete these rows. A global IQR treats Bangkok as an outlier because the cross-province distribution is skewed. Global IQR flags on guest_arrivals: 910. Global |z|>3: 142. Use per-province models instead of a global cap. Non-COVID flags observed on the high side: 30.
- **guest_arrivals zeros are published COVID-shutdown months, not missing values** (`data/core/province_month.parquet`, `guest_arrivals`, 27 zeros; 22 of them are 2020-04; 0 fall outside is_covid). Keep 0. It is a published zero, not a null. Do not impute these cells.
- **guest_arrivals and visitors_total measure different things and should not be forced equal** (`data/core/province_month.parquet`, `guest_arrivals, visitors_total`, 2464 overlapping rows; ratio guest/visitors min=0.062, median=0.453, max=0.948). Keep both columns. Where both exist, the monthly guests metric matches guest_arrivals exactly; visitors are a wider count.
- **Month-to-month guest_arrivals moves of more than 5x sit inside the COVID window** (`data/core/province_month.parquet`, `guest_arrivals`, 172 province-months with previous > 1000 and ratio > 5 or < 0.2; 0 of them fall outside 2020-03..2022-12). Keep the jumps. They are the shutdown and reopening, not corrupted cells. A drift detector trained on 2018-2019 will alarm through 2021; start the reference window at 2023-01 for post-COVID monitoring, or condition on is_covid.
- **Rainfall is strongly seasonal; guest_arrivals move much less across calendar months** (`data/core/province_month.parquet`, `avg_rain_mm, guest_arrivals, month`, rainfall median index month 12=0.02 vs month 9=2.45; guest_arrivals median index 0.83..1.28; guest median pre=75,154, covid=31,640 (0.42x pre), post=80,280 (1.07x pre)). Put calendar month, or a wet-season flag, in any rainfall model. For drift detection, compare a month with the same calendar month in the reference set. The COVID level shift (about 0.42x the pre-2020 median) is a structural break, not a sensor fault. A reference set of only 2018-2019 will flag 2020-2022 as drift by design.
- **Revenue per visitor stays in a baht range, with no thousands-vs-persons break** (`data/core/fact_tourism_province_month.parquet`, `value`, 2464 province-months; baht per visitor min 957, median 2,412, max 43,149; 0 outside 50..500,000 baht). Keep million_baht as the revenue unit. Phuket peak months near 40,000 baht per visitor are resort spend, not a unit error. Do not multiply or divide the column by 1,000.
- **Per-province |z|>3 on visitors_total is rare; global fences only flag large provinces** (`data/core/province_month.parquet`, `visitors_total`, 5 per-province |z|>3 flags; global IQR flags 234 of 2464 non-null rows). Do not winsorize visitors_total. Bangkok's magnitude is the real size of the capital, not a bad row.
- **Zero rainfall is a dry-season measurement (min = max = avg = 0), not a null coded as zero** (`data/core/province_month.parquet`, `avg_rain_mm`, 200 province-months). Keep numeric 0. Do not convert dry-season zeros to null.
- **Core dedupe is deterministic only while duplicate keys stay at zero** (`src/core/build_core.py`, `from_prior_year_column`, 0 duplicate province-month-metric-type keys in the published fact table). Add source_file and sheet as tie-breaks before drop_duplicates. Today the output does not depend on parquet row order because no key is duplicated. A second current-year copy of the same key would.

## Checks with nothing to clean

| Check | File path | Column | Count |
| --- | --- | --- | --- |
| Province dimension row count | `data/core/dim_province.parquet` | `province_id` | 77 |
| Month dimension matches the config window and has 34 COVID months | `data/core/dim_month.parquet` | `year_month, is_covid` | months=92, covid_months=34 |
| Full 77×92 grid, no duplicate keys, no months outside the window | `data/core/province_month.parquet` | `province_id, year_month` | rows=7084, duplicate_keys=0, gaps=0 |
| Nulls in avg_rain_mm | `data/core/province_month.parquet` | `avg_rain_mm` | 0 |
| Nulls in min_rain_mm | `data/core/province_month.parquet` | `min_rain_mm` | 0 |
| Nulls in max_rain_mm | `data/core/province_month.parquet` | `max_rain_mm` | 0 |
| Nulls in guest_arrivals | `data/core/province_month.parquet` | `guest_arrivals` | 0 |
| Negative rainfall cells | `data/core/province_month.parquet` | `min_rain_mm, max_rain_mm, avg_rain_mm` | 0 |
| min_rain_mm greater than max_rain_mm | `data/core/province_month.parquet` | `min_rain_mm, max_rain_mm` | 0 |
| avg_rain_mm outside min..max | `data/core/province_month.parquet` | `avg_rain_mm` | 0 |
| avg_rain_mm above 5000 mm | `data/core/province_month.parquet` | `avg_rain_mm` | 0 |
| No duplicate province-month keys | `data/staging/hii/fact_rainfall.parquet` | `province_id, year_month` | 0 |
| Negative rainfall in the staged HII extract | `data/staging/hii/fact_rainfall.parquet` | `avg_rain_mm` | 0 |
| Gaps in the staged HII province × observed-month grid | `data/staging/hii/fact_rainfall.parquet` | `province_id, year_month` | 0 |
| HII province ids with more than one Thai name | `data/staging/hii/fact_rainfall.parquet` | `province_th` | 0 |
| No duplicate province-month keys | `data/core/fact_rainfall.parquet` | `province_id, year_month` | 0 |
| Province-level rows with a null province_id | `data/staging/mots/fact_tourism_long.parquet` | `province_id` | 0 |
| Negative MOTS values | `data/staging/mots/fact_tourism_long.parquet` | `value` | 0 |
| Rows with neither year_month nor quarter | `data/staging/mots/fact_tourism_long.parquet` | `year_month, quarter` | 0 |
| Rows with both year_month and quarter | `data/staging/mots/fact_tourism_long.parquet` | `year_month, quarter` | 0 |
| year_month values outside YYYY-MM | `data/staging/mots/fact_tourism_long.parquet` | `year_month` | 0 |
| year_ce disagrees with year_month | `data/staging/mots/fact_tourism_long.parquet` | `year_ce, year_month` | 0 |
| month disagrees with year_month | `data/staging/mots/fact_tourism_long.parquet` | `month, year_month` | 0 |
| Duplicate parsed MOTS rows on the source grain | `data/staging/mots/fact_tourism_long.parquet` | `province_th_raw, year_month, quarter, metric, tourist_type, source_file, sheet, area_level` | 0 |
| Duplicate province-month metric keys before core dedupe | `data/staging/mots/fact_tourism_long.parquet` | `province_id, year_month, metric, tourist_type` | 0 |
| occupancy_pct outside 0–100 | `data/staging/mots/fact_tourism_long.parquet` | `value` | 0 (observed min=25.51, max=95.74) |
| revenue_million_baht negative or above 1,000,000 (possible baht/million mix-up) | `data/staging/mots/fact_tourism_long.parquet` | `value` | 0 (max=307,690.46) |
| Monthly total != Thai + foreign (0.1% or 1 person) | `data/staging/mots/fact_tourism_long.parquet` | `value` | 0 |
| Quarterly guest_arrivals disagrees with the sum of its three months | `data/staging/mots/fact_tourism_long.parquet` | `value` | 0 |
| No duplicate province-month-metric-type keys | `data/core/fact_tourism_province_month.parquet` | `province_id, year_month, metric, tourist_type` | 0 |
| Duplicate NSO file paths | `data/staging/nso/fact_accommodation_nso_inventory.parquet` | `file_path` | 0 |
| Alias maps to more than one province_id (1 means no conflict) | `config/province_crosswalk.csv` | `alias, province_id` | 1 |
| Distinct province ids in the crosswalk | `config/province_crosswalk.csv` | `province_id` | 77 |
| MOTS value conflicts after the first-published-value rule | `data/staging/mots/quarantine/conflicts.csv` | `(rows)` | 0 |
| Unmapped province names written by staging | `data/staging/mots/quarantine/unmapped_provinces.csv` | `(rows)` | 0 |
| data_contracts.yaml required columns, unique keys, and coverage floors | `config/data_contracts.yaml` | `required_columns, unique, coverage` | 0 violations |
| year_be is not year_ce + 543 | `data/core/province_month.parquet` | `year_be, year_ce` | 0 |
| is_covid disagrees with the 2020-03..2022-12 window | `data/core/province_month.parquet` | `is_covid, year_month` | 0 |
| Irregular month steps inside a province series | `data/core/province_month.parquet` | `year_month` | 0 |
| Rainfall province ids absent from province_month | `data/staging/hii/fact_rainfall.parquet` | `province_id` | 0 |
| guest_arrivals greater than visitors_total in the same province-month | `data/core/province_month.parquet` | `guest_arrivals, visitors_total` | 0 |
| Thai/English labels with surrounding whitespace, double spaces, U+FFFD, or Latin-1 mojibake | `data/staging/mots/fact_tourism_long.parquet` | `province_th_raw, province_th, province_en` | 0 |
| MOTS metric stored under more than one unit, or a unit other than the expected one | `data/staging/mots/fact_tourism_long.parquet` | `metric, unit` | 0 |
| Monthly guests metric disagrees with guest_arrivals on the same province-month | `data/core/fact_tourism_province_month.parquet` | `value` | 0 of 2464 |
| Duplicate keys that would make core dedupe depend on row order | `data/core/fact_tourism_province_month.parquet` | `province_id, year_month, metric, tourist_type` | 0 |

## Column profiles

Null percentages use the row count of that file. A null `year_month` on a quarterly MOTS row is expected; see the findings.

### `data/core/dim_province.parquet`

Province dimension. Rows: **77**. Exact duplicate rows: **0**. Grain: province_id.

| Column | Dtype | Nulls | Null % |
| --- | --- | ---: | ---: |
| `province_id` | `str` | 0 | 0.00% |
| `province_th` | `str` | 0 | 0.00% |
| `province_en` | `str` | 0 | 0.00% |
| `region` | `str` | 0 | 0.00% |

### `data/core/dim_month.parquet`

Month dimension. Rows: **92**. Exact duplicate rows: **0**. Grain: year_month.

| Column | Dtype | Nulls | Null % |
| --- | --- | ---: | ---: |
| `year_month` | `str` | 0 | 0.00% |
| `year_ce` | `int64` | 0 | 0.00% |
| `month` | `int64` | 0 | 0.00% |
| `year_be` | `int64` | 0 | 0.00% |
| `is_covid` | `bool` | 0 | 0.00% |

| Column | Min | Median | Max | Zeros | Negatives |
| --- | ---: | ---: | ---: | ---: | ---: |
| `year_ce` | 2,018.00 | 2,021.00 | 2,025.00 | 0 | 0 |
| `month` | 1 | 6 | 12 | 0 | 0 |
| `year_be` | 2,561.00 | 2,564.00 | 2,568.00 | 0 | 0 |

### `data/core/province_month.parquet`

Final modeling table. Rows: **7084**. Exact duplicate rows: **0**. Grain: province_id × year_month.

| Column | Dtype | Nulls | Null % |
| --- | --- | ---: | ---: |
| `province_id` | `str` | 0 | 0.00% |
| `province_th` | `str` | 0 | 0.00% |
| `year_month` | `str` | 0 | 0.00% |
| `year_ce` | `int64` | 0 | 0.00% |
| `month` | `int64` | 0 | 0.00% |
| `year_be` | `int64` | 0 | 0.00% |
| `is_covid` | `bool` | 0 | 0.00% |
| `min_rain_mm` | `float64` | 0 | 0.00% |
| `max_rain_mm` | `float64` | 0 | 0.00% |
| `avg_rain_mm` | `float64` | 0 | 0.00% |
| `guest_arrivals` | `float64` | 0 | 0.00% |
| `visitors_total` | `float64` | 4620 | 65.22% |

| Column | Min | Median | Max | Zeros | Negatives |
| --- | ---: | ---: | ---: | ---: | ---: |
| `year_ce` | 2,018.00 | 2,021.00 | 2,025.00 | 0 | 0 |
| `month` | 1 | 6 | 12 | 0 | 0 |
| `year_be` | 2,561.00 | 2,564.00 | 2,568.00 | 0 | 0 |
| `min_rain_mm` | 0 | 56.67 | 1,264.14 | 818 | 0 |
| `max_rain_mm` | 0 | 152.93 | 1,642.35 | 200 | 0 |
| `avg_rain_mm` | 0 | 102.8851 | 1,452.63 | 200 | 0 |
| `guest_arrivals` | 0 | 61,242.00 | 3,370,616.00 | 27 | 0 |
| `visitors_total` | 18,255.00 | 188,191.00 | 5,705,200.00 | 0 | 0 |

### `data/staging/hii/fact_rainfall.parquet`

HII rainfall extract (staged). Rows: **8085**. Exact duplicate rows: **0**. Grain: province_id × year_month. This is the parsed `spatial-rain-hii.csv`.

| Column | Dtype | Nulls | Null % |
| --- | --- | ---: | ---: |
| `source` | `str` | 0 | 0.00% |
| `source_file` | `str` | 0 | 0.00% |
| `year_ce` | `int64` | 0 | 0.00% |
| `month` | `int64` | 0 | 0.00% |
| `year_month` | `str` | 0 | 0.00% |
| `province_id` | `int64` | 0 | 0.00% |
| `province_th` | `str` | 0 | 0.00% |
| `min_rain_mm` | `float64` | 0 | 0.00% |
| `max_rain_mm` | `float64` | 0 | 0.00% |
| `avg_rain_mm` | `float64` | 0 | 0.00% |

| Column | Min | Median | Max | Zeros | Negatives |
| --- | ---: | ---: | ---: | ---: | ---: |
| `year_ce` | 2,018.00 | 2,022.00 | 2,026.00 | 0 | 0 |
| `month` | 1 | 6 | 12 | 0 | 0 |
| `province_id` | 10 | 50 | 96 | 0 | 0 |
| `min_rain_mm` | 0 | 57.64 | 1,421.49 | 903 | 0 |
| `max_rain_mm` | 0 | 154.2 | 1,647.78 | 232 | 0 |
| `avg_rain_mm` | 0 | 103.8762 | 1,519.62 | 232 | 0 |

### `data/core/fact_rainfall.parquet`

HII rainfall inside the modeling window. Rows: **7084**. Exact duplicate rows: **0**. Same columns as staging, filtered to the date window, province_id stored as text.

| Column | Dtype | Nulls | Null % |
| --- | --- | ---: | ---: |
| `source` | `str` | 0 | 0.00% |
| `source_file` | `str` | 0 | 0.00% |
| `year_ce` | `int64` | 0 | 0.00% |
| `month` | `int64` | 0 | 0.00% |
| `year_month` | `str` | 0 | 0.00% |
| `province_id` | `str` | 0 | 0.00% |
| `province_th` | `str` | 0 | 0.00% |
| `min_rain_mm` | `float64` | 0 | 0.00% |
| `max_rain_mm` | `float64` | 0 | 0.00% |
| `avg_rain_mm` | `float64` | 0 | 0.00% |

| Column | Min | Median | Max | Zeros | Negatives |
| --- | ---: | ---: | ---: | ---: | ---: |
| `year_ce` | 2,018.00 | 2,021.00 | 2,025.00 | 0 | 0 |
| `month` | 1 | 6 | 12 | 0 | 0 |
| `min_rain_mm` | 0 | 56.67 | 1,264.14 | 818 | 0 |
| `max_rain_mm` | 0 | 152.93 | 1,642.35 | 200 | 0 |
| `avg_rain_mm` | 0 | 102.8851 | 1,452.63 | 200 | 0 |

### `data/staging/mots/fact_tourism_long.parquet`

MOTS long extract (staged). Rows: **73080**. Exact duplicate rows: **0**. Grain: parsed metric row. Monthly and quarterly rows share this file.

| Column | Dtype | Nulls | Null % |
| --- | --- | ---: | ---: |
| `province_th_raw` | `str` | 0 | 0.00% |
| `province_id` | `str` | 6696 | 9.16% |
| `area_level` | `str` | 0 | 0.00% |
| `year_ce` | `int64` | 0 | 0.00% |
| `year_month` | `str` | 25380 | 34.73% |
| `month` | `float64` | 25380 | 34.73% |
| `quarter` | `str` | 47700 | 65.27% |
| `metric` | `str` | 0 | 0.00% |
| `tourist_type` | `str` | 0 | 0.00% |
| `value` | `float64` | 0 | 0.00% |
| `unit` | `str` | 0 | 0.00% |
| `source_file` | `str` | 0 | 0.00% |
| `source` | `str` | 0 | 0.00% |
| `sheet` | `str` | 0 | 0.00% |
| `layout` | `str` | 0 | 0.00% |
| `from_prior_year_column` | `bool` | 0 | 0.00% |

| Column | Min | Median | Max | Zeros | Negatives |
| --- | ---: | ---: | ---: | ---: | ---: |
| `year_ce` | 2,018.00 | 2,023.00 | 2,025.00 | 0 | 0 |
| `month` | 1 | 6 | 12 | 0 | 0 |
| `value` | 0 | 21,207.00 | 18,038,908.00 | 1323 | 0 |

### `data/core/fact_tourism_long.parquet`

MOTS long table inside core filters. Rows: **69539**. Exact duplicate rows: **0**. Staging rows outside the date window are removed; quarterly rows stay.

| Column | Dtype | Nulls | Null % |
| --- | --- | ---: | ---: |
| `province_th_raw` | `str` | 0 | 0.00% |
| `province_id` | `str` | 6543 | 9.41% |
| `area_level` | `str` | 0 | 0.00% |
| `year_ce` | `int64` | 0 | 0.00% |
| `year_month` | `str` | 25380 | 36.50% |
| `month` | `float64` | 25380 | 36.50% |
| `quarter` | `str` | 44159 | 63.50% |
| `metric` | `str` | 0 | 0.00% |
| `tourist_type` | `str` | 0 | 0.00% |
| `value` | `float64` | 0 | 0.00% |
| `unit` | `str` | 0 | 0.00% |
| `source_file` | `str` | 0 | 0.00% |
| `source` | `str` | 0 | 0.00% |
| `sheet` | `str` | 0 | 0.00% |
| `layout` | `str` | 0 | 0.00% |
| `from_prior_year_column` | `bool` | 0 | 0.00% |

| Column | Min | Median | Max | Zeros | Negatives |
| --- | ---: | ---: | ---: | ---: | ---: |
| `year_ce` | 2,018.00 | 2,023.00 | 2,025.00 | 0 | 0 |
| `month` | 1 | 6 | 12 | 0 | 0 |
| `value` | 0 | 21,257.00 | 18,038,908.00 | 1323 | 0 |

### `data/core/fact_tourism_province_month.parquet`

Province-month tourism facts. Rows: **40964**. Exact duplicate rows: **0**. Monthly province rows only, so `quarter` is null on every row. That is the monthly grain, not a missing quarter label.

| Column | Dtype | Nulls | Null % |
| --- | --- | ---: | ---: |
| `province_th_raw` | `str` | 0 | 0.00% |
| `province_id` | `str` | 0 | 0.00% |
| `area_level` | `str` | 0 | 0.00% |
| `year_ce` | `int64` | 0 | 0.00% |
| `year_month` | `str` | 0 | 0.00% |
| `month` | `float64` | 0 | 0.00% |
| `quarter` | `str` | 40964 | 100.00% |
| `metric` | `str` | 0 | 0.00% |
| `tourist_type` | `str` | 0 | 0.00% |
| `value` | `float64` | 0 | 0.00% |
| `unit` | `str` | 0 | 0.00% |
| `source_file` | `str` | 0 | 0.00% |
| `source` | `str` | 0 | 0.00% |
| `sheet` | `str` | 0 | 0.00% |
| `layout` | `str` | 0 | 0.00% |
| `from_prior_year_column` | `bool` | 0 | 0.00% |

| Column | Min | Median | Max | Zeros | Negatives |
| --- | ---: | ---: | ---: | ---: | ---: |
| `year_ce` | 2,018.00 | 2,023.00 | 2,025.00 | 0 | 0 |
| `month` | 1 | 6 | 12 | 0 | 0 |
| `value` | 0 | 20,567.00 | 5,705,200.00 | 736 | 0 |

### `data/staging/nso/fact_accommodation_nso_inventory.parquet`

NSO accommodation inventory (not a fact table). Rows: **338**. Exact duplicate rows: **0**. One row per extracted workbook. No province or month measures.

| Column | Dtype | Nulls | Null % |
| --- | --- | ---: | ---: |
| `source` | `str` | 0 | 0.00% |
| `survey_year_be` | `int64` | 0 | 0.00% |
| `survey_year_ce` | `int64` | 0 | 0.00% |
| `status` | `str` | 0 | 0.00% |
| `file_path` | `str` | 0 | 0.00% |

| Column | Min | Median | Max | Zeros | Negatives |
| --- | ---: | ---: | ---: | ---: | ---: |
| `survey_year_be` | 2,561.00 | 2,563.00 | 2,565.00 | 0 | 0 |
| `survey_year_ce` | 2,018.00 | 2,020.00 | 2,022.00 | 0 | 0 |

### `data/core/fact_accommodation_nso.parquet`

NSO inventory copied into core. Rows: **338**. Exact duplicate rows: **0**. Same inventory as staging. Not joined to province_month.

| Column | Dtype | Nulls | Null % |
| --- | --- | ---: | ---: |
| `source` | `str` | 0 | 0.00% |
| `survey_year_be` | `int64` | 0 | 0.00% |
| `survey_year_ce` | `int64` | 0 | 0.00% |
| `status` | `str` | 0 | 0.00% |
| `file_path` | `str` | 0 | 0.00% |

| Column | Min | Median | Max | Zeros | Negatives |
| --- | ---: | ---: | ---: | ---: | ---: |
| `survey_year_be` | 2,561.00 | 2,563.00 | 2,565.00 | 0 | 0 |
| `survey_year_ce` | 2,018.00 | 2,020.00 | 2,022.00 | 0 | 0 |

## Method

The audit scores twelve dimensions. A finding is filed under one primary dimension so the summary does not double-count it.

- **Completeness.** Null counts and percent of rows, the 77×92 grid (`dim_province` × the month list in `config/sources.yaml`), and whole sources or periods that never arrive (visitors before 2023-01, NSO measures, quarterly rows with a null month).
- **Uniqueness.** Exact duplicate rows and the grains in `config/data_contracts.yaml`.
- **Validity.** Required columns, key uniqueness, and coverage floors in `data_contracts.yaml`; `year_be = year_ce + 543`; `is_covid` versus the configured window; occupancy in 0–100; negative values; rainfall above 5000 mm; revenue above 1,000,000 million baht; `province_id` dtype.
- **Accuracy / plausibility.** Per-province IQR (1.5×IQR) and |z| > 3. Month-to-month ratios above 5 or below 0.2 when the previous month exceeds 1,000 guests. Baht per visitor outside 50..500,000. `guest_arrivals` above `visitors_total`.
- **Consistency.** Canonical province names versus `province_th_raw`. One unit per metric. Monthly `guests` versus `guest_arrivals`. Thai + foreign versus total, and quarter versus the sum of months, at the tolerance in `src/qa/consistency.py` (1 person or 0.1%).
- **Timeliness.** Last `year_month` per source versus the audit clock and versus the modeling window. Steps between consecutive months inside each province. Buddhist-era versus Gregorian years.
- **Integrity.** Match rate of rainfall, guest_arrivals, and visitors_total onto the spine. NSO rows that lack join keys. Staged rows whose month is outside the spine.
- **Conformity.** Surrounding whitespace, double spaces, U+FFFD, and Latin-1 mojibake on Thai and English names. Windows backslashes in stored paths. Float residue past 0.01 mm.
- **Temporal / stationarity.** Pre-COVID (through 2020-02), COVID (2020-03..2022-12), and post-COVID medians. Calendar-month index of rainfall and guests, for drift rules that should compare like months.
- **Data leakage.** Same-month rain and visitors beside `guest_arrivals`. Quarter totals that equal the months being predicted. 2023 monthly figures taken from a 2024 prior-year block.
- **Coverage imbalance.** Share of national `guest_arrivals` and the ratio of the largest province total to the median. Visitors non-null months per province.
- **Lineage.** Raw files absent from git, source URLs that do not return a body, manifest rows still in error, and dedupe that would follow parquet row order if a key were duplicated.
