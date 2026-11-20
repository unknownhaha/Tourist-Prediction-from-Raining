"""One serving model and a three-way, drift-triggered competition.

The serving Champion stays frozen in months with no drift trigger. It changes
only when a trigger fires and a Challenger clears the configured promotion
rule. ``legacy`` reproduces the previous one-month WAPE comparison.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from src.stream.drift import PageHinkley
from src.stream.metrics import _block, wape
from src.stream.studio_models import FEATURES, PH_DELTA, PH_THRESHOLD, _fit_forest, _predict
from src.stream.validate import N_PROVINCES

CANDIDATES = ("rf_expanding", "rf_12m", "linear_expanding")
# 2019-01 is history row 0. 2023-01 is the first validation month.
_VALIDATION_START = 48


@dataclass(frozen=True)
class PromotionRule:
    """Configurable promotion bar. ``None`` guardrails are off.

    ``min_wape_improvement`` is an absolute WAPE gap (fraction, not percent).
    ``max_mae_regression`` and ``max_rmse_regression`` are relative allowances:
    a challenger may be at most that fraction worse than the champion.
    ``confirm_months`` is how many consecutive triggered months the same
    challenger must clear the bar before it becomes the serving model.
    """

    min_wape_improvement: float = 1e-12
    max_mae_regression: float | None = None
    max_rmse_regression: float | None = None
    confirm_months: int = 1
    seed_detector: bool = False
    name: str = "legacy"

    def beats(self, challenger, champion):
        if not challenger["wape"] < champion["wape"] - self.min_wape_improvement:
            return False
        if self.max_mae_regression is not None and challenger["mae"] > champion["mae"] * (1.0 + self.max_mae_regression):
            return False
        if self.max_rmse_regression is not None and challenger["rmse"] > champion["rmse"] * (1.0 + self.max_rmse_regression):
            return False
        return True


def promotion_rule(preset="legacy", **overrides):
    """Named candidates. ``legacy`` is the previous behavior, not a team pick."""
    presets = {
        "legacy": dict(min_wape_improvement=1e-12, max_mae_regression=None, max_rmse_regression=None,
                       confirm_months=1, seed_detector=False),
        "margin_0005_confirm_1": dict(min_wape_improvement=0.005, max_mae_regression=0.0, max_rmse_regression=0.0,
                                      confirm_months=1, seed_detector=True),
        "margin_0005_confirm_2": dict(min_wape_improvement=0.005, max_mae_regression=0.0, max_rmse_regression=0.0,
                                      confirm_months=2, seed_detector=True),
        "margin_001_confirm_2": dict(min_wape_improvement=0.01, max_mae_regression=0.0, max_rmse_regression=0.0,
                                     confirm_months=2, seed_detector=True),
    }
    if preset not in presets:
        raise KeyError(f"Unknown promotion preset: {preset}")
    values = dict(presets[preset])
    values.update(overrides)
    return PromotionRule(name=preset, **values)


def _fit_candidate(name, train_x, train_y):
    if name == "rf_12m":
        train_x, train_y = train_x[-12 * N_PROVINCES:], train_y[-12 * N_PROVINCES:]
    if name == "linear_expanding":
        return LinearRegression().fit(train_x, train_y)
    return _fit_forest(train_x, train_y)


@dataclass
class ChampionPolicy:
    base: object
    active: object
    strategy: str = "rf_expanding"
    version: str = "v001-rf-fit-2024-12"
    revision: int = 1
    last_rain_lag_exceedance: str | None = None
    rule: PromotionRule = field(default_factory=lambda: promotion_rule("legacy"))
    streak: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.streak:
            self.streak = {name: 0 for name in CANDIDATES}
        if not self.rule.seed_detector:
            return
        self._ensure_family("rf_expanding", self.base.calibration["rf_fixed"])
        self._ensure_family("linear_expanding", self.base.calibration["linear"])
        if "rf_12m" not in self.base.calibration:
            self.base.calibration["rf_12m"] = self._window_calibration("rf_12m")
        self._ensure_family("rf_12m", self.base.calibration["rf_12m"])

    @property
    def versions(self):
        return {"champion": self.version}

    @property
    def baseline(self):
        return {"champion": self.base.baseline["rf_fixed"]}

    def _family_key(self, strategy):
        if self.rule.seed_detector:
            return strategy
        return "linear" if strategy == "linear_expanding" else "rf_fixed"

    def _ensure_family(self, name, calibration):
        mean, std = calibration
        self.base.calibration[name] = (float(mean), float(std))
        if name not in self.base.detectors or self.base.detectors[name] is self.base.detectors.get("rf_fixed"):
            self.base.detectors[name] = PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=12)

    def _window_calibration(self, strategy):
        """Monthly WAPE on 2023, each month fit only on rows strictly before it."""
        n = N_PROVINCES
        total = len(self.base.y_history) // n
        if total < _VALIDATION_START + 12:
            raise ValueError(f"Need 2019–2023 history to calibrate {strategy}")
        scores = []
        for index in range(_VALIDATION_START, _VALIDATION_START + 12):
            model = _fit_candidate(strategy, self.base.x_history[: index * n], self.base.y_history[: index * n])
            pred = _predict(model, self.base.x_history[index * n:(index + 1) * n])
            scores.append(wape(self.base.y_history[index * n:(index + 1) * n], pred))
        std = float(np.std(scores, ddof=1))
        if not np.isfinite(std) or std == 0:
            raise ValueError(f"No usable historical WAPE variation for {strategy}")
        return float(np.mean(scores)), std

    def _trailing_wapes(self, strategy, current_wape):
        """Up to 12 revealed months. Past months are refit without that month."""
        n = N_PROVINCES
        hist = len(self.base.y_history) // n
        start = max(0, hist - 11)
        scores = []
        for index in range(start, hist):
            train_end = index * n
            if train_end == 0:
                continue
            model = _fit_candidate(strategy, self.base.x_history[:train_end], self.base.y_history[:train_end])
            pred = _predict(model, self.base.x_history[train_end:train_end + n])
            scores.append(wape(self.base.y_history[train_end:train_end + n], pred))
        scores.append(float(current_wape))
        return scores[-12:]

    def _install_detector(self, strategy, current_wape):
        if not self.rule.seed_detector:
            family = self._family_key(strategy)
            self.base.detectors[family] = PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=12)
            return
        mean, std = self.base.calibration[strategy]
        detector = PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=0)
        for value in self._trailing_wapes(strategy, current_wape):
            detector.update(float((value - mean) / std))
        self.base.detectors[strategy] = detector

    def run(self, features, labels, month, progress):
        cutoff = str(pd.Period(month, freq="M") - 1)
        x = self.base.scaler.transform(features[FEATURES])
        psi = self.base.psi(features, month)
        rain = psi["rain_lag_1"]
        exceeded = rain["alarm"]
        rain["alarm"] = exceeded and self.last_rain_lag_exceedance == cutoff
        rain["pending_confirmation"] = exceeded and not rain["alarm"]
        self.last_rain_lag_exceedance = month if exceeded else None
        progress("PREDICT")
        served_version = self.version
        pred = _predict(self.active, x)
        progress("MONITOR")
        y = labels.guest_arrivals_total.to_numpy(dtype=float)
        lag = features.guest_lag_12.to_numpy(dtype=float)
        score = _block(y, pred, lag)
        family = self._family_key(self.strategy)
        ph = self.base.ph_single(family, score["wape"])
        reasons = [name for name, event in psi.items() if event["alarm"]]
        if ph["alarm"]:
            reasons.append("Page-Hinkley")
        comparisons = {}
        decision = "KEEP"
        if not reasons:
            self.streak = {name: 0 for name in CANDIDATES}
        else:
            candidates = {}
            for name in CANDIDATES:
                progress("RETRAIN", name)
                candidates[name] = _fit_candidate(name, self.base.x_history, self.base.y_history)
            progress("COMPARE")
            for name, candidate in candidates.items():
                comparisons[name] = _block(y, _predict(candidate, x), lag)
            for name in CANDIDATES:
                if self.rule.beats(comparisons[name], score):
                    self.streak[name] = self.streak.get(name, 0) + 1
                else:
                    self.streak[name] = 0
            winners = [name for name in CANDIDATES if self.streak[name] > 0]
            if winners:
                best = min(winners, key=lambda name: comparisons[name]["wape"])
                if self.streak[best] >= self.rule.confirm_months:
                    self.active, self.strategy = candidates[best], best
                    self.revision += 1
                    self.version = f"v{self.revision:03d}-{best}-fit-{cutoff}"
                    self._install_detector(best, comparisons[best]["wape"])
                    decision = "PROMOTE"
        self.base.x_history = np.concatenate([self.base.x_history, x])
        self.base.y_history = np.concatenate([self.base.y_history, y])
        progress("DEPLOY", decision)
        rows = pd.DataFrame(dict(year_month=month, province_id=features.province_id,
                                 province_th=labels.province_th, model="champion", version=served_version,
                                 y_true=y, y_pred=pred, y_naive=lag)).to_dict("records")
        return dict(rows=rows, metrics={"champion": score}, versions={"champion": served_version},
                    psi=psi, ph={"champion": ph}, competition=dict(
                        trigger=reasons, decision=decision, fit_end=cutoff, evaluation_month=month,
                        scores=comparisons, strategy=self.strategy, preset=self.rule.name,
                        streak=dict(self.streak)))
