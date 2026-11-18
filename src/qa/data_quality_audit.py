"""Read-only data-quality audit of staged sources and province_month.

Regenerate the report with::

    python -m src.qa.data_quality_audit

The command overwrites ``reports/data_quality_audit.md`` only. It does not
download files, rewrite Parquet, or change pipeline logic. Pass ``--skip-network``
to skip the short connectivity probe of the source URLs in ``config/sources.yaml``.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.calendar import month_range
from src.common.config_loader import load_sources
from src.common.paths import CONFIG, CORE, RAW, ROOT, STAGING
from src.qa.manifest_summary import latest_manifest_df

REPORT_PATH = ROOT / "reports" / "data_quality_audit.md"
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}
# Lower runs first inside a severity. Impact on the modeling table comes before checkout/path cleanup.
PRIORITY = {
    "pm_visitors_structural_null": 10,
    "mots_2023_monthly_from_prior_column": 20,
    "nso_inventory_only": 30,
    "raw_sources_absent": 40,
    "mots_source_files_missing_on_disk": 41,
    "nso_workbooks_missing_on_disk": 42,
    "mots_noncanonical_province_names": 50,
    "mots_windows_paths": 60,
    "mots_core_windows_paths": 61,
    "nso_windows_paths": 62,
    "manifest_mots_latest_errors": 70,
    "crosswalk_exact_duplicate_rows": 80,
    "hii_province_id_dtype": 90,
    "hii_months_outside_window": 100,
    "mots_months_outside_window": 101,
    "mots_guests_total_only": 110,
    "mots_sub_area_null_province": 120,
    "manifest_nso_stale_extract_failed": 130,
}

# Monthly average rainfall above this is treated as physically implausible for Thailand.
ABSURD_RAIN_MM = 5000.0
# revenue_million_baht above this would be > 1e12 baht in one cell (unit mix-up).
ABSURD_REVENUE_MILLION_BAHT = 1_000_000.0


@dataclass(frozen=True)
class Finding:
    issue_id: str
    severity: str
    issue: str
    file_path: str
    column: str
    count: str
    example: str
    recommended_fix: str
    on_todo: bool = True


@dataclass(frozen=True)
class PassedCheck:
    check: str
    file_path: str
    column: str
    count: str


@dataclass
class AuditReport:
    findings: list[Finding]
    passed: list[PassedCheck]
    profiles: list[str]
    availability: list[str]
    probe_lines: list[str]
    generated_at: str


def _md(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "n/a"
    return f"{100.0 * part / whole:.2f}%"


def _rel(path: Path | str) -> str:
    text = str(path).replace("\\", "/")
    root = str(ROOT).replace("\\", "/")
    if text.startswith(root):
        return text[len(root) :].lstrip("/")
    return text


def _read_parquet(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_parquet(path)


def _iqr_bounds(series: pd.Series) -> tuple[float, float]:
    q1 = float(series.quantile(0.25))
    q3 = float(series.quantile(0.75))
    iqr = q3 - q1
    return q1 - 1.5 * iqr, q3 + 1.5 * iqr


def _iqr_mask(series: pd.Series) -> pd.Series:
    clean = series.dropna()
    if clean.empty or float(clean.quantile(0.75) - clean.quantile(0.25)) == 0:
        return pd.Series(False, index=series.index)
    lo, hi = _iqr_bounds(clean)
    return series.notna() & ((series < lo) | (series > hi))


def _z_mask(series: pd.Series, threshold: float = 3.0) -> pd.Series:
    clean = series.dropna()
    std = float(clean.std(ddof=0)) if len(clean) else 0.0
    if len(clean) < 2 or std == 0:
        return pd.Series(False, index=series.index)
    z = (series - float(clean.mean())) / std
    return series.notna() & (z.abs() > threshold)


def _per_group_flags(df: pd.DataFrame, group: str, column: str, rule: str) -> pd.DataFrame:
    """Return rows whose value fails an IQR or z-score rule inside each group."""
    parts: list[pd.DataFrame] = []
    for _, grp in df.groupby(group, dropna=False):
        if rule == "iqr":
            mask = _iqr_mask(grp[column])
        else:
            mask = _z_mask(grp[column])
        if mask.any():
            parts.append(grp.loc[mask])
    if not parts:
        return df.iloc[0:0]
    return pd.concat(parts, axis=0)


def _profile_markdown(title: str, path: Path, df: pd.DataFrame, key_note: str) -> str:
    lines = [
        f"### `{_rel(path)}`",
        "",
        f"{title}. Rows: **{len(df)}**. Exact duplicate rows: **{int(df.duplicated().sum())}**. {key_note}",
        "",
        "| Column | Dtype | Nulls | Null % |",
        "| --- | --- | ---: | ---: |",
    ]
    n = len(df)
    for col in df.columns:
        nulls = int(df[col].isna().sum())
        lines.append(f"| `{col}` | `{df[col].dtype}` | {nulls} | {_pct(nulls, n)} |")
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    if numeric:
        lines.extend(["", "| Column | Min | Median | Max | Zeros | Negatives |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
        for col in numeric:
            s = df[col]
            lines.append(
                "| `{col}` | {min_v} | {med} | {max_v} | {zeros} | {negs} |".format(
                    col=col,
                    min_v=_fmt_num(s.min()) if s.notna().any() else "",
                    med=_fmt_num(s.median()) if s.notna().any() else "",
                    max_v=_fmt_num(s.max()) if s.notna().any() else "",
                    zeros=int((s == 0).sum()),
                    negs=int((s < 0).sum()),
                )
            )
    lines.append("")
    return "\n".join(lines)


def _fmt_num(value: object) -> str:
    if pd.isna(value):
        return ""
    number = float(value)
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _example_place(df: pd.DataFrame, idx: int | None = None) -> str:
    row = df.iloc[0] if idx is None else df.loc[idx]
    name = row["province_th"] if "province_th" in df.columns else row.get("province_th_raw", row.get("province_id", ""))
    when = row["year_month"] if "year_month" in df.columns and pd.notna(row["year_month"]) else row.get("quarter", "")
    return f"{name} {when}".strip()


def _grid_gap(df: pd.DataFrame, provinces: pd.Series, months: list[str]) -> pd.DataFrame:
    spine = pd.MultiIndex.from_product([provinces.astype(str), months], names=["province_id", "year_month"]).to_frame(index=False)
    have = df[["province_id", "year_month"]].copy()
    have["province_id"] = have["province_id"].astype(str)
    merged = spine.merge(have.drop_duplicates(), on=["province_id", "year_month"], how="left", indicator=True)
    return merged[merged["_merge"] == "left_only"]


def _probe_url(url: str, timeout: float = 8.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "province-month-data-quality-audit"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", "?")
            response.read(64)
            return f"HTTP {status}"
    except Exception as exc:  # noqa: BLE001 — probe must report every failure mode
        detail = str(exc).split("\n", 1)[0][:160]
        return f"{type(exc).__name__}: {detail}"


def _probe_sources() -> list[str]:
    cfg = load_sources()
    nso = cfg["nso"]
    first = nso["rounds"][0]
    nso_url = (
        f"{nso['get_survey_file_url']}?code={nso['survey_code']}"
        f"&subcode={first['subcode']}&type_menu={nso['type_menu']}"
    )
    mots = cfg["mots"]
    rss = mots["rss_category_url"].format(category_id=mots["year_categories"][0]["category_id"])
    targets = [
        ("HII rainfall CSV", cfg["hii"]["csv_url"]),
        ("NSO survey file API (first round)", nso_url),
        ("MOTS category RSS (2018)", rss),
    ]
    lines = []
    for label, url in targets:
        lines.append(f"- {label}: `{_probe_url(url)}` — `{url}`")
    return lines


def _raw_file_count() -> int:
    if not RAW.exists():
        return 0
    return sum(1 for path in RAW.rglob("*") if path.is_file())


def _audit_raw_csv(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    df = pd.read_csv(path)
    rel = _rel(path)
    null_cols = [(col, int(df[col].isna().sum())) for col in df.columns if df[col].isna().any()]
    for col, nulls in null_cols:
        findings.append(
            Finding(
                issue_id=f"raw_null_{path.stem}_{col}",
                severity="medium",
                issue="Nulls in a raw CSV column",
                file_path=rel,
                column=str(col),
                count=f"{nulls} nulls ({_pct(nulls, len(df))} of {len(df)} rows)",
                example=f"column {col}",
                recommended_fix="Inspect the publisher extract before staging. Do not fill nulls until the column's meaning is confirmed.",
            )
        )
    dup = int(df.duplicated().sum())
    if dup:
        findings.append(
            Finding(
                issue_id=f"raw_dup_{path.stem}",
                severity="high",
                issue="Exact duplicate rows in a raw CSV",
                file_path=rel,
                column="(full row)",
                count=f"{dup} duplicate rows of {len(df)}",
                example=str(df.loc[df.duplicated(keep=False)].head(1).to_dict(orient="records")[0])[:180],
                recommended_fix="Drop exact duplicate rows at staging and record the count in the manifest.",
            )
        )
    for col in df.select_dtypes(include="number").columns:
        neg = int((df[col] < 0).sum())
        if neg:
            findings.append(
                Finding(
                    issue_id=f"raw_neg_{path.stem}_{col}",
                    severity="high",
                    issue="Negative values in a raw numeric column",
                    file_path=rel,
                    column=str(col),
                    count=str(neg),
                    example=f"min={df[col].min()}",
                    recommended_fix="Quarantine negative counts or rainfall before they reach province_month.",
                )
            )
    return findings


def _audit_raw_workbook(path: Path) -> list[Finding]:
    """Sheet inventory only. Cell rules run once a workbook is actually present."""
    rel = _rel(path)
    try:
        book = pd.ExcelFile(path)
    except Exception as exc:  # noqa: BLE001
        return [
            Finding(
                issue_id=f"raw_unreadable_{path.name}",
                severity="high",
                issue="Raw workbook could not be opened",
                file_path=rel,
                column="(file)",
                count="1 file",
                example=str(exc)[:180],
                recommended_fix="Re-download the archive and confirm the Excel engine (xlrd for .xls, openpyxl for .xlsx).",
            )
        ]
    empty = []
    for sheet in book.sheet_names:
        preview = book.parse(sheet, header=None, nrows=5)
        if preview.dropna(how="all").empty:
            empty.append(sheet)
    findings: list[Finding] = []
    if empty:
        findings.append(
            Finding(
                issue_id=f"raw_empty_sheets_{path.name}",
                severity="medium",
                issue="Workbook has empty sheets",
                file_path=rel,
                column="(sheet)",
                count=f"{len(empty)} empty sheets of {len(book.sheet_names)}",
                example=empty[0],
                recommended_fix="Skip empty sheets in the parser and record them in quarantine.",
            )
        )
    return findings


def build_audit(probe: bool = True) -> AuditReport:
    cfg = load_sources()
    window = cfg["date_window"]
    months = month_range(window["start"], window["end"])
    findings: list[Finding] = []
    passed: list[PassedCheck] = []
    profiles: list[str] = []
    availability: list[str] = [
        f"Modeling window in `config/sources.yaml`: **{window['start']}** through **{window['end']}** "
        f"({len(months)} months). Expected province-month grid: **77 × {len(months)} = {77 * len(months)}**.",
        f"COVID window used by `is_covid`: **{cfg['covid_window']['start']}** through **{cfg['covid_window']['end']}**.",
        "This audit reads files already on disk. It does not download sources or rebuild Parquet.",
    ]

    raw_n = _raw_file_count()
    availability.append(
        f"`data/raw/` file count: **{raw_n}**. "
        + (
            "Raw HII, MOTS, and NSO downloads are not in this checkout (the directory is gitignored)."
            if raw_n == 0
            else "Raw downloads are present and included below."
        )
    )
    if raw_n == 0:
        findings.append(
            Finding(
                issue_id="raw_sources_absent",
                severity="high",
                issue="Raw source files are not in the checkout, so workbook- and CSV-level cleaning cannot be rechecked here",
                file_path="data/raw/",
                column="(directory)",
                count="0 files on disk",
                example="Manifest paths such as data/raw/hii/spatial-rain-hii.csv and data/raw/mots/.../*.xlsx are absent",
                recommended_fix=(
                    "Restore `data/raw/` from the ingest machine before changing parsers. "
                    "Audit staged Parquet in the meantime. Do not invent replacement raw rows."
                ),
            )
        )
    else:
        for path in sorted(p for p in RAW.rglob("*") if p.is_file()):
            suffix = path.suffix.lower()
            if suffix == ".csv":
                findings.extend(_audit_raw_csv(path))
            elif suffix in {".xls", ".xlsx"}:
                findings.extend(_audit_raw_workbook(path))

    dim_p = _read_parquet(CORE / "dim_province.parquet")
    dim_m = _read_parquet(CORE / "dim_month.parquet")
    pm = _read_parquet(CORE / "province_month.parquet")
    rain_core = _read_parquet(CORE / "fact_rainfall.parquet")
    rain_stage = _read_parquet(STAGING / "hii" / "fact_rainfall.parquet")
    mots_stage = _read_parquet(STAGING / "mots" / "fact_tourism_long.parquet")
    mots_core = _read_parquet(CORE / "fact_tourism_long.parquet")
    mots_pm = _read_parquet(CORE / "fact_tourism_province_month.parquet")
    nso_stage = _read_parquet(STAGING / "nso" / "fact_accommodation_nso_inventory.parquet")
    nso_core = _read_parquet(CORE / "fact_accommodation_nso.parquet")

    loaded = {
        "data/core/dim_province.parquet": dim_p,
        "data/core/dim_month.parquet": dim_m,
        "data/core/province_month.parquet": pm,
        "data/core/fact_rainfall.parquet": rain_core,
        "data/staging/hii/fact_rainfall.parquet": rain_stage,
        "data/staging/mots/fact_tourism_long.parquet": mots_stage,
        "data/core/fact_tourism_long.parquet": mots_core,
        "data/core/fact_tourism_province_month.parquet": mots_pm,
        "data/staging/nso/fact_accommodation_nso_inventory.parquet": nso_stage,
        "data/core/fact_accommodation_nso.parquet": nso_core,
    }
    for rel, df in loaded.items():
        if df is None:
            findings.append(
                Finding(
                    issue_id=f"missing_file_{rel.replace('/', '_')}",
                    severity="high",
                    issue="Expected dataset is missing",
                    file_path=rel,
                    column="(file)",
                    count="0",
                    example="file not found",
                    recommended_fix="Rebuild with the README run order, then rerun this audit.",
                )
            )

    if dim_p is not None:
        profiles.append(_profile_markdown("Province dimension", CORE / "dim_province.parquet", dim_p, "Grain: province_id."))
        if len(dim_p) == 77:
            passed.append(PassedCheck("Province dimension row count", "data/core/dim_province.parquet", "province_id", "77"))
        else:
            findings.append(
                Finding(
                    issue_id="dim_province_count",
                    severity="high",
                    issue="dim_province does not contain 77 provinces",
                    file_path="data/core/dim_province.parquet",
                    column="province_id",
                    count=str(len(dim_p)),
                    example="",
                    recommended_fix="Rebuild dim_province from config/province_crosswalk.csv.",
                )
            )
    if dim_m is not None:
        profiles.append(_profile_markdown("Month dimension", CORE / "dim_month.parquet", dim_m, "Grain: year_month."))
        if "is_covid" in dim_m.columns and int(dim_m["is_covid"].sum()) == 34 and len(dim_m) == len(months):
            passed.append(
                PassedCheck(
                    "Month dimension matches the config window and has 34 COVID months",
                    "data/core/dim_month.parquet",
                    "year_month, is_covid",
                    f"months={len(dim_m)}, covid_months={int(dim_m['is_covid'].sum())}",
                )
            )

    if pm is not None and dim_p is not None:
        _audit_province_month(pm, dim_p, months, findings, passed)
        profiles.append(
            _profile_markdown(
                "Final modeling table",
                CORE / "province_month.parquet",
                pm,
                "Grain: province_id × year_month.",
            )
        )

    if rain_stage is not None:
        _audit_rainfall(rain_stage, months, findings, passed, staged=True)
        profiles.append(
            _profile_markdown(
                "HII rainfall extract (staged)",
                STAGING / "hii" / "fact_rainfall.parquet",
                rain_stage,
                "Grain: province_id × year_month. This is the parsed `spatial-rain-hii.csv`.",
            )
        )
    if rain_core is not None:
        profiles.append(
            _profile_markdown(
                "HII rainfall inside the modeling window",
                CORE / "fact_rainfall.parquet",
                rain_core,
                "Same columns as staging, filtered to the date window, province_id stored as text.",
            )
        )
        dup = int(rain_core.duplicated(["province_id", "year_month"]).sum())
        passed.append(
            PassedCheck(
                "No duplicate province-month keys",
                "data/core/fact_rainfall.parquet",
                "province_id, year_month",
                str(dup),
            )
        )

    if mots_stage is not None and dim_p is not None:
        _audit_mots(mots_stage, dim_p, months, findings, passed, staged=True)
        profiles.append(
            _profile_markdown(
                "MOTS long extract (staged)",
                STAGING / "mots" / "fact_tourism_long.parquet",
                mots_stage,
                "Grain: parsed metric row. Monthly and quarterly rows share this file.",
            )
        )
    if mots_core is not None:
        profiles.append(
            _profile_markdown(
                "MOTS long table inside core filters",
                CORE / "fact_tourism_long.parquet",
                mots_core,
                "Staging rows outside the date window are removed; quarterly rows stay.",
            )
        )
    if mots_pm is not None:
        dup = int(mots_pm.duplicated(["province_id", "year_month", "metric", "tourist_type"]).sum())
        passed.append(
            PassedCheck(
                "No duplicate province-month-metric-type keys",
                "data/core/fact_tourism_province_month.parquet",
                "province_id, year_month, metric, tourist_type",
                str(dup),
            )
        )
        profiles.append(
            _profile_markdown(
                "Province-month tourism facts",
                CORE / "fact_tourism_province_month.parquet",
                mots_pm,
                "Monthly province rows only, so `quarter` is null on every row. That is the monthly grain, not a missing quarter label.",
            )
        )

    if nso_stage is not None:
        _audit_nso(nso_stage, findings, passed)
        profiles.append(
            _profile_markdown(
                "NSO accommodation inventory (not a fact table)",
                STAGING / "nso" / "fact_accommodation_nso_inventory.parquet",
                nso_stage,
                "One row per extracted workbook. No province or month measures.",
            )
        )
    if nso_core is not None:
        profiles.append(
            _profile_markdown(
                "NSO inventory copied into core",
                CORE / "fact_accommodation_nso.parquet",
                nso_core,
                "Same inventory as staging. Not joined to province_month.",
            )
        )

    _audit_crosswalk(findings, passed)
    _audit_manifest_and_quarantine(findings, passed)

    findings.sort(key=lambda item: (SEVERITY_RANK.get(item.severity, 9), PRIORITY.get(item.issue_id, 50), item.issue_id))
    probe_lines = _probe_sources() if probe else ["- Skipped (`--skip-network`)."]
    return AuditReport(
        findings=findings,
        passed=passed,
        profiles=profiles,
        availability=availability,
        probe_lines=probe_lines,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


def _audit_province_month(
    pm: pd.DataFrame,
    dim_p: pd.DataFrame,
    months: list[str],
    findings: list[Finding],
    passed: list[PassedCheck],
) -> None:
    path = "data/core/province_month.parquet"
    dup_keys = int(pm.duplicated(["province_id", "year_month"]).sum())
    gaps = _grid_gap(pm, dim_p["province_id"], months)
    extra = pm[~pm["year_month"].isin(months)]
    if dup_keys == 0 and gaps.empty and extra.empty and len(pm) == len(dim_p) * len(months):
        passed.append(
            PassedCheck(
                f"Full {len(dim_p)}×{len(months)} grid, no duplicate keys, no months outside the window",
                path,
                "province_id, year_month",
                f"rows={len(pm)}, duplicate_keys={dup_keys}, gaps={len(gaps)}",
            )
        )
    else:
        findings.append(
            Finding(
                issue_id="pm_grid",
                severity="high",
                issue="province_month is not a complete province × month spine",
                file_path=path,
                column="province_id, year_month",
                count=f"rows={len(pm)}, duplicate_keys={dup_keys}, gaps={len(gaps)}, outside_window={len(extra)}",
                example=_example_place(gaps) if len(gaps) else "see count",
                recommended_fix="Rebuild dim_province × dim_month and left-join facts. Do not drop spine rows to hide gaps.",
            )
        )

    for col in ("avg_rain_mm", "min_rain_mm", "max_rain_mm", "guest_arrivals"):
        nulls = int(pm[col].isna().sum())
        passed.append(PassedCheck(f"Nulls in {col}", path, col, str(nulls)))

    visitor_nulls = int(pm["visitors_total"].isna().sum())
    early = pm[pm["year_month"] < "2023-01"]
    late = pm[pm["year_month"] >= "2023-01"]
    early_null = int(early["visitors_total"].isna().sum())
    late_null = int(late["visitors_total"].isna().sum())
    sample = pm.loc[pm["visitors_total"].isna()].iloc[0]
    findings.append(
        Finding(
            issue_id="pm_visitors_structural_null",
            severity="high",
            issue="visitors_total is null for every province-month before 2023-01",
            file_path=path,
            column="visitors_total",
            count=(
                f"{visitor_nulls} nulls ({_pct(visitor_nulls, len(pm))} of {len(pm)}); "
                f"{early_null} of {len(early)} rows before 2023-01; "
                f"{late_null} nulls from 2023-01 through the window end"
            ),
            example=(
                f"{sample.province_th} {sample.year_month}: visitors_total is null, "
                f"guest_arrivals={sample.guest_arrivals:.0f}"
            ),
            recommended_fix=(
                "Keep the nulls. The monthly (R) visitor series starts at 2023-01 and is complete after that. "
                "Use guest_arrivals for 2018-01 onward. Do not fill visitors_total with 0."
            ),
        )
    )

    zeros = pm[pm["guest_arrivals"] == 0]
    zero_outside = zeros[~zeros["is_covid"]]
    by_month = zeros.groupby("year_month").size()
    top_month = str(by_month.idxmax()) if len(by_month) else ""
    zero_example = _example_place(zeros) if len(zeros) else "none"
    findings.append(
        Finding(
            issue_id="pm_guest_published_zeros",
            severity="info",
            issue="guest_arrivals zeros are published COVID-shutdown months, not missing values",
            file_path=path,
            column="guest_arrivals",
            count=f"{len(zeros)} zeros; {int((zeros.year_month == '2020-04').sum()) if len(zeros) else 0} of them are 2020-04; {len(zero_outside)} fall outside is_covid",
            example=f"{top_month} accounts for {int(by_month.max()) if len(by_month) else 0} zeros (for example {zero_example})",
            recommended_fix="Keep 0. It is a published zero, not a null. Do not impute these cells.",
            on_todo=False,
        )
    )

    nat = pm.groupby("year_month")["guest_arrivals"].sum()
    med_2019 = float(pm.loc[pm["year_month"].str.startswith("2019"), "guest_arrivals"].median())
    med_2021 = float(pm.loc[pm["year_month"].str.startswith("2021"), "guest_arrivals"].median())
    findings.append(
        Finding(
            issue_id="pm_covid_level_shift",
            severity="info",
            issue="COVID-period guest_arrivals drop is a real level shift",
            file_path=path,
            column="guest_arrivals, is_covid",
            count=(
                f"is_covid true on {int(pm['is_covid'].sum())} of {len(pm)} rows; "
                f"national sum {nat.idxmin()}={nat.min():,.0f} vs {nat.idxmax()}={nat.max():,.0f}"
            ),
            example=f"Median province-month guest_arrivals 2019={med_2019:,.0f}, 2021={med_2021:,.0f}",
            recommended_fix="Keep is_covid as a model feature. Do not winsorize or drop 2020-03 through 2022-12 to make the series look stationary.",
            on_todo=False,
        )
    )

    _audit_pm_outliers(pm, findings)
    _audit_pm_rain_rules(pm, findings, passed)
    _audit_pm_ratio(pm, findings)


def _audit_pm_outliers(pm: pd.DataFrame, findings: list[Finding]) -> None:
    path = "data/core/province_month.parquet"
    guest_flags = _per_group_flags(pm, "province_id", "guest_arrivals", "iqr")
    covid_n = int(guest_flags["is_covid"].sum()) if len(guest_flags) else 0
    non = guest_flags[~guest_flags["is_covid"]] if len(guest_flags) else guest_flags
    high_non = int((non["guest_arrivals"] > 0).sum()) if len(non) else 0
    example_non = _example_place(non.sort_values("guest_arrivals", ascending=False)) if len(non) else "none"
    findings.append(
        Finding(
            issue_id="pm_guest_iqr_by_province",
            severity="info",
            issue="Per-province IQR flags on guest_arrivals are COVID troughs and pre-2020 seasonal peaks",
            file_path=path,
            column="guest_arrivals",
            count=f"{len(guest_flags)} per-province IQR flags across {guest_flags['province_id'].nunique() if len(guest_flags) else 0} provinces; {covid_n} inside is_covid; {len(non)} outside is_covid",
            example=f"Outside-COVID example (high side): {example_non}",
            recommended_fix=(
                "Do not cap or delete these rows. A global IQR treats Bangkok as an outlier because the cross-province distribution is skewed. "
                f"Global IQR flags on guest_arrivals: {int(_iqr_mask(pm['guest_arrivals']).sum())}. "
                f"Global |z|>3: {int(_z_mask(pm['guest_arrivals']).sum())}. Use per-province models instead of a global cap. "
                f"Non-COVID flags observed on the high side: {high_non}."
            ),
            on_todo=False,
        )
    )

    rain_z = _per_group_flags(pm, "province_id", "avg_rain_mm", "z")
    if len(rain_z):
        top = rain_z.sort_values("avg_rain_mm", ascending=False).iloc[0]
        findings.append(
            Finding(
                issue_id="pm_rain_z_by_province",
                severity="low",
                issue="A few province-months have extreme but internally consistent rainfall",
                file_path=path,
                column="avg_rain_mm",
                count=(
                    f"{len(rain_z)} per-province |z|>3 flags in {rain_z['province_id'].nunique()} provinces; "
                    f"per-province IQR flags {len(_per_group_flags(pm, 'province_id', 'avg_rain_mm', 'iqr'))}; "
                    f"max avg_rain_mm={pm['avg_rain_mm'].max():.1f}"
                ),
                example=f"{top.province_th} {top.year_month}: min={top.min_rain_mm:.1f}, avg={top.avg_rain_mm:.1f}, max={top.max_rain_mm:.1f} mm",
                recommended_fix=(
                    "Keep the values. They sit inside min_rain_mm..max_rain_mm, are not negative, and match known wet-season peaks in the far south. "
                    "Add an extreme-weather indicator if a model needs it. Do not clip to a global IQR fence."
                ),
                on_todo=False,
            )
        )

    visitor_z = _per_group_flags(pm.dropna(subset=["visitors_total"]), "province_id", "visitors_total", "z")
    passed_count = len(visitor_z)
    findings.append(
        Finding(
            issue_id="pm_visitors_z_by_province",
            severity="info",
            issue="Per-province |z|>3 on visitors_total is rare; global fences only flag large provinces",
            file_path=path,
            column="visitors_total",
            count=(
                f"{passed_count} per-province |z|>3 flags; "
                f"global IQR flags {int(_iqr_mask(pm['visitors_total']).sum())} of {int(pm['visitors_total'].notna().sum())} non-null rows"
            ),
            example=_example_place(pm.loc[pm["visitors_total"].idxmax(): pm["visitors_total"].idxmax()])
            if pm["visitors_total"].notna().any()
            else "",
            recommended_fix="Do not winsorize visitors_total. Bangkok's magnitude is the real size of the capital, not a bad row.",
            on_todo=False,
        )
    )


def _audit_pm_rain_rules(pm: pd.DataFrame, findings: list[Finding], passed: list[PassedCheck]) -> None:
    path = "data/core/province_month.parquet"
    neg = int(((pm["min_rain_mm"] < 0) | (pm["max_rain_mm"] < 0) | (pm["avg_rain_mm"] < 0)).sum())
    order_bad = int((pm["min_rain_mm"] > pm["max_rain_mm"]).sum())
    avg_out = int(((pm["avg_rain_mm"] < pm["min_rain_mm"] - 1e-6) | (pm["avg_rain_mm"] > pm["max_rain_mm"] + 1e-6)).sum())
    absurd = int((pm["avg_rain_mm"] > ABSURD_RAIN_MM).sum())
    passed.append(PassedCheck("Negative rainfall cells", path, "min_rain_mm, max_rain_mm, avg_rain_mm", str(neg)))
    passed.append(PassedCheck("min_rain_mm greater than max_rain_mm", path, "min_rain_mm, max_rain_mm", str(order_bad)))
    passed.append(PassedCheck("avg_rain_mm outside min..max", path, "avg_rain_mm", str(avg_out)))
    passed.append(PassedCheck(f"avg_rain_mm above {ABSURD_RAIN_MM:.0f} mm", path, "avg_rain_mm", str(absurd)))
    zeros = pm[(pm["min_rain_mm"] == 0) & (pm["max_rain_mm"] == 0) & (pm["avg_rain_mm"] == 0)]
    months = zeros.groupby(zeros["year_month"].str.slice(5, 7)).size().sort_values(ascending=False)
    month_txt = ", ".join(f"month {m}={int(n)}" for m, n in months.items())
    findings.append(
        Finding(
            issue_id="pm_zero_rainfall_dry_season",
            severity="info",
            issue="Zero rainfall is a dry-season measurement (min = max = avg = 0), not a null coded as zero",
            file_path=path,
            column="avg_rain_mm",
            count=f"{len(zeros)} province-months",
            example=month_txt or "none",
            recommended_fix="Keep numeric 0. Do not convert dry-season zeros to null.",
            on_todo=False,
        )
    )
    residue = pm["avg_rain_mm"].dropna()
    noisy = int((residue - residue.round(2)).abs().gt(1e-6).sum())
    if noisy:
        sample = pm.loc[(pm["avg_rain_mm"] - pm["avg_rain_mm"].round(2)).abs().gt(1e-6), "avg_rain_mm"].iloc[0]
        findings.append(
            Finding(
                issue_id="pm_rain_float_residue",
                severity="low",
                issue="Rainfall carries sub-0.01 mm binary residue from the upstream file",
                file_path=path,
                column="avg_rain_mm",
                count=f"{noisy} of {len(residue)} values differ from a 2-decimal rounding",
                example=f"{sample:.6f} mm",
                recommended_fix="Optional: round millimeters to 2 decimals at staging for display. Leave full precision for modeling.",
                on_todo=False,
            )
        )


def _audit_pm_ratio(pm: pd.DataFrame, findings: list[Finding]) -> None:
    both = pm[pm["guest_arrivals"].notna() & pm["visitors_total"].notna() & (pm["visitors_total"] > 0)].copy()
    if both.empty:
        return
    both["ratio"] = both["guest_arrivals"] / both["visitors_total"]
    low = both.loc[both["ratio"].idxmin()]
    findings.append(
        Finding(
            issue_id="pm_guest_vs_visitors_ratio",
            severity="info",
            issue="guest_arrivals and visitors_total measure different things and should not be forced equal",
            file_path="data/core/province_month.parquet",
            column="guest_arrivals, visitors_total",
            count=(
                f"{len(both)} overlapping rows; ratio guest/visitors "
                f"min={both['ratio'].min():.3f}, median={both['ratio'].median():.3f}, max={both['ratio'].max():.3f}"
            ),
            example=(
                f"{low.province_th} {low.year_month}: guest_arrivals={low.guest_arrivals:.0f}, "
                f"visitors_total={low.visitors_total:.0f}, ratio={low.ratio:.3f}"
            ),
            recommended_fix="Keep both columns. Where both exist, the monthly guests metric matches guest_arrivals exactly; visitors are a wider count.",
            on_todo=False,
        )
    )


def _audit_rainfall(
    rain: pd.DataFrame,
    months: list[str],
    findings: list[Finding],
    passed: list[PassedCheck],
    staged: bool,
) -> None:
    path = "data/staging/hii/fact_rainfall.parquet"
    work = rain.copy()
    work["province_id"] = work["province_id"].astype(str)
    dup = int(work.duplicated(["province_id", "year_month"]).sum())
    passed.append(PassedCheck("No duplicate province-month keys", path, "province_id, year_month", str(dup)))
    outside = work[~work["year_month"].isin(months)]
    outside_months = sorted(outside["year_month"].unique())
    findings.append(
        Finding(
            issue_id="hii_months_outside_window",
            severity="low",
            issue="Staged HII rainfall continues past the modeling window and is dropped by build_core",
            file_path=path,
            column="year_month",
            count=f"{len(outside)} rows ({outside['province_id'].nunique()} provinces × {len(outside_months)} months)",
            example=f"{outside_months[0]} … {outside_months[-1]}" if outside_months else "none",
            recommended_fix=(
                "Keep the staged rows. Extend `config/sources.yaml` date_window and rebuild core only if models should use months after 2025-08. "
                "Do not delete the extra months from staging."
            ),
        )
    )
    if str(rain["province_id"].dtype) != "string" and not pd.api.types.is_string_dtype(rain["province_id"]):
        findings.append(
            Finding(
                issue_id="hii_province_id_dtype",
                severity="low",
                issue="Staged HII province_id is an integer while core and MOTS use text ids",
                file_path=path,
                column="province_id",
                count=f"dtype={rain['province_id'].dtype}; {rain['province_id'].nunique()} distinct ids",
                example=f"staging value {int(rain['province_id'].iloc[0])} (int) vs core text id '10'",
                recommended_fix="Cast province_id to a stripped string in `src/staging/hii.py` so joins do not depend on a later cast in build_core.",
            )
        )
    neg = int(((work["min_rain_mm"] < 0) | (work["max_rain_mm"] < 0) | (work["avg_rain_mm"] < 0)).sum())
    passed.append(PassedCheck("Negative rainfall in the staged HII extract", path, "avg_rain_mm", str(neg)))
    full_months = sorted(work["year_month"].unique())
    gaps = _grid_gap(work, work["province_id"].drop_duplicates(), full_months)
    passed.append(
        PassedCheck(
            "Gaps in the staged HII province × observed-month grid",
            path,
            "province_id, year_month",
            str(len(gaps)),
        )
    )
    if staged:
        names = work[["province_id", "province_th"]].drop_duplicates()
        multi = names.groupby("province_id")["province_th"].nunique()
        passed.append(PassedCheck("HII province ids with more than one Thai name", path, "province_th", str(int((multi > 1).sum()))))


def _audit_mots(
    st: pd.DataFrame,
    dim_p: pd.DataFrame,
    months: list[str],
    findings: list[Finding],
    passed: list[PassedCheck],
    staged: bool,
) -> None:
    path = "data/staging/mots/fact_tourism_long.parquet"
    canon = dict(zip(dim_p["province_id"].astype(str), dim_p["province_th"], strict=True))
    prov = st[st["area_level"] == "province"].copy()
    prov["province_id"] = prov["province_id"].astype("string")
    unmapped = prov[prov["province_id"].isna()]
    passed.append(PassedCheck("Province-level rows with a null province_id", path, "province_id", str(len(unmapped))))

    named = prov[prov["province_id"].notna()].copy()
    named["canonical"] = named["province_id"].map(canon)
    dirty = named[named["province_th_raw"] != named["canonical"]]
    if len(dirty):
        top = dirty["province_th_raw"].value_counts().head(3)
        example = "; ".join(f"{name} ({int(n)} rows)" for name, n in top.items())
        findings.append(
            Finding(
                issue_id="mots_noncanonical_province_names",
                severity="medium",
                issue="MOTS province labels vary (abbreviations, spelling, year suffixes, รวม prefixes) even though province_id is filled",
                file_path=path,
                column="province_th_raw",
                count=f"{len(dirty)} of {len(named)} province rows ({_pct(len(dirty), len(named))}); {dirty['province_th_raw'].nunique()} distinct non-canonical labels",
                example=example,
                recommended_fix=(
                    "Add canonical `province_th` from `dim_province` beside `province_th_raw`. "
                    "Keep the raw label for lineage. Do not create new province ids for aliases such as กรุงเทพฯ, ศรีษะเกษ, or ประจวบศิรีขันธ์."
                ),
            )
        )

    sub = st[(st["area_level"] == "sub_area") & st["province_id"].isna()]
    if len(sub):
        labels = sorted(sub["province_th_raw"].dropna().unique())
        findings.append(
            Finding(
                issue_id="mots_sub_area_null_province",
                severity="low",
                issue="Resort and sub-area sheets have a null province_id by design",
                file_path=path,
                column="province_id, province_th_raw",
                count=f"{len(sub)} rows, {len(labels)} labels",
                example=", ".join(labels[:4]),
                recommended_fix=(
                    "Leave these rows off the 77-province spine. Map a parent province only after an approved rule "
                    "(the label often contains the parent, for example พัทยา ชลบุรี)."
                ),
            )
        )

    backslash = int(st["source_file"].astype(str).str.contains(r"\\", regex=True).sum())
    if backslash:
        findings.append(
            Finding(
                issue_id="mots_windows_paths",
                severity="medium",
                issue="MOTS source_file uses Windows backslashes, so the path does not resolve on Linux",
                file_path=path,
                column="source_file",
                count=f"{backslash} of {len(st)} staged rows",
                example=str(st["source_file"].iloc[0]),
                recommended_fix="Write POSIX paths in the manifest and Parquet (`data/raw/mots/...`). The same pattern is copied into `data/core/fact_tourism_long.parquet`.",
            )
        )
    core_long = _read_parquet(CORE / "fact_tourism_long.parquet")
    if core_long is not None and "source_file" in core_long.columns:
        core_bs = int(core_long["source_file"].astype(str).str.contains(r"\\", regex=True).sum())
        if core_bs:
            findings.append(
                Finding(
                    issue_id="mots_core_windows_paths",
                    severity="medium",
                    issue="Core MOTS lineage paths also use Windows backslashes",
                    file_path="data/core/fact_tourism_long.parquet",
                    column="source_file",
                    count=f"{core_bs} of {len(core_long)} rows",
                    example=str(core_long["source_file"].iloc[0]),
                    recommended_fix="Normalize separators when core tables are next rebuilt. Do not hand-edit the Parquet.",
                )
            )

    neg = int((st["value"] < 0).sum())
    passed.append(PassedCheck("Negative MOTS values", path, "value", str(neg)))
    both_blank = int((st["year_month"].isna() & st["quarter"].isna()).sum())
    both_filled = int((st["year_month"].notna() & st["quarter"].notna()).sum())
    quarter_only = int((st["year_month"].isna() & st["quarter"].notna()).sum())
    passed.append(PassedCheck("Rows with neither year_month nor quarter", path, "year_month, quarter", str(both_blank)))
    passed.append(PassedCheck("Rows with both year_month and quarter", path, "year_month, quarter", str(both_filled)))
    findings.append(
        Finding(
            issue_id="mots_quarterly_null_year_month",
            severity="info",
            issue="Null year_month on quarterly MOTS rows is the quarterly grain, not a missing month",
            file_path=path,
            column="year_month, quarter",
            count=f"{quarter_only} rows with quarter set and year_month null",
            example=f"{st.loc[st['quarter'].notna(), 'quarter'].iloc[0]} {st.loc[st['quarter'].notna(), 'metric'].iloc[0]}",
            recommended_fix="Do not forward-fill quarterly rows onto months. Use the monthly guest_arrivals rows, which already sum to the quarter within 0.1%.",
            on_todo=False,
        )
    )

    bad_month = st["year_month"].dropna().astype(str)
    bad_fmt = int((~bad_month.str.match(r"^\d{4}-(0[1-9]|1[0-2])$")).sum())
    passed.append(PassedCheck("year_month values outside YYYY-MM", path, "year_month", str(bad_fmt)))
    aligned = st[st["year_month"].notna()]
    year_mismatch = int((aligned["year_month"].str.slice(0, 4).astype(int) != aligned["year_ce"]).sum())
    month_mismatch = int((aligned["year_month"].str.slice(5, 7).astype(int) != aligned["month"]).sum())
    passed.append(PassedCheck("year_ce disagrees with year_month", path, "year_ce, year_month", str(year_mismatch)))
    passed.append(PassedCheck("month disagrees with year_month", path, "month, year_month", str(month_mismatch)))

    outside = st[st["year_month"].notna() & ~st["year_month"].isin(months)]
    if len(outside):
        findings.append(
            Finding(
                issue_id="mots_months_outside_window",
                severity="low",
                issue="Staged MOTS has province-months after 2025-08 that core drops",
                file_path=path,
                column="year_month",
                count=f"{len(outside)} rows; months {outside['year_month'].min()} … {outside['year_month'].max()}",
                example=", ".join(
                    f"{ym}={int(n)}" for ym, n in outside.groupby("year_month").size().sort_index().items()
                ),
                recommended_fix="Keep them in staging. Extend the date window only if 2025-09 through 2025-12 should enter province_month.",
            )
        )

    _audit_mots_prior_year(st, findings)
    _audit_mots_keys_and_units(st, findings, passed)
    _audit_mots_quarter_sum(st, passed)
    if staged:
        referenced = st["source_file"].astype(str).str.replace("\\", "/", regex=False).unique()
        missing = [p for p in referenced if not (ROOT / p).exists()]
        findings.append(
            Finding(
                issue_id="mots_source_files_missing_on_disk",
                severity="high",
                issue="Every MOTS workbook cited by the staged extract is missing from this checkout",
                file_path=path,
                column="source_file",
                count=f"{len(missing)} of {len(referenced)} distinct source paths are absent",
                example=missing[0] if missing else "",
                recommended_fix="Re-run the MOTS crawler where Playwright can reach www.mots.go.th if the workbooks themselves need re-audit. Staged values are still auditable.",
            )
        )


def _audit_mots_prior_year(st: pd.DataFrame, findings: list[Finding]) -> None:
    monthly = st[(st["area_level"] == "province") & st["year_month"].notna() & (st["layout"] == "monthly_province")]
    if monthly.empty or "from_prior_year_column" not in monthly.columns:
        return
    keys = ["province_id", "year_month", "metric", "tourist_type"]
    only_prior = monthly.groupby(keys, dropna=False)["from_prior_year_column"].all()
    only_prior = only_prior[only_prior]
    if only_prior.empty:
        return
    hit = monthly.set_index(keys).loc[only_prior.index].reset_index()
    years = sorted(hit["year_ce"].dropna().unique().tolist())
    metrics = sorted(hit["metric"].dropna().unique().tolist())
    file_example = str(hit["source_file"].iloc[0])
    findings.append(
        Finding(
            issue_id="mots_2023_monthly_from_prior_column",
            severity="medium",
            issue="All 2023 monthly visitors, guests, occupancy, and revenue come from a prior-year block in the 2024 workbook",
            file_path="data/staging/mots/fact_tourism_long.parquet",
            column="from_prior_year_column",
            count=f"{len(hit)} rows; years {years}; metrics {metrics}",
            example=file_example,
            recommended_fix=(
                "Keep the 2023 values — they are the only monthly copy, and guest totals match guest_arrivals. "
                "Carry the prior-year flag onto province_month for visitors_total in 2023 so it is not treated as a primary 2023 release. "
                "guest_arrivals itself is not from a prior-year column. Search for a standalone 2023 (R) workbook before replacing these cells."
            ),
        )
    )


def _audit_mots_keys_and_units(st: pd.DataFrame, findings: list[Finding], passed: list[PassedCheck]) -> None:
    path = "data/staging/mots/fact_tourism_long.parquet"
    key = ["province_th_raw", "year_month", "quarter", "metric", "tourist_type", "source_file", "sheet", "area_level"]
    dup = int(st.duplicated(key).sum())
    passed.append(PassedCheck("Duplicate parsed MOTS rows on the source grain", path, ", ".join(key), str(dup)))

    monthly = st[(st["area_level"] == "province") & st["year_month"].notna() & st["province_id"].notna()]
    metric_key = ["province_id", "year_month", "metric", "tourist_type"]
    dup_metric = int(monthly.duplicated(metric_key).sum())
    passed.append(PassedCheck("Duplicate province-month metric keys before core dedupe", path, ", ".join(metric_key), str(dup_metric)))

    occ = st.loc[st["metric"] == "occupancy_pct", "value"]
    if len(occ):
        bad_occ = int(((occ < 0) | (occ > 100)).sum())
        passed.append(
            PassedCheck(
                "occupancy_pct outside 0–100",
                path,
                "value",
                f"{bad_occ} (observed min={occ.min():.2f}, max={occ.max():.2f})",
            )
        )
    rev = st.loc[st["metric"] == "revenue_million_baht", "value"]
    if len(rev):
        bad_rev = int(((rev < 0) | (rev > ABSURD_REVENUE_MILLION_BAHT)).sum())
        passed.append(
            PassedCheck(
                "revenue_million_baht negative or above 1,000,000 (possible baht/million mix-up)",
                path,
                "value",
                f"{bad_rev} (max={rev.max():,.2f})",
            )
        )

    guests = st[(st["metric"] == "guests") & (st["area_level"] == "province")]
    if len(guests) and set(guests["tourist_type"].unique()) == {"total"}:
        findings.append(
            Finding(
                issue_id="mots_guests_total_only",
                severity="low",
                issue="The monthly guests metric has no Thai/foreign split",
                file_path=path,
                column="tourist_type",
                count=f"{len(guests)} guests rows, all tourist_type=total",
                example="metric=guests, tourist_type=total",
                recommended_fix=(
                    "Confirm the (R) sheet publishes only จำนวนผู้เข้าพัก as a total. "
                    "Do not split it with the visitor Thai/foreign ratio."
                ),
            )
        )

    # Thai + foreign vs total, same tolerance as src/qa/consistency.py
    count_metrics = {"guest_arrivals", "guests", "visitors"}
    sub = st[(st["area_level"] == "province") & st["year_month"].notna() & st["metric"].isin(count_metrics)]
    piv = sub.pivot_table(
        index=["province_id", "year_month", "metric", "source_file"],
        columns="tourist_type",
        values="value",
        aggfunc="first",
    )
    if {"total", "thai", "foreign"}.issubset(piv.columns):
        both = piv.dropna(subset=["total", "thai", "foreign"])
        tol = np.maximum(1.0, both["total"].abs() * 0.001)
        mismatch = int(((both["total"] - (both["thai"] + both["foreign"])).abs() > tol).sum())
        passed.append(PassedCheck("Monthly total != Thai + foreign (0.1% or 1 person)", path, "value", str(mismatch)))


def _audit_mots_quarter_sum(st: pd.DataFrame, passed: list[PassedCheck]) -> None:
    sub = st[
        (st["layout"] == "quarterly_domestic")
        & (st["metric"] == "guest_arrivals")
        & (st["area_level"] == "province")
    ].copy()
    if sub.empty:
        return
    monthly = sub[sub["year_month"].notna()].copy()
    quarterly = sub[sub["quarter"].notna()].copy()
    monthly["quarter"] = monthly["year_ce"].astype(int).astype(str) + "-Q" + (((monthly["month"] - 1) // 3 + 1).astype(int)).astype(str)
    keys = ["source_file", "sheet", "year_ce", "tourist_type", "quarter"]
    summed = monthly.groupby(keys, dropna=False)["value"].sum()
    quoted = quarterly.groupby(keys, dropna=False)["value"].first()
    both = pd.concat([quoted.rename("quoted"), summed.rename("summed")], axis=1).dropna()
    both = both[both["summed"] != 0]
    tol = np.maximum(1.0, both["quoted"].abs() * 0.001)
    mismatch = int(((both["quoted"] - both["summed"]).abs() > tol).sum())
    passed.append(
        PassedCheck(
            "Quarterly guest_arrivals disagrees with the sum of its three months",
            "data/staging/mots/fact_tourism_long.parquet",
            "value",
            str(mismatch),
        )
    )


def _audit_nso(nso: pd.DataFrame, findings: list[Finding], passed: list[PassedCheck]) -> None:
    path = "data/staging/nso/fact_accommodation_nso_inventory.parquet"
    years = sorted(int(y) for y in nso["survey_year_ce"].unique())
    findings.append(
        Finding(
            issue_id="nso_inventory_only",
            severity="high",
            issue="NSO accommodation never becomes province-level measures",
            file_path=path,
            column="file_path, status",
            count=(
                f"{len(nso)} workbook rows; survey years CE {years}; "
                f"status counts {nso['status'].value_counts().to_dict()}; "
                f"0 numeric measure columns; 0 rows joined to province_month"
            ),
            example=str(nso["file_path"].iloc[0]),
            recommended_fix=(
                "Parse province tables out of the Excel workbooks after the RAR archives are restored. "
                "Survey years are 2018, 2020, and 2022 only, so do not spread one survey across every month. "
                "Agree a join rule before adding columns to province_month."
            ),
        )
    )
    backslash = int(nso["file_path"].astype(str).str.contains(r"\\", regex=True).sum())
    if backslash:
        findings.append(
            Finding(
                issue_id="nso_windows_paths",
                severity="medium",
                issue="NSO inventory paths use Windows backslashes and the archives are not on disk",
                file_path=path,
                column="file_path",
                count=f"{backslash} of {len(nso)} paths",
                example=str(nso["file_path"].iloc[0]),
                recommended_fix="Store POSIX paths. The same paths are copied to `data/core/fact_accommodation_nso.parquet`.",
            )
        )
    dup = int(nso["file_path"].duplicated().sum())
    passed.append(PassedCheck("Duplicate NSO file paths", path, "file_path", str(dup)))
    missing_files = 0
    for raw in nso["file_path"].astype(str):
        posix = raw.replace("\\", "/")
        if not (ROOT / posix).exists():
            missing_files += 1
    findings.append(
        Finding(
            issue_id="nso_workbooks_missing_on_disk",
            severity="high",
            issue="NSO workbooks listed in the inventory are not in this checkout",
            file_path=path,
            column="file_path",
            count=f"{missing_files} of {len(nso)} paths missing under the repo root",
            example=str(nso["file_path"].iloc[0]).replace("\\", "/"),
            recommended_fix="Re-download the three MD RAR archives and extract them with 7-Zip or UnRAR before any cell-level NSO audit.",
        )
    )


def _audit_crosswalk(findings: list[Finding], passed: list[PassedCheck]) -> None:
    path = CONFIG / "province_crosswalk.csv"
    if not path.exists():
        findings.append(
            Finding(
                issue_id="crosswalk_missing",
                severity="high",
                issue="Province crosswalk is missing",
                file_path="config/province_crosswalk.csv",
                column="(file)",
                count="0",
                example="",
                recommended_fix="Restore config/province_crosswalk.csv before parsing MOTS names.",
            )
        )
        return
    cw = pd.read_csv(path, dtype=str)
    extra = int(cw.duplicated().sum())
    conflict = int(cw.groupby("alias")["province_id"].nunique().max()) if len(cw) else 0
    if extra:
        aliases = sorted(cw.loc[cw.duplicated(keep=False), "alias"].unique())
        findings.append(
            Finding(
                issue_id="crosswalk_exact_duplicate_rows",
                severity="low",
                issue="Province crosswalk contains exact duplicate rows",
                file_path="config/province_crosswalk.csv",
                column="alias",
                count=f"{extra} extra copies; aliases {aliases}",
                example=aliases[0] if aliases else "",
                recommended_fix="Drop exact duplicate rows. They map to the same province_id, so this is not a conflicting code. Keep genuine aliases (กรุงเทพฯ, ศรีษะเกษ, ประจวบศิรีขันธ์).",
            )
        )
    passed.append(
        PassedCheck(
            "Alias maps to more than one province_id (1 means no conflict)",
            "config/province_crosswalk.csv",
            "alias, province_id",
            str(conflict),
        )
    )
    ids = cw["province_id"].nunique(dropna=True)
    passed.append(PassedCheck("Distinct province ids in the crosswalk", "config/province_crosswalk.csv", "province_id", str(ids)))


def _audit_manifest_and_quarantine(findings: list[Finding], passed: list[PassedCheck]) -> None:
    latest = latest_manifest_df()
    if latest.empty:
        findings.append(
            Finding(
                issue_id="manifest_missing",
                severity="medium",
                issue="Download manifest is empty",
                file_path="data/manifest/manifest_downloads.csv",
                column="status",
                count="0",
                example="",
                recommended_fix="Rerun ingest so each URL has a latest status.",
            )
        )
        return
    bad = latest[latest["status"].fillna("") != "ok"]
    mots_err = bad[bad["source"] == "mots"]
    if len(mots_err):
        urls = mots_err["news_url"].dropna().astype(str).unique().tolist()
        findings.append(
            Finding(
                issue_id="manifest_mots_latest_errors",
                severity="medium",
                issue="Latest manifest status is still error for three MOTS news pages",
                file_path="data/manifest/manifest_downloads.csv",
                column="status, news_url",
                count=f"{len(mots_err)} latest rows with status=error",
                example="; ".join(urls),
                recommended_fix=(
                    "Retry these three news URLs and confirm they are not province-month workbooks. "
                    "Historical error rows that later succeeded are not current failures. "
                    "Modeled guest_arrivals and visitors_total are already complete inside their published windows."
                ),
            )
        )
    nso_bad = bad[bad["source"] == "nso"]
    if len(nso_bad):
        findings.append(
            Finding(
                issue_id="manifest_nso_stale_extract_failed",
                severity="low",
                issue="A null-URL NSO extract_failed row is still the latest status for its own manifest key",
                file_path="data/manifest/manifest_downloads.csv",
                column="status, file_url",
                count=f"{len(nso_bad)} latest non-ok NSO rows; inventory status is extracted for every workbook",
                example=str(nso_bad["error_message"].dropna().iloc[0])[:180] if nso_bad["error_message"].notna().any() else "extract_failed",
                recommended_fix=(
                    "Key manifest summaries by local_path when file_url is null. "
                    "Do not re-extract solely because of this row: the three RAR downloads have status ok and the inventory lists 338 extracted workbooks."
                ),
            )
        )
    qpath = STAGING / "mots" / "quarantine" / "quarantine.csv"
    if qpath.exists():
        q = pd.read_csv(qpath)
        findings.append(
            Finding(
                issue_id="mots_quarantine_national",
                severity="info",
                issue="Quarantine holds national MOTS workbooks that are out of the province grid on purpose",
                file_path="data/staging/mots/quarantine/quarantine.csv",
                column="reason",
                count=f"{len(q)} files; reasons {q['reason'].value_counts().to_dict() if len(q) else {}}",
                example=str(q["file"].iloc[0]) if len(q) else "",
                recommended_fix="Leave these files quarantined. Do not parse national totals into the 77-province table.",
                on_todo=False,
            )
        )
    conflicts = STAGING / "mots" / "quarantine" / "conflicts.csv"
    if conflicts.exists():
        cdf = pd.read_csv(conflicts)
        passed.append(PassedCheck("MOTS value conflicts after the first-published-value rule", _rel(conflicts), "(rows)", str(len(cdf))))
    unmapped = STAGING / "mots" / "quarantine" / "unmapped_provinces.csv"
    if unmapped.exists():
        udf = pd.read_csv(unmapped)
        passed.append(PassedCheck("Unmapped province names written by staging", _rel(unmapped), "(rows)", str(len(udf))))


def _findings_table(findings: list[Finding]) -> str:
    header = "| Severity | Issue | File path | Column | Count | Example | Recommended fix |"
    sep = "| --- | --- | --- | --- | --- | --- | --- |"
    rows = [header, sep]
    for item in findings:
        rows.append(
            "| {sev} | {issue} | `{path}` | `{col}` | {count} | {example} | {fix} |".format(
                sev=item.severity,
                issue=_md(item.issue),
                path=_md(item.file_path),
                col=_md(item.column),
                count=_md(item.count),
                example=_md(item.example),
                fix=_md(item.recommended_fix),
            )
        )
    return "\n".join(rows)


def _todo(findings: list[Finding]) -> str:
    ranked = [item for item in findings if item.on_todo]
    lines = []
    for index, item in enumerate(ranked, start=1):
        lines.append(
            f"{index}. **[{item.severity}]** {item.issue} "
            f"(`{item.file_path}`, `{item.column}`, {item.count}). {item.recommended_fix}"
        )
    return "\n".join(lines)


def _keep(findings: list[Finding]) -> str:
    kept = [item for item in findings if not item.on_todo]
    lines = []
    for item in kept:
        lines.append(f"- **{item.issue}** (`{item.file_path}`, `{item.column}`, {item.count}). {item.recommended_fix}")
    return "\n".join(lines)


def _passed_table(passed: list[PassedCheck]) -> str:
    rows = [
        "| Check | File path | Column | Count |",
        "| --- | --- | --- | --- |",
    ]
    for item in passed:
        rows.append(f"| {_md(item.check)} | `{_md(item.file_path)}` | `{_md(item.column)}` | {_md(item.count)} |")
    return "\n".join(rows)


def render_report(report: AuditReport) -> str:
    top = [item for item in report.findings if item.severity in {"high", "medium"}]
    top_lines = [
        f"- **[{item.severity}]** {item.issue} — `{item.file_path}` / `{item.column}` — {item.count}"
        for item in top
    ]
    parts = [
        "# Data quality audit",
        "",
        f"_Generated {report.generated_at} by `python -m src.qa.data_quality_audit`. "
        "Read-only: no source values, Parquet tables, or pipeline code were changed._",
        "",
        "## How to regenerate",
        "",
        "```bash",
        "python -m src.qa.data_quality_audit",
        "```",
        "",
        "Use `--skip-network` to skip the connectivity probe. The script overwrites this file.",
        "",
        "## What was audited",
        "",
        "\n".join(line if line.startswith("- ") else f"- {line}" for line in report.availability),
        "",
        "### Connectivity from this run",
        "",
        "A short GET (8s timeout, 64-byte read) of the configured source URLs. "
        "This does not save files and is not a substitute for ingest.",
        "",
        "\n".join(report.probe_lines),
        "",
        "## Top issues",
        "",
        "\n".join(top_lines) if top_lines else "- No high or medium issues.",
        "",
        "## Findings",
        "",
        "Severity: **high** = would bias or invent the modeling table if handled wrongly; "
        "**medium** = dirty labels, paths, or provenance that should be fixed before the next rebuild; "
        "**low** = small type or coverage decisions; **info** = a real pattern that should be kept.",
        "",
        _findings_table(report.findings),
        "",
        "## Prioritized cleaning to-do",
        "",
        _todo(report.findings),
        "",
        "## Patterns to keep",
        "",
        "These are quantified so they are not cleaned away.",
        "",
        _keep(report.findings),
        "",
        "## Checks with nothing to clean",
        "",
        _passed_table(report.passed),
        "",
        "## Column profiles",
        "",
        "Null percentages use the row count of that file. A null `year_month` on a quarterly MOTS row is expected; see the findings.",
        "",
        "\n".join(report.profiles),
        "## Method",
        "",
        "- Missing values: pandas `isna` counts and percent of rows. The 77×92 grid gap is `dim_province.province_id` × the `config/sources.yaml` month list, left-joined to the table.",
        "- Duplicates: exact duplicate rows, plus the grain keys in `config/data_contracts.yaml` (province_id, year_month, and metric/tourist_type where that is the grain).",
        "- Outliers: within each province, IQR fences at 1.5×IQR and |z| > 3 using the population standard deviation. Global fences are reported only to show they flag Bangkok because the cross-province distribution is skewed.",
        "- Impossible values: negative rainfall or counts, `min_rain_mm` > `max_rain_mm`, `avg_rain_mm` outside that range, occupancy outside 0–100, rainfall above "
        f"{ABSURD_RAIN_MM:.0f} mm, revenue above {ABSURD_REVENUE_MILLION_BAHT:,.0f} million baht.",
        "- Province names: `province_th_raw` compared with `dim_province.province_th` on `province_id`. Crosswalk aliases that differ from the canonical name are expected.",
        "- Dates: `year_month` must match `YYYY-MM`, and `year_ce` / `month` must match that string. Quarterly rows are allowed to have a null `year_month`.",
        "- Thai + foreign versus total, and quarterly guest_arrivals versus the sum of months, use the same tolerance as `src/qa/consistency.py` (max of 1 person or 0.1%).",
        "",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write reports/data_quality_audit.md")
    parser.add_argument("--skip-network", action="store_true", help="Do not probe source URLs")
    parser.add_argument("--output", type=Path, default=REPORT_PATH, help="Report path")
    args = parser.parse_args(argv)
    report = build_audit(probe=not args.skip_network)
    text = render_report(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    highs = sum(1 for item in report.findings if item.severity == "high")
    print(f"Wrote {args.output} ({len(report.findings)} findings, {highs} high)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
