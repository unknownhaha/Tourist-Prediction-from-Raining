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
           rollback_months: int = 2, refresh: bool = False, error_mode: str = "province_abs",
           seed: list[float] | None = None, initial_model: str = "logdiff_expanding@2024-12",
           trained_through: str = "2024-12", reference_end: str = "2024-12", log=None,
           adwin_direction: str = "both") -> dict:
    """Month-by-month serve / observe / detect / decide loop.

    The keyword defaults reproduce the approved 2025 primary run exactly. ``log`` is called
    once per month with that month's state (ADWIN window, cut direction, PSI, trigger,
    decision). ``adwin_direction="up"`` lets only a cut whose newer window has the higher
    mean error (degradation) count as an ADWIN alarm for the trigger; every cut is logged.
    """
    if adwin_direction not in {"both", "up"}:
        raise KeyError(adwin_direction)
    active = initial_model
    registry = [{
        "id": "v001",
        "model": active,
        "trained_through": trained_through,
        "effective_from": months[0],
        "rolled_back": False,
    }]
    version = 1
    previous = None
    probation = None
    streak = {name: 0 for name in candidates}
    recent_psi: list[bool] = []
    last_rain = None
    if error_mode not in {"province_abs", "monthly_wape"}:
        raise KeyError(error_mode)
    detector = ADWIN(delta)
    seed_cuts = 0
    for value in seed or []:
        seed_cuts += int(detector.update(float(value)))
    window_after_seed = len(detector.window)
    events = []
    served_rows = []
    adwin_alarm_months = []
    for month in months:
        n_events = len(events)
        served_model = active
        pred = np.asarray(predict(active, month), dtype=float)
        actual = np.asarray(y[month], dtype=float)
        lag = np.asarray(naive[month], dtype=float)
        before = list(detector.window)
        if error_mode == "province_abs":
            adwin_hit = False
            added = [float(e) for e in np.abs(actual - pred)]
            for error in added:
                adwin_hit = detector.update(error) or adwin_hit
        else:
            added = [float(_block(actual, pred, lag)["wape"])]
            adwin_hit = detector.update(added[0])
        cut = None
        if adwin_hit:
            full = before + added
            kept = list(detector.window)
            dropped = full[: len(full) - len(kept)]
            mean_before = float(np.mean(dropped)) if dropped else float("nan")
            mean_after = float(np.mean(kept)) if kept else float("nan")
            cut = {"mean_before": mean_before, "mean_after": mean_after, "n_dropped": len(dropped),
                   "direction": "up" if mean_after > mean_before else "down"}
            if adwin_direction == "up" and cut["direction"] != "up":
                adwin_hit = False
        if adwin_hit:
            adwin_alarm_months.append(month)
        psi_hit, features, rain_raw = psi(month, reference_end)
        rain_confirmed = bool(rain_raw and last_rain == _previous(month))
        if rain_raw and not rain_confirmed:
            features = [name for name in features if name != "rain_lag_1"]
            psi_hit = bool(features)
        last_rain = month if rain_raw else None
        fired = trigger_fires(trigger_mode, psi_hit, adwin_hit, recent_psi, gate_months)
        recent_psi.append(psi_hit)
        if psi_hit and not fired:
            # PSI by itself is a watch. It is logged and does not train a Challenger.
            events.append({
                "month": month, "event": "watch", "psi": True, "adwin": adwin_hit,
                "trigger": False, "retrain": False, "features": features,
                "reference_end": reference_end,
            })
        elif adwin_hit or fired:
            events.append({
                "month": month, "event": "alert", "psi": psi_hit, "adwin": adwin_hit,
                "trigger": fired, "retrain": fired, "features": features,
                "reference_end": reference_end,
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
        if log is not None:
            block = _block(actual, pred, lag)
            log({
                "month": month, "served_model": served_model, "served": block,
                "adwin_window_len": len(detector.window),
                "adwin_mean": float(np.mean(detector.window)) if detector.window else float("nan"),
                "adwin_cut": cut is not None, "adwin_alarm": bool(adwin_hit), "cut": cut,
                "psi": bool(psi_hit), "psi_features": list(features), "rain_confirmed": rain_confirmed,
                "trigger": bool(fired), "decision": decision, "reference_end": reference_end,
                "events": events[n_events:],
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
        "error_mode": error_mode,
        "delta": delta,
        "seed_length": 0 if seed is None else len(seed),
        "seed_cuts": seed_cuts,
        "window_after_seed": window_after_seed,
        "adwin_alarm_months": adwin_alarm_months,
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
