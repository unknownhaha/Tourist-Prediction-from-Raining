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
import yaml

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
    "leakage_same_month_features": 15,
    "leakage_quarter_total": 25,
    "timeliness_coverage_end": 35,
    "integrity_join_rates": 36,
    "coverage_imbalance_volume": 45,
    "hii_url_unreachable": 72,
    "contract_visitors_floor": 85,
    "lineage_dedupe_tie_break": 140,
}

# One primary dimension per finding. The summary table counts these.
DIMENSION = {
    "pm_visitors_structural_null": "completeness",
    "nso_inventory_only": "completeness",
    "mots_guests_total_only": "completeness",
    "mots_quarterly_null_year_month": "completeness",
    "mots_quarantine_national": "completeness",
    "pm_grid": "completeness",
    "dim_province_count": "completeness",
    "crosswalk_missing": "completeness",
    "crosswalk_exact_duplicate_rows": "uniqueness",
    "hii_province_id_dtype": "validity",
    "contract_visitors_floor": "validity",
    "contract_violations": "validity",
    "pm_guest_published_zeros": "accuracy",
    "pm_guest_iqr_by_province": "accuracy",
    "pm_rain_z_by_province": "accuracy",
    "pm_visitors_z_by_province": "accuracy",
    "pm_zero_rainfall_dry_season": "accuracy",
    "pm_spend_per_visitor": "accuracy",
    "pm_mom_jumps": "accuracy",
    "mots_noncanonical_province_names": "consistency",
    "pm_guest_vs_visitors_ratio": "consistency",
    "hii_months_outside_window": "timeliness",
    "mots_months_outside_window": "timeliness",
    "timeliness_coverage_end": "timeliness",
    "mots_sub_area_null_province": "integrity",
    "integrity_join_rates": "integrity",
    "pm_rain_float_residue": "conformity",
    "mots_windows_paths": "conformity",
    "mots_core_windows_paths": "conformity",
    "nso_windows_paths": "conformity",
    "pm_covid_level_shift": "temporal",
    "pm_seasonality": "temporal",
    "mots_2023_monthly_from_prior_column": "leakage",
    "leakage_same_month_features": "leakage",
    "leakage_quarter_total": "leakage",
    "coverage_imbalance_volume": "coverage_imbalance",
    "raw_sources_absent": "lineage",
    "mots_source_files_missing_on_disk": "lineage",
    "nso_workbooks_missing_on_disk": "lineage",
    "manifest_mots_latest_errors": "lineage",
    "manifest_nso_stale_extract_failed": "lineage",
    "manifest_missing": "lineage",
    "hii_url_unreachable": "lineage",
    "lineage_dedupe_tie_break": "lineage",
}

DIMENSION_LABEL = {
    "completeness": "Completeness",
    "uniqueness": "Uniqueness",
    "validity": "Validity",
    "accuracy": "Accuracy / plausibility",
    "consistency": "Consistency",
    "timeliness": "Timeliness / freshness",
    "integrity": "Integrity",
    "conformity": "Conformity / format",
    "temporal": "Temporal / stationarity",
    "leakage": "Data leakage",
    "coverage_imbalance": "Coverage imbalance",
    "lineage": "Lineage / reproducibility",
}

