"""Champion policy without MOTS or HII files."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.stream.champion_policy import CANDIDATES, ChampionPolicy, PromotionRule
from src.stream.drift import PageHinkley
from src.stream.raw_2025 import _collect_sources
from src.stream.studio import note_deployment
from src.stream.studio_models import FEATURES, PH_DELTA, PH_THRESHOLD, _freeze_baseline

FIXTURE = Path(__file__).parent / "fixtures" / "champion_month.csv"
N = 77


class Const:
    def __init__(self, value):
        self.value = np.asarray(value, dtype=float)

    def predict(self, x):
        if self.value.ndim == 0:
            return np.full(len(x), float(self.value))
        return self.value


class Identity:
    def transform(self, frame):
        return frame[FEATURES].to_numpy(dtype=float)


class Base:
    def __init__(self, months=12):
        self.x_history = np.zeros((months * N, len(FEATURES)))
        self.y_history = np.full(months * N, 100.0)
        cal = (0.2, 1.0)
        self.calibration = {"rf_fixed": cal, "linear": cal, "rf_monthly": cal, "rf_12m": cal}
        self.detectors = {
            key: PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=12)
            for key in ("rf_fixed", "linear", "rf_monthly")
        }
        self.baseline = {"rf_fixed": {"overall": {"wape": 0.09}}}
        self.scaler = Identity()
        self.alarms = {name: False for name in ("guest_lag_1", "guest_lag_12", "rain_lag_1", "rain_roll3")}

    def psi(self, features, month):
        return {name: {"value": float(self.alarms[name]), "alarm": self.alarms[name]} for name in self.alarms}

    def ph_single(self, name, current):
        detector = self.detectors[name]
        mean, std = self.calibration[name]
        z = float((current - mean) / std)
        alarm = detector.update(z)
        return {"value": detector.statistic, "alarm": alarm, "z": z}


def frame(y=100.0):
    raw = pd.read_csv(FIXTURE, dtype={"province_id": str})
    assert len(raw) == N
    raw["guest_arrivals_total"] = y
    return raw


def run(policy, month, y=100.0):
    table = frame(y)
    labels = table[["province_id", "province_th", "guest_arrivals_total"]]
    return policy.run(table, labels, month, lambda *args: None)


def policy(rule, champion, candidates, months=12, monkeypatch=None):
    base = Base(months)
    model = ChampionPolicy(base, Const(champion), rule=rule)
    mapping = {name: Const(value) for name, value in candidates.items()}

    def fit(name, train_x, train_y):
        return mapping[name]

    monkeypatch.setattr("src.stream.champion_policy._fit_candidate", fit)
    return model


def test_no_drift_keeps_the_frozen_champion(monkeypatch):
    model = policy(PromotionRule(min_wape_improvement=0.005, confirm_months=1), 80, {}, monkeypatch=monkeypatch)
    result = run(model, "2025-01")
    assert result["competition"]["decision"] == "KEEP"
    assert result["competition"]["scores"] == {}
    assert result["competition"]["trigger"] == []
    assert model.version == "v001-rf-fit-2024-12"
    assert {row["version"] for row in result["rows"]} == {"v001-rf-fit-2024-12"}


def test_drift_and_a_losing_challenger_keeps(monkeypatch):
    model = policy(PromotionRule(min_wape_improvement=0.005, confirm_months=1, seed_detector=True),
                   80, {name: 50 for name in CANDIDATES}, monkeypatch=monkeypatch)
    model.base.alarms["guest_lag_1"] = True
    result = run(model, "2025-01")
    assert result["competition"]["trigger"] == ["guest_lag_1"]
    assert result["competition"]["decision"] == "KEEP"
    assert model.strategy == "rf_expanding"
    assert all(result["competition"]["scores"][name]["wape"] > result["metrics"]["champion"]["wape"] for name in CANDIDATES)


def test_drift_below_the_margin_keeps(monkeypatch):
    # Champion predicts 80 (WAPE 0.20). Challenger predicts 81 (WAPE 0.19): better, short of 0.05.
    model = policy(PromotionRule(min_wape_improvement=0.05, confirm_months=1),
                   80, {name: 81 for name in CANDIDATES}, monkeypatch=monkeypatch)
    model.base.alarms["guest_lag_12"] = True
    result = run(model, "2025-01")
    assert result["metrics"]["champion"]["wape"] == pytest.approx(0.2)
    assert result["competition"]["scores"]["rf_expanding"]["wape"] == pytest.approx(0.19)
    assert result["competition"]["decision"] == "KEEP"
    assert model.version.startswith("v001")


def test_margin_and_confirmation_promote_from_the_next_month(monkeypatch):
    rule = PromotionRule(min_wape_improvement=0.05, confirm_months=2, name="confirm-2")
    model = policy(rule, 50, {name: 100 for name in CANDIDATES}, monkeypatch=monkeypatch)
    model.base.alarms["guest_lag_1"] = True
    first = run(model, "2025-03")
    assert first["competition"]["decision"] == "KEEP"
    assert model.streak["rf_expanding"] == 1
    assert model.version.startswith("v001")
    second = run(model, "2025-04")
    assert second["competition"]["decision"] == "PROMOTE"
    assert second["competition"]["strategy"] == "rf_expanding"
    assert {row["version"] for row in second["rows"]} == {"v001-rf-fit-2024-12"}
    assert model.version == "v002-rf_expanding-fit-2025-03"
    assert second["versions"]["champion"] == "v001-rf-fit-2024-12"


def test_rain_lag_needs_two_consecutive_psi_alarms(monkeypatch):
    model = policy(PromotionRule(), 80, {name: 100 for name in CANDIDATES}, monkeypatch=monkeypatch)
    model.base.alarms["rain_lag_1"] = True
    first = run(model, "2025-02")
    assert first["psi"]["rain_lag_1"]["pending_confirmation"]
    assert not first["psi"]["rain_lag_1"]["alarm"]
    assert first["competition"]["trigger"] == []
    second = run(model, "2025-03")
    assert second["psi"]["rain_lag_1"]["alarm"]
    assert second["competition"]["trigger"] == ["rain_lag_1"]


def test_seeded_detector_does_not_alarm_immediately_after_promotion(monkeypatch):
    rule = PromotionRule(min_wape_improvement=0.05, confirm_months=1, seed_detector=True, name="seed")
    model = policy(rule, 50, {name: 80 for name in CANDIDATES}, months=12, monkeypatch=monkeypatch)
    before = {name: model.base.detectors[name] for name in ("rf_expanding", "rf_12m", "linear_expanding")}
    assert len({id(item) for item in before.values()}) == 3
    assert all(before[name] is not model.base.detectors["rf_fixed"] for name in before)
    model.base.alarms["guest_lag_1"] = True
    result = run(model, "2025-06")
    assert result["competition"]["decision"] == "PROMOTE"
    seeded = model.base.detectors["rf_expanding"]
    assert seeded is not before["rf_expanding"]
    assert seeded._n == 12
    assert seeded._cumsum != 0
    follow = model.base.ph_single("rf_expanding", 0.2)
    assert not follow["alarm"]


def test_legacy_preset_matches_the_one_month_wape_rule(monkeypatch):
    model = policy(PromotionRule(), 50, {name: 100 for name in CANDIDATES}, monkeypatch=monkeypatch)
    shared = model.base.detectors["rf_fixed"]
    model.base.alarms["guest_lag_1"] = True
    result = run(model, "2025-01")
    assert result["competition"]["preset"] == "legacy"
    assert result["competition"]["decision"] == "PROMOTE"
    assert model.base.detectors["rf_fixed"]._n == 12
    assert model.base.detectors["rf_fixed"]._cumsum == 0
    assert model.base.detectors["rf_fixed"] is not shared


def test_rmse_guardrail_blocks_a_wape_win(monkeypatch):
    wild = np.full(N, 100.0)
    wild[0] = 1100.0
    rule = PromotionRule(min_wape_improvement=0.05, max_mae_regression=0.0, max_rmse_regression=0.0,
                         confirm_months=1, name="guarded")
    model = policy(rule, 80, {name: wild for name in CANDIDATES}, monkeypatch=monkeypatch)
    model.base.alarms["guest_lag_1"] = True
    result = run(model, "2025-01")
    score = result["competition"]["scores"]["rf_expanding"]
    assert score["wape"] < result["metrics"]["champion"]["wape"] - 0.05
    assert score["rmse"] > result["metrics"]["champion"]["rmse"]
    assert result["competition"]["decision"] == "KEEP"


def test_effective_from_advances_only_on_promotion():
    state = {"champion": "champion", "registry": [],
             "deployment": {"model": "champion", "version": "v001", "effective_from": "2025-01"}}
    note_deployment(state, {"decision": "KEEP"}, "v002", "2025-01")
    assert state["deployment"]["effective_from"] == "2025-01"
    assert state["registry"] == []
    note_deployment(state, {"decision": "PROMOTE"}, "v002", "2025-01")
    assert state["deployment"]["effective_from"] == "2025-02"
    assert state["deployment"]["version"] == "v002"
    assert state["registry"][0]["action"] == "PROMOTE"


def test_rain_collection_is_independent_of_manifest_order():
    guests = [{"province_id": "10", "guest_arrivals_total": 5}]
    rain = pd.DataFrame({"province_id": ["10"], "rain_min_mm": [1.0], "rain_avg_mm": [2.0], "rain_max_mm": [3.0]})
    forward = _collect_sources([("xlsx", guests), ("rain", rain)])
    reverse = _collect_sources([("rain", rain), ("xlsx", guests)])
    assert forward[0] == reverse[0]
    pd.testing.assert_frame_equal(forward[1].reset_index(drop=True), reverse[1].reset_index(drop=True))
    with pytest.raises(ValueError, match="rainfall"):
        _collect_sources([("xlsx", guests)])


def test_baseline_check_tolerates_numeric_noise_and_warns_on_runtime(tmp_path):
    metrics = {"overall": {"wape": 0.1, "mae": 2.0, "rmse": 3.0, "mase": 0.5, "n": 4}, "per_province": []}
    path = tmp_path / "baseline_test_2024.json"
    _freeze_baseline(path, metrics)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["runtime"]["sklearn"]
    saved["metrics"]["overall"]["wape"] *= 1 + 1e-7
    saved["runtime"] = {"python": "0", "sklearn": "0", "numpy": "0"}
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.warns(UserWarning, match="runtime"):
        _freeze_baseline(path, metrics)
    saved["metrics"]["overall"]["wape"] = 0.2
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        _freeze_baseline(path, metrics)
