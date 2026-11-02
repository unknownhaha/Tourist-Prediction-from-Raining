# National picture chart

Date: 2026-11-02

## Purpose

Add one offline chart to the handbook explorer so stakeholders and data scientists can see rainfall against tourism for the whole country, without reading the coverage report or opening Parquet.

## What the reader sees

A new handbook page, **National picture**, is available to every role.

- Stakeholders and DS/ML land on this page.
- Operators and QA keep their current start pages and open the chart from the handbook list.
- English and Thai captions and chart labels follow the existing language tabs.

The chart is one monthly series for 2018-01 through 2025-08:

- **Guest arrivals:** sum across all 77 provinces.
- **Visitors:** sum across provinces. Months with no values are gaps, not zeros. The line starts when data exists (typically 2023).
- **Rainfall:** mean of `avg_rain_mm` across provinces, on a second axis.
- **COVID window:** a shaded band for 2020-03 through 2022-12, from `is_covid`. This is the project flag, not a causal claim.
- **Hover:** the month and the three numbers.

A short caption under the chart states the aggregation (sums versus average, 77 provinces) and that this pipeline does not fit a model.

## Data path

`python -m src.docs.build_site` reads `data/core/province_month.parquet` when the file exists.

For each `year_month` it stores:

| Field | Rule |
|-------|------|
| `guest_arrivals` | Sum, ignoring nulls |
| `visitors_total` | Sum, ignoring nulls. If every province is null, store null |
| `avg_rain_mm` | Mean of non-null values across provinces |
| `is_covid` | The month flag (same for every province) |

Months are stored in calendar order. A null visitor total is shown on hover as an em dash, not as zero.

The series is embedded in `site/index.html` with the rest of the explorer payload. The page does not `fetch` a separate file, so double-clicking `site/index.html` still works.

If the Parquet file is missing or empty, the site build still succeeds. The chart page tells the reader to run `python -m src.core.build_core` and then rebuild the site. The builder does not invent numbers.

## How it fits the site

- Page id `national` in `docs/explorer.yaml`.
- Caption prose in `docs/national_picture.md` and `docs/th/national_picture.md`.
- The SVG is drawn by `site/assets/app.js` when that page is open. The markdown file is the caption, not the chart.
- Axis names, legend, COVID label, hover text, and the empty-file message live in `docs/i18n/en.yaml` and `docs/i18n/th.yaml`.
- Rebuild command stays `python -m src.docs.build_site`.

## Units

- Aggregation lives in the docs builder (a function or small module under `src/docs/`), called only from `build_site`.
- Chart drawing lives in `src/docs/assets/app.js`, with styles in `src/docs/assets/app.css`.
- The builder copies those assets into `site/` as it does today.

## Tests

- A tiny in-memory table checks monthly sums, the mean rainfall, and that an all-null visitor month stays null.
- Building the site with no `province_month.parquet` still writes `site/index.html` and does not embed fabricated series values.

## Out of scope

- Province picker or any grain other than national.
- A third-party chart library.
- Reading Parquet in the browser.
- Training a model, lags, or other features.
- Failing the site build when the Parquet file is absent.