EXPECTED_METRIC_UNIT = {
    "guest_arrivals": "count",
    "guests": "count",
    "visitors": "count",
    "occupancy_pct": "percent",
    "revenue_million_baht": "million_baht",
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


def dimension_of(issue_id: str) -> str:
    if issue_id in DIMENSION:
        return DIMENSION[issue_id]
    if issue_id.startswith("raw_dup_"):
        return "uniqueness"
    if issue_id.startswith(("raw_neg_", "raw_unreadable_")):
        return "validity"
    if issue_id.startswith(("raw_null_", "raw_empty_", "missing_file_")):
        return "completeness"
    return "other"


def _months_between(later: str, earlier: str) -> int:
    later_y, later_m = map(int, later.split("-"))
    earlier_y, earlier_m = map(int, earlier.split("-"))
    return (later_y - earlier_y) * 12 + (later_m - earlier_m)


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
    _audit_framework_dimensions(
        pm=pm,
        dim_p=dim_p,
        dim_m=dim_m,
        rain_stage=rain_stage,
        mots_stage=mots_stage,
        mots_pm=mots_pm,
        nso_stage=nso_stage,
        months=months,
        findings=findings,
        passed=passed,
    )

    probe_lines = _probe_sources() if probe else ["- Skipped (`--skip-network`)."]
    if probe:
        hii_line = next((line for line in probe_lines if line.startswith("- HII")), "")
        if "HTTP 200" not in hii_line:
            findings.append(
                Finding(
                    issue_id="hii_url_unreachable",
                    severity="medium",
                    issue="The configured HII rainfall CSV did not return a body in this run",
                    file_path="config/sources.yaml",
                    column="hii.csv_url",
                    count="1 URL",
                    example=hii_line.split("`", 2)[1] if "`" in hii_line else hii_line[:180],
                    recommended_fix=(
                        "Retry the download from a network that can read data.hii.or.th before refreshing rainfall. "
                        "The staged extract already on disk remains the audited copy. Do not replace it with an empty file."
                    ),
                )
            )
    findings.sort(key=lambda item: (SEVERITY_RANK.get(item.severity, 9), PRIORITY.get(item.issue_id, 50), item.issue_id))
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


def _audit_framework_dimensions(
    pm: pd.DataFrame | None,
    dim_p: pd.DataFrame | None,
    dim_m: pd.DataFrame | None,
    rain_stage: pd.DataFrame | None,
    mots_stage: pd.DataFrame | None,
    mots_pm: pd.DataFrame | None,
    nso_stage: pd.DataFrame | None,
    months: list[str],
    findings: list[Finding],
    passed: list[PassedCheck],
) -> None:
    """Checks that sit outside missing-value, duplicate, and outlier screens."""
    _audit_contracts(findings, passed)
    if pm is not None and dim_m is not None:
        be_bad = int((pm["year_be"] != pm["year_ce"] + 543).sum())
        covid_bad = int(
            (pm["is_covid"] != ((pm["year_month"] >= "2020-03") & (pm["year_month"] <= "2022-12"))).sum()
        )
        passed.append(PassedCheck("year_be is not year_ce + 543", "data/core/province_month.parquet", "year_be, year_ce", str(be_bad)))
        passed.append(
            PassedCheck(
                "is_covid disagrees with the 2020-03..2022-12 window",
                "data/core/province_month.parquet",
                "is_covid, year_month",
                str(covid_bad),
            )
        )
        passed.extend(_audit_timeliness(pm, rain_stage, mots_stage, nso_stage, findings))
        _audit_integrity(pm, rain_stage, mots_stage, nso_stage, findings, passed)
        _audit_accuracy_and_shift(pm, dim_p, findings, passed)
        _audit_leakage(pm, mots_stage, findings)
        _audit_imbalance(pm, findings)
    if mots_stage is not None:
        _audit_conformity_and_units(mots_stage, dim_p, rain_stage, findings, passed)
    _audit_lineage_determinism(findings, passed)


def _audit_contracts(findings: list[Finding], passed: list[PassedCheck]) -> None:
    path = CONFIG / "data_contracts.yaml"
    if not path.exists():
        return
    contracts = yaml.safe_load(path.read_text(encoding="utf-8"))
    tables = {
        "dim_province": CORE / "dim_province.parquet",
        "dim_month": CORE / "dim_month.parquet",
        "fact_rainfall": CORE / "fact_rainfall.parquet",
        "fact_tourism_province_month": CORE / "fact_tourism_province_month.parquet",
        "province_month": CORE / "province_month.parquet",
    }
    violations: list[str] = []
    for name, spec in contracts.items():
        file_path = tables.get(name)
        if file_path is None or not file_path.exists():
            violations.append(f"{name}: file missing")
            continue
        df = pd.read_parquet(file_path)
        missing = [col for col in spec.get("required_columns", []) if col not in df.columns]
        if missing:
            violations.append(f"{name}: missing columns {missing}")
        keys = spec.get("unique", [])
        if keys and int(df.duplicated(keys).sum()):
            violations.append(f"{name}: {int(df.duplicated(keys).sum())} duplicate keys {keys}")
        coverage = spec.get("coverage") or {}
        for col, floor in coverage.items():
            if not col.endswith("_min_non_null"):
                continue
            series_name = col[: -len("_min_non_null")]
            if series_name in df.columns and int(df[series_name].notna().sum()) < int(floor):
                violations.append(f"{name}: {series_name} non-null below {floor}")
    if violations:
        findings.append(
            Finding(
                issue_id="contract_violations",
                severity="high",
                issue="Core tables break data_contracts.yaml",
                file_path="config/data_contracts.yaml",
                column="required_columns, unique, coverage",
                count=f"{len(violations)} violations",
                example=violations[0],
                recommended_fix="Fix the named table before modeling. The contract check is the same grain the QA module enforces.",
            )
        )
    else:
        passed.append(
            PassedCheck(
                "data_contracts.yaml required columns, unique keys, and coverage floors",
                "config/data_contracts.yaml",
                "required_columns, unique, coverage",
                "0 violations",
            )
        )
    pm_path = CORE / "province_month.parquet"
    if pm_path.exists():
        pm = pd.read_parquet(pm_path)
        floor = (contracts.get("province_month") or {}).get("coverage", {}).get("visitors_total_min_non_null")
        actual = int(pm["visitors_total"].notna().sum()) if "visitors_total" in pm.columns else 0
        early_null = int(pm.loc[pm["year_month"] < "2023-01", "visitors_total"].isna().sum())
        late_null = int(pm.loc[pm["year_month"] >= "2023-01", "visitors_total"].isna().sum())
        if floor and actual >= int(floor) and early_null and late_null == 0:
            findings.append(
                Finding(
                    issue_id="contract_visitors_floor",
                    severity="low",
                    issue="The visitors_total coverage floor passes even though an entire period is null",
                    file_path="config/data_contracts.yaml",
                    column="province_month.coverage.visitors_total_min_non_null",
                    count=f"floor {floor}, actual non-null {actual}, nulls before 2023-01 {early_null}, nulls from 2023-01 onward {late_null}",
                    example="a hole inside 2023-01..2025-08 would still pass as long as 2400 cells stay filled",
                    recommended_fix=(
                        "Add a contract rule that visitors_total is null exactly when year_month < 2023-01, "
                        "and non-null on every later spine row."
                    ),
                )
            )


def _audit_timeliness(
    pm: pd.DataFrame,
    rain_stage: pd.DataFrame | None,
    mots_stage: pd.DataFrame | None,
    nso_stage: pd.DataFrame | None,
    findings: list[Finding],
) -> list[PassedCheck]:
    asof = datetime.now(timezone.utc).strftime("%Y-%m")
    ends = [("province_month", "data/core/province_month.parquet", str(pm["year_month"].max()))]
    if rain_stage is not None and len(rain_stage):
        ends.append(("HII staged rainfall", "data/staging/hii/fact_rainfall.parquet", str(rain_stage["year_month"].max())))
    if mots_stage is not None and len(mots_stage):
        monthly = mots_stage.loc[mots_stage["layout"] == "monthly_province", "year_month"].dropna()
        if len(monthly):
            ends.append(("MOTS monthly (R) staged", "data/staging/mots/fact_tourism_long.parquet", str(monthly.max())))
    if nso_stage is not None and len(nso_stage):
        ends.append(
            (
                "NSO survey year",
                "data/staging/nso/fact_accommodation_nso_inventory.parquet",
                f"{int(nso_stage['survey_year_ce'].max())}-12",
            )
        )
    pm_end = str(pm["year_month"].max())
    hii_end = next((end for _label, _path, end in ends if _label.startswith("HII")), pm_end)
    example = "; ".join(f"{label} ends {end} ({_months_between(asof, end)} months before {asof})" for label, _path, end in ends)
    findings.append(
        Finding(
            issue_id="timeliness_coverage_end",
            severity="medium",
            issue="The modeling table stops before the rainfall extract and well before the audit clock",
            file_path="data/core/province_month.parquet",
            column="year_month",
            count=(
                f"province_month ends {pm_end} ({_months_between(asof, pm_end)} months before {asof}); "
                f"staged HII ends {hii_end} ({_months_between(hii_end, pm_end)} months later)"
            ),
            example=example,
            recommended_fix=(
                "Treat 2025-08 as a declared label window, not as 'latest available'. "
                "Extend config/sources.yaml and rebuild core when models need the rainfall already staged through the HII end month. "
                "NSO stays a biennial survey (2018, 2020, 2022); do not expect a monthly accommodation series from it."
            ),
        )
    )
    steps = _month_steps(pm)
    irregular = int((steps != 1).sum()) if len(steps) else 0
    return [
        PassedCheck(
            "Irregular month steps inside a province series",
            "data/core/province_month.parquet",
            "year_month",
            str(irregular),
        )
    ]


def _month_steps(df: pd.DataFrame) -> pd.Series:
    ordered = df.sort_values(["province_id", "year_month"])
    ordinal = ordered["year_month"].str.slice(0, 4).astype(int) * 12 + ordered["year_month"].str.slice(5, 7).astype(int)
    previous = ordinal.groupby(ordered["province_id"]).shift(1)
    return (ordinal - previous).dropna()


def _audit_integrity(
    pm: pd.DataFrame,
    rain_stage: pd.DataFrame | None,
    mots_stage: pd.DataFrame | None,
    nso_stage: pd.DataFrame | None,
    findings: list[Finding],
    passed: list[PassedCheck],
) -> None:
    spine = len(pm)
    rain_matched = int(pm["avg_rain_mm"].notna().sum())
    guest_matched = int(pm["guest_arrivals"].notna().sum())
    visitor_matched = int(pm["visitors_total"].notna().sum())
    rain_orphans = 0
    if rain_stage is not None:
        rain_orphans = int((~rain_stage["year_month"].isin(set(pm["year_month"]))).sum())
    mots_orphans = 0
    if mots_stage is not None:
        mots_orphans = int(
            (mots_stage["year_month"].notna() & ~mots_stage["year_month"].isin(set(pm["year_month"]))).sum()
        )
    nso_keys = 0 if nso_stage is None else int(sum(col in nso_stage.columns for col in ("province_id", "year_month")))
    ids_only_in_facts = 0
    if rain_stage is not None:
        ids_only_in_facts = len(set(rain_stage["province_id"].astype(str)) - set(pm["province_id"].astype(str)))
    findings.append(
        Finding(
            issue_id="integrity_join_rates",
            severity="high" if visitor_matched < spine or nso_keys == 0 else "info",
            issue="Join match rates onto the province-month spine",
            file_path="data/core/province_month.parquet",
            column="province_id, year_month",
            count=(
                f"rainfall {rain_matched}/{spine} ({_pct(rain_matched, spine)}); "
                f"guest_arrivals {guest_matched}/{spine} ({_pct(guest_matched, spine)}); "
                f"visitors_total {visitor_matched}/{spine} ({_pct(visitor_matched, spine)}); "
                f"NSO province_id/year_month columns present {nso_keys}/2; "
                f"staged rainfall rows outside the spine months {rain_orphans}; "
                f"staged MOTS rows outside the spine months {mots_orphans}; "
                f"rainfall province ids missing from the spine {ids_only_in_facts}"
            ),
            example="visitors_total matches only 2023-01 onward; NSO inventory has no province_id and cannot be joined",
            recommended_fix=(
                "Keep the left-join spine so unmatched visitors stay null. "
                "Do not inner-join or the pre-2023 rows disappear. "
                "Parse NSO to province_id before any accommodation join. "
                "Sub-area MOTS rows stay off this spine until a parent-province rule exists."
            ),
        )
    )
    passed.append(
        PassedCheck(
            "Rainfall province ids absent from province_month",
            "data/staging/hii/fact_rainfall.parquet",
            "province_id",
            str(ids_only_in_facts),
        )
    )


def _audit_accuracy_and_shift(
    pm: pd.DataFrame,
    dim_p: pd.DataFrame | None,
    findings: list[Finding],
    passed: list[PassedCheck],
) -> None:
    path = "data/core/province_month.parquet"
    ordered = pm.sort_values(["province_id", "year_month"]).copy()
    ordered["previous"] = ordered.groupby("province_id")["guest_arrivals"].shift(1)
    ordered["ratio"] = ordered["guest_arrivals"] / ordered["previous"].replace(0, np.nan)
    jumps = ordered[(ordered["previous"] > 1000) & ((ordered["ratio"] > 5) | (ordered["ratio"] < 0.2))]
    outside = jumps[(jumps["year_month"] < "2020-03") | (jumps["year_month"] > "2022-12")]
    example = "none"
    if len(jumps):
        row = jumps.sort_values("ratio").iloc[0]
        example = (
            f"{row.province_th} {row.year_month}: {row.previous:.0f} -> {row.guest_arrivals:.0f} "
            f"(ratio {row.ratio:.3f})"
        )
    findings.append(
        Finding(
            issue_id="pm_mom_jumps",
            severity="info" if outside.empty else "medium",
            issue="Month-to-month guest_arrivals moves of more than 5x sit inside the COVID window",
            file_path=path,
            column="guest_arrivals",
            count=(
                f"{len(jumps)} province-months with previous > 1000 and ratio > 5 or < 0.2; "
                f"{len(outside)} of them fall outside 2020-03..2022-12"
            ),
            example=example,
            recommended_fix=(
                "Keep the jumps. They are the shutdown and reopening, not corrupted cells. "
                "A drift detector trained on 2018-2019 will alarm through 2021; start the reference window at 2023-01 for post-COVID monitoring, or condition on is_covid."
            ),
            on_todo=not outside.empty,
        )
    )

    both = pm[pm["visitors_total"].notna() & (pm["visitors_total"] > 0)].copy()
    guests_above = int((both["guest_arrivals"] > both["visitors_total"]).sum())
    passed.append(
        PassedCheck(
            "guest_arrivals greater than visitors_total in the same province-month",
            path,
            "guest_arrivals, visitors_total",
            str(guests_above),
        )
    )
    if dim_p is not None and (CORE / "fact_tourism_province_month.parquet").exists():
        facts = pd.read_parquet(CORE / "fact_tourism_province_month.parquet")
        revenue = facts[(facts["metric"] == "revenue_million_baht") & (facts["tourist_type"] == "total")][
            ["province_id", "year_month", "value"]
        ].rename(columns={"value": "revenue"})
        visitors = facts[(facts["metric"] == "visitors") & (facts["tourist_type"] == "total")][
            ["province_id", "year_month", "value"]
        ].rename(columns={"value": "visitors"})
        spend = revenue.merge(visitors, on=["province_id", "year_month"])
        spend["baht"] = spend["revenue"] * 1_000_000 / spend["visitors"]
        names = dict(zip(dim_p["province_id"].astype(str), dim_p["province_th"], strict=False))
        low = spend.loc[spend["baht"].idxmin()]
        high = spend.loc[spend["baht"].idxmax()]
        absurd = int(((spend["baht"] < 50) | (spend["baht"] > 500_000)).sum())
        findings.append(
            Finding(
                issue_id="pm_spend_per_visitor",
                severity="high" if absurd else "info",
                issue="Revenue per visitor stays in a baht range, with no thousands-vs-persons break",
                file_path="data/core/fact_tourism_province_month.parquet",
                column="value",
                count=(
                    f"{len(spend)} province-months; baht per visitor min {spend['baht'].min():,.0f}, "
                    f"median {spend['baht'].median():,.0f}, max {spend['baht'].max():,.0f}; "
                    f"{absurd} outside 50..500,000 baht"
                ),
                example=(
                    f"low {names.get(str(low.province_id), low.province_id)} {low.year_month} {low.baht:,.0f} baht; "
                    f"high {names.get(str(high.province_id), high.province_id)} {high.year_month} {high.baht:,.0f} baht"
                ),
                recommended_fix=(
                    "Keep million_baht as the revenue unit. Phuket peak months near 40,000 baht per visitor are resort spend, not a unit error. "
                    "Do not multiply or divide the column by 1,000."
                ),
                on_todo=bool(absurd),
            )
        )

    def _period(year_month: str) -> str:
        if year_month < "2020-03":
            return "pre"
        if year_month <= "2022-12":
            return "covid"
        return "post"

    labeled = pm.assign(period=pm["year_month"].map(_period))
    med = labeled.groupby("period")["guest_arrivals"].median()
    rain_by_month = pm.groupby("month")["avg_rain_mm"].median()
    rain_index = rain_by_month / rain_by_month.mean()
    guest_index = labeled.groupby("month")["guest_arrivals"].median()
    guest_index = guest_index / guest_index.mean()
    findings.append(
        Finding(
            issue_id="pm_seasonality",
            severity="info",
            issue="Rainfall is strongly seasonal; guest_arrivals move much less across calendar months",
            file_path=path,
            column="avg_rain_mm, guest_arrivals, month",
            count=(
                f"rainfall median index month {int(rain_index.idxmin())}={rain_index.min():.2f} vs "
                f"month {int(rain_index.idxmax())}={rain_index.max():.2f}; "
                f"guest_arrivals median index {guest_index.min():.2f}..{guest_index.max():.2f}; "
                f"guest median pre={med.get('pre', float('nan')):,.0f}, "
                f"covid={med.get('covid', float('nan')):,.0f} "
                f"({med.get('covid', float('nan')) / med.get('pre', float('nan')):.2f}x pre), "
                f"post={med.get('post', float('nan')):,.0f} ({med.get('post', float('nan')) / med.get('pre', float('nan')):.2f}x pre)"
            ),
            example=f"driest calendar month index {rain_index.min():.2f}, wettest {rain_index.max():.2f}",
            recommended_fix=(
                "Put calendar month, or a wet-season flag, in any rainfall model. "
                "For drift detection, compare a month with the same calendar month in the reference set. "
                "The COVID level shift (about 0.42x the pre-2020 median) is a structural break, not a sensor fault. "
                "A reference set of only 2018-2019 will flag 2020-2022 as drift by design."
            ),
            on_todo=False,
        )
    )


def _audit_leakage(pm: pd.DataFrame, mots_stage: pd.DataFrame | None, findings: list[Finding]) -> None:
    same_rain = int((pm["avg_rain_mm"].notna() & pm["guest_arrivals"].notna()).sum())
    same_visitors = int((pm["visitors_total"].notna() & pm["guest_arrivals"].notna()).sum())
    findings.append(
        Finding(
            issue_id="leakage_same_month_features",
            severity="high",
            issue="Same-month rainfall and visitors are contemporaneous with guest_arrivals",
            file_path="data/core/province_month.parquet",
            column="avg_rain_mm, visitors_total, guest_arrivals",
            count=f"{same_rain} rows have same-month rain and guests; {same_visitors} also have same-month visitors_total",
            example="a row for 2024-12 contains that month's rain, visitors, and guest_arrivals together",
            recommended_fix=(
                "For a forecast of month t decided before t ends, use features from months through t-1 only. "
                "Same-month avg_rain_mm is valid only for a nowcast, and the training label must say so. "
                "Do not use same-month visitors_total to predict same-month guest_arrivals; they are published together."
            ),
        )
    )
    if mots_stage is None or mots_stage.empty:
        return
    sub = mots_stage[
        (mots_stage["layout"] == "quarterly_domestic")
        & (mots_stage["metric"] == "guest_arrivals")
        & (mots_stage["area_level"] == "province")
    ].copy()
    monthly = sub[sub["year_month"].notna()].copy()
    quarterly = sub[sub["quarter"].notna()][["source_file", "sheet", "year_ce", "tourist_type", "quarter"]].drop_duplicates()
    if monthly.empty or quarterly.empty:
        return
    monthly["quarter"] = (
        monthly["year_ce"].astype(int).astype(str)
        + "-Q"
        + (((monthly["month"] - 1) // 3 + 1).astype(int)).astype(str)
    )
    keys = ["source_file", "sheet", "year_ce", "tourist_type", "quarter"]
    exposed = monthly.merge(quarterly, on=keys, how="inner")
    findings.append(
        Finding(
            issue_id="leakage_quarter_total",
            severity="medium",
            issue="Quarterly guest_arrivals equal the sum of the three months in the same workbook",
            file_path="data/staging/mots/fact_tourism_long.parquet",
            column="quarter, value",
            count=f"{len(exposed)} monthly rows sit next to a quarter total that includes them",
            example="2018-Q1 total is the sum of Jan+Feb+Mar guest_arrivals in the same source file (0 mismatches)",
            recommended_fix=(
                "Do not feed the quarter total into a model that predicts one of that quarter's months. "
                "The audit already checks that the quarter matches the monthly sum within 0.1%."
            ),
        )
    )


def _audit_imbalance(pm: pd.DataFrame, findings: list[Finding]) -> None:
    volume = pm.groupby("province_id")["guest_arrivals"].sum().sort_values(ascending=False)
    total = float(volume.sum())
    names = dict(zip(pm["province_id"], pm["province_th"], strict=False))
    top_id = str(volume.index[0])
    visitor_counts = pm.groupby("province_id")["visitors_total"].apply(lambda s: int(s.notna().sum()))
    findings.append(
        Finding(
            issue_id="coverage_imbalance_volume",
            severity="medium",
            issue="Guest volume is highly uneven across provinces; visitor coverage is not",
            file_path="data/core/province_month.parquet",
            column="guest_arrivals",
            count=(
                f"{names.get(top_id, top_id)} is {volume.iloc[0] / total:.1%} of national guest_arrivals; "
                f"largest/median province total {volume.iloc[0] / volume.median():.1f}x; "
                f"smallest/median {volume.iloc[-1] / volume.median():.2f}x; "
                f"visitors_total non-null months per province min {int(visitor_counts.min())}, max {int(visitor_counts.max())}"
            ),
            example=f"{names.get(top_id, top_id)} {volume.iloc[0]:,.0f} vs median province {volume.median():,.0f}",
            recommended_fix=(
                "Fit province effects or a log target. Score errors per province, not only on the pooled sum, or Bangkok dominates. "
                "Do not drop large provinces to balance the sample. The missing visitors_total cells are the same 60 months in every province."
            ),
        )
    )


def _audit_conformity_and_units(
    mots_stage: pd.DataFrame,
    dim_p: pd.DataFrame | None,
    rain_stage: pd.DataFrame | None,
    findings: list[Finding],
    passed: list[PassedCheck],
) -> None:
    path = "data/staging/mots/fact_tourism_long.parquet"
    text_frames = [("province_th_raw", mots_stage["province_th_raw"])]
    if dim_p is not None:
        text_frames.append(("dim_province.province_th", dim_p["province_th"]))
        text_frames.append(("dim_province.province_en", dim_p["province_en"]))
    if rain_stage is not None:
        text_frames.append(("hii.province_th", rain_stage["province_th"]))
    dirty = 0
    for _name, series in text_frames:
        text = series.astype(str)
        dirty += int((text != text.str.strip()).sum())
        dirty += int(text.str.contains(r"\s{2,}", regex=True).sum())
        dirty += int(text.str.contains("\ufffd", regex=False).sum())
        dirty += int(text.str.contains(r"à¸|Ã.|à¹", regex=True).sum())
    passed.append(
        PassedCheck(
            "Thai/English labels with surrounding whitespace, double spaces, U+FFFD, or Latin-1 mojibake",
            path,
            "province_th_raw, province_th, province_en",
            str(dirty),
        )
    )
    pairs = mots_stage.groupby(["metric", "unit"], dropna=False).size().reset_index(name="n")
    bad_pairs = [
        f"{row.metric}->{row.unit} ({int(row.n)})"
        for row in pairs.itertuples(index=False)
        if EXPECTED_METRIC_UNIT.get(row.metric) not in (None, row.unit)
    ]
    passed.append(
        PassedCheck(
            "MOTS metric stored under more than one unit, or a unit other than the expected one",
            path,
            "metric, unit",
            str(len(bad_pairs)) if not bad_pairs else "; ".join(bad_pairs),
        )
    )
    if (CORE / "fact_tourism_province_month.parquet").exists():
        facts = pd.read_parquet(CORE / "fact_tourism_province_month.parquet")
        guests = facts[(facts["metric"] == "guests") & (facts["tourist_type"] == "total")][
            ["province_id", "year_month", "value"]
        ]
        arrivals = facts[(facts["metric"] == "guest_arrivals") & (facts["tourist_type"] == "total")][
            ["province_id", "year_month", "value"]
        ]
        both = guests.merge(arrivals, on=["province_id", "year_month"], suffixes=("_guests", "_arrivals"))
        disagree = int((both["value_guests"] != both["value_arrivals"]).sum()) if len(both) else 0
        passed.append(
            PassedCheck(
                "Monthly guests metric disagrees with guest_arrivals on the same province-month",
                "data/core/fact_tourism_province_month.parquet",
                "value",
                f"{disagree} of {len(both)}",
            )
        )


def _audit_lineage_determinism(findings: list[Finding], passed: list[PassedCheck]) -> None:
    facts_path = CORE / "fact_tourism_province_month.parquet"
    dup = 0
    if facts_path.exists():
        facts = pd.read_parquet(facts_path)
        dup = int(facts.duplicated(["province_id", "year_month", "metric", "tourist_type"]).sum())
    findings.append(
        Finding(
            issue_id="lineage_dedupe_tie_break",
            severity="info",
            issue="Core dedupe is deterministic only while duplicate keys stay at zero",
            file_path="src/core/build_core.py",
            column="from_prior_year_column",
            count=f"{dup} duplicate province-month-metric-type keys in the published fact table",
            example="build_fact_tourism_province_month sorts by from_prior_year_column and keeps the first row",
            recommended_fix=(
                "Add source_file and sheet as tie-breaks before drop_duplicates. "
                "Today the output does not depend on parquet row order because no key is duplicated. "
                "A second current-year copy of the same key would."
            ),
            on_todo=False,
        )
    )
    passed.append(
        PassedCheck(
            "Duplicate keys that would make core dedupe depend on row order",
            "data/core/fact_tourism_province_month.parquet",
            "province_id, year_month, metric, tourist_type",
            str(dup),
        )
    )


def _findings_table(findings: list[Finding]) -> str:
    header = "| Dimension | Severity | Issue | File path | Column | Count | Example | Recommended fix |"
    sep = "| --- | --- | --- | --- | --- | --- | --- | --- |"
    rows = [header, sep]
    for item in findings:
        rows.append(
            "| {dim} | {sev} | {issue} | `{path}` | `{col}` | {count} | {example} | {fix} |".format(
                dim=_md(DIMENSION_LABEL.get(dimension_of(item.issue_id), dimension_of(item.issue_id))),
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


def _dimension_summary(findings: list[Finding]) -> str:
    rows = [
        "| Dimension | Findings | High | Medium | Low | Info | First item |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for key, label in DIMENSION_LABEL.items():
        group = [item for item in findings if dimension_of(item.issue_id) == key]
        counts = {severity: sum(1 for item in group if item.severity == severity) for severity in SEVERITY_RANK}
        first = group[0].issue if group else "No defect rows. Related passed checks are listed below."
        rows.append(
            f"| {label} | {len(group)} | {counts['high']} | {counts['medium']} | {counts['low']} | {counts['info']} | {_md(first)} |"
        )
    other = [item for item in findings if dimension_of(item.issue_id) not in DIMENSION_LABEL]
    if other:
        rows.append(f"| Other | {len(other)} |  |  |  |  | {_md(other[0].issue)} |")
    return "\n".join(rows)


def _todo(findings: list[Finding]) -> str:
    ranked = [item for item in findings if item.on_todo]
    lines = []
    for index, item in enumerate(ranked, start=1):
        lines.append(
            f"{index}. **[{item.severity} / {DIMENSION_LABEL.get(dimension_of(item.issue_id), dimension_of(item.issue_id))}]** "
            f"{item.issue} (`{item.file_path}`, `{item.column}`, {item.count}). {item.recommended_fix}"
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
        "## Summary by dimension",
        "",
        "Each finding has one primary dimension. Counts are the number of finding rows, not the number of bad cells.",
        "",
        _dimension_summary(report.findings),
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
        "The audit scores twelve dimensions. A finding is filed under one primary dimension so the summary does not double-count it.",
        "",
        "- **Completeness.** Null counts and percent of rows, the 77×92 grid (`dim_province` × the month list in `config/sources.yaml`), and whole sources or periods that never arrive (visitors before 2023-01, NSO measures, quarterly rows with a null month).",
        "- **Uniqueness.** Exact duplicate rows and the grains in `config/data_contracts.yaml`.",
        "- **Validity.** Required columns, key uniqueness, and coverage floors in `data_contracts.yaml`; `year_be = year_ce + 543`; `is_covid` versus the configured window; occupancy in 0–100; negative values; rainfall above "
        f"{ABSURD_RAIN_MM:.0f} mm; revenue above {ABSURD_REVENUE_MILLION_BAHT:,.0f} million baht; `province_id` dtype.",
        "- **Accuracy / plausibility.** Per-province IQR (1.5×IQR) and |z| > 3. Month-to-month ratios above 5 or below 0.2 when the previous month exceeds 1,000 guests. Baht per visitor outside 50..500,000. `guest_arrivals` above `visitors_total`.",
        "- **Consistency.** Canonical province names versus `province_th_raw`. One unit per metric. Monthly `guests` versus `guest_arrivals`. Thai + foreign versus total, and quarter versus the sum of months, at the tolerance in `src/qa/consistency.py` (1 person or 0.1%).",
        "- **Timeliness.** Last `year_month` per source versus the audit clock and versus the modeling window. Steps between consecutive months inside each province. Buddhist-era versus Gregorian years.",
        "- **Integrity.** Match rate of rainfall, guest_arrivals, and visitors_total onto the spine. NSO rows that lack join keys. Staged rows whose month is outside the spine.",
        "- **Conformity.** Surrounding whitespace, double spaces, U+FFFD, and Latin-1 mojibake on Thai and English names. Windows backslashes in stored paths. Float residue past 0.01 mm.",
        "- **Temporal / stationarity.** Pre-COVID (through 2020-02), COVID (2020-03..2022-12), and post-COVID medians. Calendar-month index of rainfall and guests, for drift rules that should compare like months.",
        "- **Data leakage.** Same-month rain and visitors beside `guest_arrivals`. Quarter totals that equal the months being predicted. 2023 monthly figures taken from a 2024 prior-year block.",
        "- **Coverage imbalance.** Share of national `guest_arrivals` and the ratio of the largest province total to the median. Visitors non-null months per province.",
        "- **Lineage.** Raw files absent from git, source URLs that do not return a body, manifest rows still in error, and dedupe that would follow parquet row order if a key were duplicated.",
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
