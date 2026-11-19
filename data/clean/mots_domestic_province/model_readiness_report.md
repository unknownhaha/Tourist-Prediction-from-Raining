# Model-readiness report (all rows, COVID included, `is_covid` not used as a feature)

## 1. Structure

| check | province_quarter | province_month |
|---|---|---|
| rows | 2,156 | 6,468 |
| provinces × periods | 77 × 28 | 77 × 84 |
| duplicate keys | 0 | 0 |
| null cells | 0 | 0 |
| inf cells | 0 | 0 |
| negative numeric cells | 0 | 0 |
| balanced panel | True | True |

## 2. Column roles

- Constant columns (drop): rain_months, rain_source_rows
- Identifier / label columns (not numeric features): province_th, province_en (use province_id or region as categorical)
- Same-period tourism columns that leak the target (29): every `visitors_*`, `tourists_*`, `excursionists_*`, `guest_arrivals_*`, `revenue_*`, `avg_expenditure_*`, `avg_length_of_stay_*`, `occupancy_rate_pct_total`, `rooms_total`. They are published together with the target (e.g. visitors_total = visitors_thai + visitors_foreign = tourists_total + excursionists_total exactly), so they cannot be inputs when predicting a tourism target for the same period.
- `is_covid`: present, excluded from features in this run by request.

## 3. Target candidates

| target | table | zeros | skew (raw) | skew (log1p) | max / median |
|---|---|---:|---:|---:|---:|
| visitors_total | quarter | 0 | 6.4 | -0.47 | 47× |
| guest_arrivals_total | quarter | 0 | 6.2 | -0.23 | 51× |
| guest_arrivals_total | month | 27 | 6.3 | -1.67 | 55× |

Raw targets are heavily right-skewed (Bangkok, Chon Buri, Phuket dominate); model `log1p(target)`.

## 4. What ignoring COVID means

- log(visitors) vs the province × quarter median: non-COVID rows mean +0.15 (sd 0.24); COVID rows mean -0.98 (sd 1.07).
- 43% of quarterly rows (924) and 40% of monthly rows (2618) sit in the COVID window. Without the flag a model sees this drop as unexplained noise, or attributes part of it to rain.
- Zeros in the targets are all COVID lockdown months/quarters; they are real and must stay.

## 5. Rain features

- Rain lags built from the continuous HII series (from 2018-01): nulls in quarterly lags 0, monthly lags 0.
- Within-province correlation (both sides demeaned by province × season, so seasonality and province size are removed):

  - visitors_total (quarter): rain_sum_mm -0.066 (non-COVID rows +0.162); rain_lag1 +0.069 (non-COVID rows -0.100); rain_lag4 +0.130 (non-COVID rows -0.164)
  - guest_arrivals_total (month): rain_avg_mm -0.037 (non-COVID rows -0.017); rain_lag1 -0.015 (non-COVID rows -0.088); rain_lag12 +0.073 (non-COVID rows -0.153)

Rain anomalies explain little of the tourism anomalies; expect rain to add a small gain over a seasonal baseline.

## 6. Baselines (train ≤ 2024, test = 2025, all rows incl. COVID, no COVID feature)

### visitors_total (quarterly)

| model | WAPE % | MAPE % | RMSE(log) |
|---|---:|---:|---:|
| Seasonal naive (same quarter last year) | 4.9 | 6.0 | 0.103 |
| Province × quarter mean (train) | 33.9 | 34.5 | 0.482 |
| Province × quarter of 2024 | 4.9 | 6.0 | 0.103 |
| Log-linear FE (province + season + trend), no rain | 27.8 | 28.8 | 0.383 |
| Log-linear FE + rain (rain_sum_mm, rain_lag1) | 28.4 | 31.4 | 0.402 |
| Seasonal naive × rain change (Δlog rain_sum_mm) | 5.6 | 5.5 | 0.100 |
- coefficients, Log-linear FE + rain (rain_sum_mm, rain_lag1): rain_sum_mm -0.1798, rain_lag1 +0.0247
- coefficients, Seasonal naive × rain change (Δlog rain_sum_mm): Δlog rain_sum_mm -0.0674, intercept (avg growth) +0.0472

### guest_arrivals_total (monthly)

| model | WAPE % | MAPE % | RMSE(log) |
|---|---:|---:|---:|
| Seasonal naive (same month last year) | 5.5 | 6.5 | 0.090 |
| Province × month mean (train) | 32.3 | 28.9 | 0.398 |
| Province × month of 2024 | 5.5 | 6.5 | 0.090 |
| Log-linear FE (province + season + trend), no rain | 31.7 | 33.2 | 0.472 |
| Log-linear FE + rain (rain_avg_mm, rain_lag1) | 31.9 | 33.8 | 0.480 |
| Seasonal naive × rain change (Δlog rain_avg_mm) | 8.5 | 9.1 | 0.120 |
- coefficients, Log-linear FE + rain (rain_avg_mm, rain_lag1): rain_avg_mm -0.0683, rain_lag1 -0.0115
- coefficients, Seasonal naive × rain change (Δlog rain_avg_mm): Δlog rain_avg_mm +0.0693, intercept (avg growth) +0.0048

