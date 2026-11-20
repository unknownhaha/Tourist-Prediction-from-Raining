import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.model.leakage import build_forecast_features
from src.stream.drift import PageHinkley
from src.stream.studio_models import prepare, psi_alarm


def test_threshold_boundaries_and_historical_page_hinkley_seed():
    assert psi_alarm(0.15)
    assert not psi_alarm(np.nextafter(0.15, 0))
    detector = PageHinkley(delta=0.5, threshold=3.0, initial_count=12)
    assert not detector.update(2.0)
    assert detector.statistic == pytest.approx(2 - 2 / 13 - 0.5)
    assert detector.update(3.0)


def test_frozen_holdout_then_expanding_monthly_refit(tmp_path):
    path = Path(__file__).resolve().parents[1] / "data/model/province_month_unified.parquet"
    panel = pd.read_parquet(path)
    history = panel.loc[panel.year_month < "2025-01"]
    baseline_file = tmp_path / "baseline_test_2024.json"
    models = prepare(history, baseline_file)
    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    assert baseline["train"]["end"] == "2022-12"
    assert baseline["metrics"]["overall"]["wape"] == pytest.approx(0.0920671209917)
    assert len(baseline["metrics"]["per_province"]) == 77
    assert len(models.x_history) == 72 * 77
    jan = build_forecast_features(panel, "2025-01").sort_values("province_id")
    original = models.predict(jan)
    np.testing.assert_array_equal(original["rf_fixed"], original["rf_monthly"])
    changed = panel.copy()
    changed.loc[changed.year_month == "2025-01", "guest_arrivals_total"] *= 3
    jan_changed = build_forecast_features(changed, "2025-01").sort_values("province_id")
    pd.testing.assert_frame_equal(jan, jan_changed)
    assert models.psi(jan, "2025-01") == models.psi(jan_changed, "2025-01")
    for name, prediction in models.predict(jan_changed).items():
        np.testing.assert_array_equal(prediction, original[name])
    monthly = panel.loc[panel.year_month == "2025-01"].sort_values("province_id")
    models.learn(jan, monthly.guest_arrivals_total, "2025-01")
    assert len(models.x_history) == 73 * 77
    feb = build_forecast_features(panel, "2025-02")
    forecasts = models.predict(feb)
    assert not np.array_equal(forecasts["rf_fixed"], forecasts["rf_monthly"])
    assert models.versions["rf_fixed"].endswith("2024-12")
    assert models.versions["rf_monthly"].endswith("2025-01")
    with pytest.raises(ValueError, match="2025-02"):
        models.learn(jan, monthly.guest_arrivals_total, "2025-01")
