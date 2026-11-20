"""One serving model and a three-way, drift-triggered competition."""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from src.stream.drift import PageHinkley
from src.stream.metrics import _block
from src.stream.studio_models import FEATURES, PH_DELTA, PH_THRESHOLD, _fit_forest, _predict

CANDIDATES = ("rf_expanding", "rf_12m", "linear_expanding")


@dataclass
class ChampionPolicy:
    base: object
    active: object
    strategy: str = "rf_expanding"
    version: str = "v001-rf-fit-2024-12"
    revision: int = 1
    last_rain_lag_exceedance: str | None = None

    @property
    def versions(self):
        return {"champion": self.version}

    @property
    def baseline(self):
        return {"champion": self.base.baseline["rf_fixed"]}

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
        family = "linear" if self.strategy == "linear_expanding" else "rf_fixed"
        ph = self.base.ph_single(family, score["wape"])
        reasons = [name for name, event in psi.items() if event["alarm"]]
        if ph["alarm"]:
            reasons.append("Page-Hinkley")
        comparisons = {}
        decision = "KEEP"
        if reasons:
            candidates = {}
            for name in CANDIDATES:
                progress("RETRAIN", name)
                train_x, train_y = self.base.x_history, self.base.y_history
                if name == "rf_12m":
                    train_x, train_y = train_x[-12 * 77:], train_y[-12 * 77:]
                candidates[name] = (LinearRegression().fit(train_x, train_y)
                                    if name == "linear_expanding" else _fit_forest(train_x, train_y))
            progress("COMPARE")
            for name, candidate in candidates.items():
                comparisons[name] = _block(y, _predict(candidate, x), lag)
            best = min(CANDIDATES, key=lambda name: comparisons[name]["wape"])
            if comparisons[best]["wape"] < score["wape"] - 1e-12:
                self.active, self.strategy = candidates[best], best
                self.revision += 1
                self.version = f"v{self.revision:03d}-{best}-fit-{cutoff}"
                new_family = "linear" if best == "linear_expanding" else "rf_fixed"
                self.base.detectors[new_family] = PageHinkley(PH_DELTA, PH_THRESHOLD, initial_count=12)
                decision = "PROMOTE"
        self.base.x_history = np.concatenate([self.base.x_history, x])
        self.base.y_history = np.concatenate([self.base.y_history, y])
        progress("DEPLOY", decision)
        rows = pd.DataFrame(dict(year_month=month, province_id=features.province_id,
                                 province_th=labels.province_th, model="champion", version=served_version,
                                 y_true=y, y_pred=pred, y_naive=lag)).to_dict("records")
        return dict(rows=rows, metrics={"champion": score}, versions={"champion": served_version},
                    psi=psi, ph={"champion": ph}, competition=dict(trigger=reasons, decision=decision,
                    fit_end=cutoff, evaluation_month=month, scores=comparisons, strategy=self.strategy))
