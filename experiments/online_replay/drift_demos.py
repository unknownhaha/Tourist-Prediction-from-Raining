"""Add-on drift demos (do not change the Champion or the 2025 replay).

Variant 1 (long replay): 2020-01..2023-12, month by month, Track A = RF logdiff refit monthly
(old Champion), Track B = the frozen EWA Champion (experts refit monthly, weights reset each
January). PSI reference = 2019 (Seasonal reference ending 2019-12; 2018 has no feature rows
because its lags would need 2017), never refreshed. ADWIN seed = each track's own one-step
OOS monthly WAPE for 2019-02..2019-12 (11 points; 2019-01 has no earlier modelling month).

Variant 2 (normal-period seed): the 2023 demo (PSI reference 2021-2022) but ADWIN seeded with
the same 2019 one-step OOS errors instead of the COVID-era 2021-2022 errors. 2019 is computed
without leakage (fit on 2019-01..t-1), so 2024 errors (the future) are not used.

Default detector = the approved local ADWIN (delta 0.002, window >= 32, >= 16 per side).
Illustrative runs (clearly labelled, not configs, not tuned on the replay years):
* local ADWIN with min_window 16 / min_side 8 (delta 0.002) on raw monthly WAPE;
* the same on *relative* WAPE (WAPE / mean of the track's own 2019 seed), see ScaledADWIN.
river's variance-aware ADWIN was tried too: its bound keeps a 2/(3m)*ln(2/delta) term that
assumes values in [0, 1], so it does not fire on 4 % -> 20 % WAPE steps either (adapter kept, tested).

The input gate runs non-strict here: COVID months push exp_lr_recent outside [-5, 5], which is a
hard stop for the 2025 replay. In the demos the problem is logged (gate_feature_problems) instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.online_replay import experts as X
from experiments.online_replay import selection as S
from experiments.online_replay.replay import _by_month, _champion_inputs, _explain, _summary, monthly_wape_series, run_track
from experiments.psi_adwin.adwin import ADWIN
from experiments.psi_adwin.run import Models, champion_oos_wape

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
SEED_MONTHS = ("2019-02", "2019-12")


class RiverADWIN:
    """Adapter: river.drift.ADWIN (variance-aware bound) with the ``window`` list the loop logs."""

    def __init__(self, delta: float = 0.002, clock: int = 1, min_window_length: int = 5, grace_period: int = 10):
        from river import drift
        self.inner = drift.ADWIN(delta=delta, clock=clock, min_window_length=min_window_length, grace_period=grace_period)
        self.values: list[float] = []
        self.window: list[float] = []

    def update(self, value: float) -> bool:
        self.values.append(float(value))
        self.inner.update(float(value))
        width = int(self.inner.width)
        self.window = self.values[-width:] if width else []
        return bool(self.inner.drift_detected)


class ScaledADWIN:
    """Local ADWIN on WAPE divided by a fixed scale (the mean of the seed WAPE).

    The Hoeffding bound assumes values in [0, 1]; normal monthly WAPE lives around 0.03-0.06,
    so a shift from 4 % to 20 % is invisible on the raw scale. Dividing by the seed mean makes
    the error relative to the normal level. ``window`` is kept in WAPE units for logging.
    """

    def __init__(self, scale: float, delta: float = 0.002, min_window: int = 16, min_side: int = 8):
        self.scale = float(scale)
        self.inner = ADWIN(delta, min_window=min_window, min_side=min_side)

    @property
    def window(self) -> list[float]:
        return [v * self.scale for v in self.inner.window]

    def update(self, value: float) -> bool:
        return self.inner.update(float(value) / self.scale)


# name -> factory(seed) ; seed = that track's seed WAPE list
DETECTORS = {
    "approved_adwin": lambda seed: None,
    "ILLUSTRATIVE_adwin_min16_side8": lambda seed: ADWIN(0.002, min_window=16, min_side=8),
    "ILLUSTRATIVE_adwin_min16_side8_relative": lambda seed: ScaledADWIN(float(np.mean(seed))),
    "ILLUSTRATIVE_adwin_min16_side8_relative_confirm1": lambda seed: ScaledADWIN(float(np.mean(seed))),
}
# Promotion preset overrides (the approved preset needs the trigger in 2 consecutive months).
PRESET_OVERRIDE = {"ILLUSTRATIVE_adwin_min16_side8_relative_confirm1": "margin_0005_confirm_1"}


def champion_series(ctx, sel, years):
    """EWA Champion predictions by month for whole years (weights reset each January)."""
    served, weights, experts = {}, {}, {}
    for year in years:
        part, pred, w, ex = _champion_inputs(ctx, sel, year)
        served.update(_by_month(part, pred))
        weights.update(w)
        for k, v in ex.items():
            experts.setdefault(k, {}).update(v)
    return served, weights, experts


def seeds_2019(ctx, sel, models):
    a = [float(r["wape"]) for r in champion_oos_wape(models, ctx.table, *SEED_MONTHS)]
    # 2019-01 has no earlier modelling month, so the Champion's 2019 EWA starts in February.
    from experiments.online_trials.models import ewa
    from experiments.online_replay.run import expert_preds  # noqa: F401  (same experts as the Champion)
    months = [m for m in X.months_of(2019) if m >= SEED_MONTHS[0]]
    part = X.part_of(ctx, months)
    ex = {"logdiff": X.monthly(ctx, "logdiff", sel["covid_variant"], months),
          "level": X.monthly(ctx, "level", sel["covid_variant"], months)}
    if sel["experts"] != "E2":
        raise NotImplementedError("drift demos assume the E2 Champion")
    pred, _ = ewa(part, ex, float(sel["eta"]))
    return a, monthly_wape_series(part, pred)


def _run_all(ctx, cfg, months, served_b, weights_b, experts_b, ref_end, trained_through, seed_a, seed_b, variant):
    out, logs, events = {}, [], []
    for det_name, factory in DETECTORS.items():
        for track, initial, served, seed, w, ex in (
            ("A", f"rf_logdiff_monthly@{trained_through}", {}, seed_a, None, None),
            ("B", f"champion@{trained_through}", {"champion": served_b}, seed_b, weights_b, experts_b),
        ):
            run_cfg = cfg
            if det_name in PRESET_OVERRIDE:
                run_cfg = {**cfg, "drift": {**cfg["drift"], "promotion_preset": PRESET_OVERRIDE[det_name]}}
            t = run_track(ctx, run_cfg, months, served, initial, trained_through, ref_end, seed, variant,
                          weights=w, experts=ex, gate=(det_name == "approved_adwin"), detector=factory(seed),
                          strict_gate=False)
            key = f"{track}|{det_name}"
            label = ("Track A: RF logdiff refit monthly" if track == "A" else "Track B: EWA Champion") + f" | {det_name}"
            s = _summary(t, label)
            s["seed"] = seed
            s["explanation"] = _explain(s)
            s["served_model_by_month"] = t["log"][["month", "served_model"]].values.tolist()
            out[key] = s
            logs.append(t["log"].assign(track=track, detector=det_name))
            events += [{"track": track, "detector": det_name, **e} for e in t["result"]["events"]]
            print(key, "|", s["explanation"], "| served", round(s["served"]["wape"] * 100, 2),
                  "| promotions", [p["month"] for p in s["promotions"]], "| rollbacks", [r["month"] for r in s["rollbacks"]], flush=True)
    return out, pd.concat(logs, ignore_index=True), events


def _write(prefix, out, log, events, extra):
    log.to_csv(RESULTS / f"{prefix}_log.csv", index=False)
    (RESULTS / f"{prefix}_events.jsonl").write_text("".join(json.dumps(e, default=float) + "\n" for e in events), encoding="utf-8")
    (RESULTS / f"{prefix}_summary.json").write_text(json.dumps({**extra, "runs": out}, indent=2, default=float) + "\n", encoding="utf-8")


def stage_drift_demos(ctx: X.Context) -> dict:
    cfg = S.load_config()
    sel = S.frozen_choice(cfg)
    models = Models(ctx.table, ctx.history)
    seed_a, seed_b = seeds_2019(ctx, sel, models)
    note = {"seed": "one-step OOS monthly WAPE 2019-02..2019-12 of each track (fit on 2019-01..t-1; 11 points)",
            "seed_A": seed_a, "seed_B": seed_b,
            "illustrative": "detectors named ILLUSTRATIVE_* are sensitivity runs, not configs; nothing was tuned on the replay years"}
    # Variant 1: long replay 2020-01..2023-12, reference 2019, trained through 2019-12.
    months = [m for y in (2020, 2021, 2022, 2023) for m in X.months_of(y)]
    served_b, weights_b, experts_b = champion_series(ctx, sel, (2020, 2021, 2022, 2023))
    out1, log1, ev1 = _run_all(ctx, cfg, months, served_b, weights_b, experts_b, "2019-12", "2019-12", seed_a, seed_b, sel["covid_variant"])
    _write("replay_2020", out1, log1, ev1, {**note, "psi_reference": "2019 same calendar month (2018 rows have no features), never refreshed",
                                            "months": [months[0], months[-1]]})
    # Variant 2: 2023 with the normal-period seed, reference 2021-2022 as in the original demo.
    m23 = X.months_of(2023)
    served23, weights23, experts23 = champion_series(ctx, sel, (2023,))
    out2, log2, ev2 = _run_all(ctx, cfg, m23, served23, weights23, experts23, "2022-12", "2022-12", seed_a, seed_b, sel["covid_variant"])
    _write("replay_2023_normalseed", out2, log2, ev2, {**note, "psi_reference": "2021-2022 same calendar month (as in the original 2023 demo)"})
    return {"v1": out1, "v2": out2}
