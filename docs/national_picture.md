# National picture

Monthly view of **`province_month.parquet`** aggregated across all **77** provinces (2018-01–2025-08 CE).

The chart above shows:

- **Guest arrivals** — sum of `guest_arrivals` each month.
- **Visitors** — sum of `visitors_total` where published (gaps before ~2023 are missing data, not zero).
- **Rainfall** — mean of `avg_rain_mm` (right axis, mm).
- **Shaded band** — project `is_covid` window (2020-03–2022-12); not a causal claim.

This pipeline prepares data only; it does not train or run prediction models. For table definitions see the [Data dictionary](data_dictionary.md).
