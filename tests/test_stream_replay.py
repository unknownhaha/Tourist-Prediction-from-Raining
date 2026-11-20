"""Validation, cleaning, data quality, leakage, and the 2025 replay."""

from __future__ import annotations

import pandas as pd
import pytest

from src.common.calendar import month_range
from src.common.paths import DATA
from src.model.leakage import assert_no_future_leakage, forecast_source_months
from src.stream.clean import clean_panel
from src.stream.model import OnlineLinear, SeasonalNaive
from src.stream.quality import assess_month
from src.stream.replay import replay
from src.stream.validate import StreamHalt, validate_month

N = 77


def _panel(months: list[str], *, guest=None, rain_avg=2.0, thai=40.0, foreign=60.0, province_id=None) -> pd.DataFrame:
    rows = []
    for year_month in months:
        for index in range(1, N + 1):
            pid = f"{index:02d}" if province_id is None else province_id(index)
            value = float(100 + index) if guest is None else float(guest(index, year_month))
            rows.append(
                {
                    "province_id": pid,
                    "year_month": year_month,
                    "guest_arrivals_total": value,
                    "guest_arrivals_thai": None if year_month.startswith("2018") else thai,
                    "guest_arrivals_foreign": None if year_month.startswith("2018") else foreign,
                    "rain_avg_mm": rain_avg,
                    "rain_min_mm": 1.0,
                    "rain_max_mm": 3.0,
                    "is_covid": year_month >= "2020-03" and year_month <= "2022-12",
                }
            )
    return pd.DataFrame(rows)


def test_validate_rejects_negative_target_and_bad_rain_order():
    month = _panel(["2025-01"])
    month.loc[0, "guest_arrivals_total"] = -1
    with pytest.raises(StreamHalt, match="guest_arrivals_total_non_negative"):
        validate_month(month, "2025-01", stage="re-validate")
    month = _panel(["2025-01"])
    month.loc[0, "rain_min_mm"] = 5
    with pytest.raises(StreamHalt, match="rain_order"):
        validate_month(month, "2025-01", stage="re-validate")


def test_validate_rejects_covid_flag_that_disagrees_with_calendar():
    month = _panel(["2025-01"])
    month["is_covid"] = True
    with pytest.raises(StreamHalt, match="is_covid_calendar"):
        validate_month(month, "2025-01", stage="validate")


def test_validate_rejects_a_short_month():
    month = _panel(["2025-01"]).iloc[:-1]
    with pytest.raises(StreamHalt, match="row_count"):
        validate_month(month, "2025-01", stage="validate")


def test_clean_pads_province_id_without_changing_the_target():
    frame = _panel(["2025-01"])
    frame["province_id"] = [index for index in range(1, N + 1)]
    original = frame["guest_arrivals_total"].astype(float).tolist()
    cleaned, actions = clean_panel(frame)
    assert cleaned["province_id"].tolist() == [f"{index:02d}" for index in range(1, N + 1)]
    assert cleaned["guest_arrivals_total"].astype(float).tolist() == original
    assert any(item["action"] == "pad_province_id" for item in actions)


def test_clean_copies_rain_only_from_the_same_staging_month():
    frame = _panel(["2025-01", "2025-02"])
    frame.loc[frame["year_month"] == "2025-02", ["rain_avg_mm", "rain_min_mm", "rain_max_mm"]] = None
    staging = _panel(["2025-02"])
    staging["rain_avg_mm"] = 9.0
    staging["rain_min_mm"] = 8.0
    staging["rain_max_mm"] = 10.0
    cleaned, actions = clean_panel(frame, staging)
    february = cleaned.loc[cleaned["year_month"] == "2025-02", "rain_avg_mm"]
    january = cleaned.loc[cleaned["year_month"] == "2025-01", "rain_avg_mm"]
    assert (february == 9.0).all()
    assert (january == 2.0).all()
    assert any(item["action"] == "rain_from_staging_exact_month" for item in actions)

    frame = _panel(["2025-02"])
    frame[["rain_avg_mm", "rain_min_mm", "rain_max_mm"]] = None
    staging = _panel(["2025-01"])
    cleaned, actions = clean_panel(frame, staging)
    assert cleaned["rain_avg_mm"].isna().all()
    assert not any(item["action"] == "rain_from_staging_exact_month" for item in actions)


