"""The data-quality audit is read-only and reports the known structural gaps."""

from pathlib import Path

import pytest

from src.common.paths import CORE
from src.qa.data_quality_audit import build_audit, render_report


pytestmark = pytest.mark.integration


def _finding(report, issue_id: str):
    matches = [item for item in report.findings if item.issue_id == issue_id]
    assert matches, issue_id
    return matches[0]


def test_audit_flags_structural_gaps_without_rewriting_data(tmp_path: Path):
    province_month = CORE / "province_month.parquet"
    if not province_month.exists():
        pytest.skip("core tables not built")
    before = province_month.read_bytes()

    report = build_audit(probe=False)
    text = render_report(report)
    out = tmp_path / "data_quality_audit.md"
    out.write_text(text, encoding="utf-8")

    assert province_month.read_bytes() == before
    assert "| Dimension | Severity | Issue | File path | Column | Count | Example | Recommended fix |" in text
    assert "## Summary by dimension" in text
    assert "## Prioritized cleaning to-do" in text
    for label in (
        "Completeness",
        "Uniqueness",
        "Validity",
        "Accuracy / plausibility",
        "Consistency",
        "Timeliness / freshness",
        "Integrity",
        "Conformity / format",
        "Temporal / stationarity",
        "Data leakage",
        "Coverage imbalance",
        "Lineage / reproducibility",
    ):
        assert f"| {label} |" in text

    visitors = _finding(report, "pm_visitors_structural_null")
    assert visitors.severity == "high"
    assert visitors.file_path == "data/core/province_month.parquet"
    assert visitors.count.startswith("4620 nulls")
    assert "data/core/province_month.parquet" in text

    prior = _finding(report, "mots_2023_monthly_from_prior_column")
    assert prior.severity == "medium"
    assert prior.count.startswith("7392 rows")

    raw = _finding(report, "raw_sources_absent")
    assert raw.file_path == "data/raw/"

    grid = [item for item in report.passed if "Full" in item.check and "province_month" in item.file_path]
    assert grid and "gaps=0" in grid[0].count
    assert not any(item.issue_id == "pm_grid" for item in report.findings)

    leakage = _finding(report, "leakage_same_month_features")
    assert leakage.severity == "high"
    assert "7084 rows" in leakage.count
    imbalance = _finding(report, "coverage_imbalance_volume")
    assert imbalance.file_path == "data/core/province_month.parquet"
    assert "กรุงเทพมหานคร" in imbalance.count or "19." in imbalance.count
