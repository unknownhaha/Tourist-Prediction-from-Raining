"""Promotion and rollback on top of precomputed month predictions.

A promotion decided in month t serves from t+1. After ``rollback_months``
served months, the new model's total absolute error is compared with the
previous model's shadow forecast on those same months. A strictly worse new
model is removed for the following month.
"""

from __future__ import annotations

import numpy as np

from experiments.psi_adwin.adwin import ADWIN
from experiments.psi_adwin.triggers import trigger_fires
from src.stream.metrics import _block


def _next_month(month: str) -> str:
    year, mon = map(int, month.split("-"))
    mon += 1
    if mon == 13:
        year, mon = year + 1, 1
    return f"{year:04d}-{mon:02d}"


def replay(months: list[str], y: dict, naive: dict, predict, psi, candidates: tuple[str, ...],
           rule, *, trigger_mode: str, delta: float = 0.002, gate_months: int = 2,
           rollback_months: int = 2, refresh: bool = False) -> dict:
    active = "logdiff_expanding@2024-12"
    registry = [{
        "id": "v001",
        "model": active,
        "trained_through": "2024-12",
        "effective_from": months[0],
        "rolled_back": False,
    }]
    version = 1
    previous = None
    probation = None
    streak = {name: 0 for name in candidates}
    recent_psi: list[bool] = []
    last_rain = None
    reference_end = "2024-12"
    detector = ADWIN(delta)
    events = []
    served_rows = []
    for month in months:
        pred = np.asarray(predict(active, month), dtype=float)
        actual = np.asarray(y[month], dtype=float)
        lag = np.asarray(naive[month], dtype=float)
        adwin_hit = False
        for error in np.abs(actual - pred):
            adwin_hit = detector.update(float(error)) or adwin_hit
        psi_hit, features, rain_raw = psi(month, reference_end)
        rain_confirmed = bool(rain_raw and last_rain == _previous(month))
        if rain_raw and not rain_confirmed:
            features = [name for name in features if name != "rain_lag_1"]
            psi_hit = bool(features)
        last_rain = month if rain_raw else None
        fired = trigger_fires(trigger_mode, psi_hit, adwin_hit, recent_psi, gate_months)
        recent_psi.append(psi_hit)
        if psi_hit or adwin_hit or fired:
            events.append({
                "month": month, "event": "alert", "psi": psi_hit, "adwin": adwin_hit,
                "trigger": fired, "features": features, "reference_end": reference_end,
            })
        decision = "KEEP"
        if probation is not None:
            shadow = np.asarray(predict(probation["previous"], month), dtype=float)
            probation["new_abs"] += float(np.abs(actual - pred).sum())
            probation["old_abs"] += float(np.abs(actual - shadow).sum())
            probation["months"] += 1
            if probation["months"] >= rollback_months:
                worse = probation["new_abs"] > probation["old_abs"]
                events.append({
                    "month": month, "event": "rollback_check", "version": probation["version"],
                    "worse": worse, "new_abs": probation["new_abs"], "old_abs": probation["old_abs"],
                })
                if worse:
                    active = probation["previous"]
                    for item in registry:
                        if item["id"] == probation["version"]:
                            item["rolled_back"] = True
                    events.append({
                        "month": month, "event": "rollback", "from": probation["version"],
                        "to": probation["previous"], "effective_from": _next_month(month),
                    })
                streak = {name: 0 for name in candidates}
                probation = None
        elif fired:
            scores = {}
            for name in candidates:
                key = _challenger_key(name, month)
                hat = np.asarray(predict(key, month), dtype=float)
                scores[name] = _block(actual, hat, lag)
            events.append({"month": month, "event": "challengers", "scores": scores})
            served_block = _block(actual, pred, lag)
            for name in candidates:
                streak[name] = streak.get(name, 0) + 1 if rule.beats(scores[name], served_block) else 0
            winners = [name for name in candidates if streak[name] > 0]
            if winners:
                best = min(winners, key=lambda name: scores[name]["wape"])
                if streak[best] >= rule.confirm_months:
                    version += 1
                    new_key = _challenger_key(best, month)
                    version_id = f"v{version:03d}"
                    registry.append({
                        "id": version_id,
                        "model": new_key,
                        "trained_through": _previous(month),
                        "effective_from": _next_month(month),
                        "rolled_back": False,
                    })
                    events.append({
                        "month": month, "event": "promote", "winner": best, "version": version_id,
                        "model": new_key, "effective_from": _next_month(month),
                        "winner_wape": scores[best]["wape"], "champion_wape": served_block["wape"],
                    })
                    probation = {
                        "version": version_id, "previous": active, "new_model": new_key,
                        "new_abs": 0.0, "old_abs": 0.0, "months": 0, "starts": _next_month(month),
                    }
                    # The new model serves from the next month. This month stays with `active`.
                    pending = new_key
                    decision = "PROMOTE"
                    if refresh:
                        reference_end = _previous(month)
                    # Apply the switch at the start of the next loop by storing it.
                    probation["pending_model"] = pending
        else:
            streak = {name: 0 for name in candidates}
        for province_id, actual_i, pred_i, lag_i in zip(range(len(actual)), actual, pred, lag):
            served_rows.append({
                "year_month": month, "y_true": float(actual_i), "y_pred": float(pred_i),
                "y_naive": float(lag_i), "model": active, "decision": decision,
            })
        if probation is not None and probation.get("starts") == _next_month(month) and decision == "PROMOTE":
            active = probation["pending_model"]
    frame_y = np.array([row["y_true"] for row in served_rows])
    frame_p = np.array([row["y_pred"] for row in served_rows])
    frame_n = np.array([row["y_naive"] for row in served_rows])
    return {
        "events": events,
        "registry": registry,
        "served": _block(frame_y, frame_p, frame_n),
        "rows": served_rows,
    }


def _previous(month: str) -> str:
    year, mon = map(int, month.split("-"))
    mon -= 1
    if mon == 0:
        year, mon = year - 1, 12
    return f"{year:04d}-{mon:02d}"


def _challenger_key(name: str, decision_month: str) -> str:
    """Expanding and 12-month models are trained through the previous month.

    The seasonal model is the one scored on this decision month: same calendar
    month in the prior two years. If it is promoted, those weights stay frozen.
    """
    if name == "logdiff_seasonal_2y":
        return f"{name}@{decision_month}"
    return f"{name}@{_previous(decision_month)}"