def test_quality_keeps_zero_rows_and_flags_them():
    history = _panel(month_range("2024-01", "2024-12"), guest=lambda index, month: 500.0)
    month = _panel(["2025-01"], guest=lambda index, month: 0.0 if index == 1 else 500.0)
    report, flags = assess_month(month, history, "2025-01")
    assert report["hard_fail"] is False
    assert (month["guest_arrivals_total"] == 0).sum() == 1
    assert "zero_arrival_kept" in set(flags["check"])
    assert set(flags["severity"]) <= {"warn"}


def test_forecast_sources_for_a_stream_month_are_before_that_month():
    sources = forecast_source_months("2025-03")
    assert_no_future_leakage(sources.values(), "2025-03")
    assert "2025-03" not in sources.values()
    assert all(month < "2025-03" for month in sources.values())


def test_replay_is_deterministic_and_covers_twelve_by_seventy_seven(tmp_path):
    panel = _panel(month_range("2022-01", "2025-12"), guest=lambda index, month: 200.0 + index)
    first = tmp_path / "a"
    second = tmp_path / "b"
    metrics_a = replay(output_dir=first, panel=panel, seed=2025)
    metrics_b = replay(output_dir=second, panel=panel, seed=2025)
    left = pd.read_parquet(first / "predictions.parquet")
    right = pd.read_parquet(second / "predictions.parquet")
    pd.testing.assert_frame_equal(left, right)
    assert len(left) == 12 * 77
    assert left["year_month"].nunique() == 12
    assert left.groupby("year_month")["province_id"].nunique().eq(77).all()
    assert metrics_a["overall"]["wape"] == metrics_b["overall"]["wape"]
    text_a = (first / "lineage" / "2025-06.json").read_text(encoding="utf-8")
    text_b = (second / "lineage" / "2025-06.json").read_text(encoding="utf-8")
    assert text_a == text_b
    assert "logged_at" not in text_a


def test_replay_halts_when_a_stream_month_is_missing(tmp_path):
    panel = _panel(month_range("2022-01", "2025-12"))
    panel = panel.loc[panel["year_month"] != "2025-06"]
    with pytest.raises(StreamHalt, match="2025-06"):
        replay(output_dir=tmp_path / "out", panel=panel)


def test_online_linear_learn_changes_the_next_prediction():
    features = pd.DataFrame(
        {
            "province_id": ["10"],
            "guest_lag_1": [10.0],
            "guest_lag_12": [10.0],
            "rain_lag_1": [1.0],
            "rain_roll3": [1.0],
            "is_covid": [False],
            "is_covid_lag12": [False],
        }
    )
    model = OnlineLinear(seed=2025, step=0.01)
    before = float(model.predict(features).iloc[0])
    model.learn(features, pd.Series([50.0]))
    after = float(model.predict(features).iloc[0])
    assert before == 10.0
    assert after != before


def test_end_to_end_2025_seasonal_naive_on_the_unified_table(tmp_path):
    snapshot = DATA / "model" / "province_month_unified.parquet"
    metrics = replay(
        output_dir=tmp_path / "stream",
        snapshot_path=snapshot,
        staging_rain=None,
        model=SeasonalNaive(),
        seed=2025,
    )
    log = pd.read_parquet(tmp_path / "stream" / "predictions.parquet")
    assert len(log) == 12 * 77
    assert set(log["year_month"]) == set(month_range("2025-01", "2025-12"))
    reference = pd.read_parquet(tmp_path / "stream" / "reference_2023_2024.parquet")
    assert len(reference) == 77 * 24
    assert reference["year_month"].min() == "2023-01"
    assert reference["year_month"].max() == "2024-12"
    assert not ((reference["year_month"] >= "2020-03") & (reference["year_month"] <= "2022-12")).any()
    unified = pd.read_parquet(snapshot)
    unified["province_id"] = unified["province_id"].astype(str)
    january = log.loc[log["year_month"] == "2025-01"].merge(
        unified.loc[unified["year_month"] == "2024-01", ["province_id", "guest_arrivals_total"]],
        on="province_id",
    )
    assert (january["y_pred"] == january["guest_arrivals_total"]).all()
    assert metrics["overall"]["mase"] == pytest.approx(1.0)
    assert metrics["n_predictions"] == 924
    assert metrics["overall"]["wape"] > 0
