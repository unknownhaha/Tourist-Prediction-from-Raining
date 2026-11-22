"""Online / alternative model trials, orders 0-5 of plans/online-model-trials-plan.md.

Protocol (same as experiments/backtest): rolling origin. Frozen models train on
2019-01..(Y-1)-12 and forecast year Y; monthly/online models learn only from rows
before month t. Every hyperparameter, alpha, threshold, or variant is chosen on 2024
ONLY; 2023 and 2025 are held-out reports. 2024 was also the selection year of the
current RF logdiff (Protocol B), so every 2024 number here is optimistic.

Run: ./.venv/bin/python experiments/online_trials/run.py
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.backtest.run import per_month, run_year, score  # noqa: E402
from experiments.beat_seasonal_naive.run import _rain_history  # noqa: E402
from experiments.new_features.dq import DataQualityError, assess_feature_inputs  # noqa: E402
from experiments.new_features.features import build_new_features  # noqa: E402
from experiments.new_features.run import _load  # noqa: E402
from experiments.new_features.validate_inputs import (  # noqa: E402
    FeatureValidationError,
    validate_feature_inputs,
    write_validation_report,
)
from experiments.online_trials.models import (  # noqa: E402
    blend,
    build_trial_table,
    ewa,
    lgbm_spec,
    predict_basefix,
    predict_spec,
    ridge_spec,
    river_linear_year,
    switch,
    ts_year,
)
from src.model.leakage import shift_year_month  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning)
HERE = Path(__file__).resolve().parent
YEARS = (2023, 2024, 2025)
SELECT_YEAR = 2024
REFERENCE_FROZEN = {
    "rf_logdiff": {2023: 0.2577, 2024: 0.0631, 2025: 0.0479},
    "rf_level": {2023: 0.0857, 2024: 0.0854, 2025: 0.0627},
    "seasonal_naive": {2023: 0.3272, 2024: 0.1115, 2025: 0.0548},
}

BASEFIX_VARIANTS = {
    "basefix": dict(switch_base=True),
    "basefix_momentum": dict(switch_base=True, momentum=True),
    "basefix_gap": dict(switch_base=True, gap_feature=True),
    "basefix_momentum_gap": dict(switch_base=True, momentum=True, gap_feature=True),
    "momentum_only": dict(switch_base=False, momentum=True),
}
ALPHAS = [round(a, 2) for a in np.arange(0.0, 1.0001, 0.05)]
THRESHOLDS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0, float("inf")]
ETAS = [0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]
LGBM_GRID = {
    "leaves15_lr0.05_n300": dict(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=20),
    "leaves31_lr0.05_n300": dict(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=20),
    "leaves15_lr0.1_n200": dict(n_estimators=200, learning_rate=0.1, num_leaves=15, min_child_samples=20),
    "leaves7_lr0.05_n500": dict(n_estimators=500, learning_rate=0.05, num_leaves=7, min_child_samples=20),
}
SNARIMAX_GRID = {
    f"p{p}d{d}q{q}_sp{sp}sd{sd}sq{sq}_lr{lr}": dict(p=p, d=d, q=q, sp=sp, sd=sd, sq=sq, lr=lr)
    for (p, d, q, sp, sd, sq) in [(1, 0, 0, 0, 1, 0), (2, 0, 0, 0, 1, 0), (1, 0, 1, 0, 1, 0), (1, 0, 0, 1, 1, 0)]
    for lr in (0.01, 0.003)
}
HW_GRID = {
    f"a{a}_b0.01_g{g}": dict(alpha=a, beta=0.01, gamma=g) for a in (0.2, 0.5, 0.8) for g in (0.1, 0.3)
}
RIVER_GRID = {
    "river_linreg": {f"lr{lr}_l2{l2}": dict(lr=lr, l2=l2) for lr in (0.001, 0.0003) for l2 in (0.0, 0.01)},
    "river_pa": {f"C{c}": dict(C=c, eps=0.1) for c in (0.001, 0.01, 0.1)},
    "river_blr": {f"smooth{s}": dict(smoothing=s) for s in (None, 0.9995, 0.999, 0.995)},
}
RIDGE_ALPHAS = (0.1, 1.0, 10.0, 100.0, 1000.0)


def check_inputs(panel: pd.DataFrame) -> tuple[dict, dict]:
    """The existing validation and DQ gates, run before any feature reaches a model."""
    extra = build_new_features(panel)
    try:
        validation = validate_feature_inputs(panel, extra)
    except FeatureValidationError as exc:
        write_validation_report(exc.report, HERE / "validation_report.json")
        raise
    write_validation_report(validation, HERE / "validation_report.json")
    try:
        quality = assess_feature_inputs(panel, extra)
    except DataQualityError as exc:
        (HERE / "dq_report.json").write_text(json.dumps(exc.report, indent=2), encoding="utf-8")
        raise
    (HERE / "dq_report.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    return validation, quality


def months_of(year: int) -> list[str]:
    return [f"{year}-{m:02d}" for m in range(1, 13)]


def batch_year(fn, year: int, mode: str) -> np.ndarray:
    """fn(train_end, months) -> predictions. Frozen: one fit; monthly: refit per month."""
    if mode == "frozen":
        return fn(f"{year - 1}-12", months_of(year))
    return np.concatenate([fn(shift_year_month(month, 1), [month]) for month in months_of(year)])


class Recorder:
    def __init__(self, table: pd.DataFrame):
        self.table = table
        self.parts = {y: table.loc[table["year_month"].between(f"{y}-01", f"{y}-12")] for y in YEARS}
        self.rows, self.month_rows, self.preds = [], [], {}

    def add(self, order: str, model: str, config: str, year: int, mode: str, pred: np.ndarray, selected: bool = True):
        part = self.parts[year]
        m = score(part, pred)
        m["gap_vs_naive"] = m["wape"] - m["naive_wape"]
        self.rows.append({"order": order, "model": model, "config": config, "year": year, "mode": mode,
                          "selected_on_2024": selected, **m})
        if selected:
            self.preds[(model, year, mode)] = pred
            for month, value in per_month(part, pred).items():
                self.month_rows.append({"order": order, "model": model, "year": year, "mode": mode,
                                        "year_month": month, "wape": value})
        print(f"[{order}] {model:<24} {config:<28} {year} {mode:<8} wape={m['wape']:.4f}", flush=True)
        return m["wape"]


def select(scores: dict[str, float]) -> str:
    return min(scores, key=lambda k: (scores[k], k))


def main() -> None:
    t0 = time.time()
    timings, notes = {}, []
    panel = _load()
    validation, quality = check_inputs(panel)
    print("VALIDATION warnings", validation.get("n_warnings"), "hard", validation.get("n_hard"), "DQ", quality["scores"], flush=True)
    table = build_trial_table(panel)
    history = _rain_history(panel)
    rec = Recorder(table)
    selection = {}

    # ---- order 0: reproduce the reference backtest
    s = time.time()
    repro = {}
    for model in ("rf_logdiff", "rf_level", "seasonal_naive"):
        for year in YEARS:
            for mode in ("frozen", "monthly"):
                if model == "seasonal_naive" and mode == "monthly":
                    pred = rec.preds[(model, year, "frozen")]
                else:
                    pred = run_year(table, history, year, model, mode)
                w = rec.add("0", model, "reference", year, mode, pred)
                if mode == "frozen":
                    repro[f"{model}|{year}"] = {"wape": w, "reference": REFERENCE_FROZEN[model][year],
                                                "match": round(w, 4) == REFERENCE_FROZEN[model][year]}
    if not all(v["match"] for v in repro.values()):
        raise SystemExit(f"order 0 did not reproduce the reference numbers: {repro}")
    timings["order0"] = time.time() - s

    # ---- order 1: base-year fix for RF logdiff
    s = time.time()
    scores = {}
    for name, kw in BASEFIX_VARIANTS.items():
        fn = lambda te, ms, kw=kw: predict_basefix(table, history, te, ms, **kw)  # noqa: E731
        scores[name] = score(rec.parts[SELECT_YEAR], batch_year(fn, SELECT_YEAR, "frozen"))["wape"]
    chosen = select(scores)
    selection["order1_basefix"] = {"metric": "2024 frozen WAPE", "scores": scores, "chosen": chosen,
                                   "reference_rf_logdiff_2024": REFERENCE_FROZEN["rf_logdiff"][2024]}
    for name, kw in BASEFIX_VARIANTS.items():
        fn = lambda te, ms, kw=kw: predict_basefix(table, history, te, ms, **kw)  # noqa: E731
        for year in YEARS:
            for mode in ("frozen", "monthly"):
                rec.add("1", f"rf_logdiff_{name}", name, year, mode, batch_year(fn, year, mode), selected=True)
    timings["order1"] = time.time() - s

    # ---- order 2: blend / switch / EWA of RF logdiff + RF level
    s = time.time()
    sel_part = rec.parts[SELECT_YEAR]
    p = rec.preds
    a_scores = {a: score(sel_part, blend(p[("rf_logdiff", 2024, "frozen")], p[("rf_level", 2024, "frozen")], a))["wape"] for a in ALPHAS}
    alpha = select(a_scores)
    t_scores = {str(th): score(sel_part, switch(sel_part, p[("rf_logdiff", 2024, "frozen")], p[("rf_level", 2024, "frozen")], th))["wape"] for th in THRESHOLDS}
    thr = float(select(t_scores))
    e_scores, traces = {}, {}
    for eta in ETAS:
        pred, _ = ewa(sel_part, {"logdiff": p[("rf_logdiff", 2024, "monthly")], "level": p[("rf_level", 2024, "monthly")]}, eta)
        e_scores[eta] = score(sel_part, pred)["wape"]
    eta = select(e_scores)
    selection["order2_blend"] = {"metric": "2024 frozen WAPE", "scores": {str(k): v for k, v in a_scores.items()}, "chosen_alpha": alpha}
    selection["order2_switch"] = {"metric": "2024 frozen WAPE", "scores": t_scores, "chosen_threshold": thr,
                                  "note": "The COVID-base branch (is_covid_lag12=1 -> level) is fixed by design; 2024 has no COVID-base rows, so 2024 cannot tune it."}
    selection["order2_ewa"] = {"metric": "2024 monthly WAPE (online weights over monthly-refit experts)",
                               "scores": {str(k): v for k, v in e_scores.items()}, "chosen_eta": eta}
    for year in YEARS:
        part = rec.parts[year]
        for mode in ("frozen", "monthly"):
            ld, lv = p[("rf_logdiff", year, mode)], p[("rf_level", year, mode)]
            rec.add("2", "blend_logdiff_level", f"alpha={alpha}", year, mode, blend(ld, lv, alpha))
            rec.add("2", "switch_logdiff_level", f"covid_base_or_|yoy|>{thr}", year, mode, switch(part, ld, lv, thr))
        pred, trace = ewa(part, {"logdiff": p[("rf_logdiff", year, "monthly")], "level": p[("rf_level", year, "monthly")]}, eta)
        traces[year] = trace
        rec.add("2", "ewa_logdiff_level", f"eta={eta}", year, "monthly", pred)
    selection["order2_ewa"]["weights"] = {str(k): v for k, v in traces.items()}
    timings["order2"] = time.time() - s

    # ---- order 3: LightGBM challenger
    s = time.time()
    scores, frozen_cache = {}, {}
    for tag, params in LGBM_GRID.items():
        spec = lgbm_spec(tag, **params)
        fn = lambda te, ms, spec=spec: predict_spec(table, history, te, ms, spec)  # noqa: E731
        for year in YEARS:
            frozen_cache[(tag, year)] = batch_year(fn, year, "frozen")
        scores[tag] = score(sel_part, frozen_cache[(tag, SELECT_YEAR)])["wape"]
    chosen = select(scores)
    selection["order3_lgbm"] = {"metric": "2024 frozen WAPE", "scores": scores, "chosen": chosen}
    for tag in LGBM_GRID:
        for year in YEARS:
            rec.add("3", "lgbm_logdiff", tag, year, "frozen", frozen_cache[(tag, year)], selected=tag == chosen)
    spec = lgbm_spec(chosen, **LGBM_GRID[chosen])
    fn = lambda te, ms: predict_spec(table, history, te, ms, spec)  # noqa: E731
    for year in YEARS:
        rec.add("3", "lgbm_logdiff", chosen, year, "monthly", batch_year(fn, year, "monthly"))
    notes.append("Order 3 optional extra (LightGBM init_model warm-start vs full refit) was not run: a full monthly refit takes well under a second at this data size, so warm-starting has no speed case to make; left for later.")
    timings["order3"] = time.time() - s

    # ---- order 4: per-province time series (river)
    s = time.time()
    for kind, grid in (("snarimax", SNARIMAX_GRID), ("holtwinters", HW_GRID)):
        cache, scores = {}, {}
        for tag, params in grid.items():
            try:
                cache[(tag, SELECT_YEAR)] = ts_year(panel, table, SELECT_YEAR, kind, params)
                scores[tag] = score(sel_part, cache[(tag, SELECT_YEAR)][1])["wape"]
            except Exception as exc:  # recorded, not fatal
                notes.append(f"Order 4 {kind} {tag} failed on 2024: {exc!r}")
        chosen = select(scores)
        selection[f"order4_{kind}"] = {"metric": "2024 online (monthly) WAPE", "scores": scores, "chosen": chosen}
        for tag in grid:
            if (tag, SELECT_YEAR) in cache and tag != chosen:
                rec.add("4", kind, tag, SELECT_YEAR, "monthly", cache[(tag, SELECT_YEAR)][1], selected=False)
        for year in YEARS:
            frozen, online = cache[(chosen, year)] if year == SELECT_YEAR else ts_year(panel, table, year, kind, grid[chosen])
            rec.add("4", kind, chosen, year, "frozen", frozen)
            rec.add("4", kind, chosen, year, "monthly", online)
    timings["order4"] = time.time() - s

    # ---- order 5: incremental linear (river) + expanding Ridge
    s = time.time()
    for kind, grid in RIVER_GRID.items():
        cache, scores = {}, {}
        for tag, params in grid.items():
            cache[(tag, SELECT_YEAR)] = river_linear_year(table, history, SELECT_YEAR, kind, params)
            scores[tag] = score(sel_part, cache[(tag, SELECT_YEAR)][1])["wape"]
        chosen = select(scores)
        selection[f"order5_{kind}"] = {"metric": "2024 online (monthly) WAPE", "scores": scores, "chosen": chosen}
        for tag in grid:
            if tag != chosen:
                rec.add("5", kind, tag, SELECT_YEAR, "monthly", cache[(tag, SELECT_YEAR)][1], selected=False)
        for year in YEARS:
            frozen, online = cache[(chosen, year)] if year == SELECT_YEAR else river_linear_year(table, history, year, kind, grid[chosen])
            rec.add("5", kind, chosen, year, "frozen", frozen)
            rec.add("5", kind, chosen, year, "monthly", online)
    scores, frozen_cache = {}, {}
    for a in RIDGE_ALPHAS:
        spec = ridge_spec(a)
        fn = lambda te, ms, spec=spec: predict_spec(table, history, te, ms, spec, clip=True, log_features=True)  # noqa: E731
        for year in YEARS:
            frozen_cache[(a, year)] = batch_year(fn, year, "frozen")
        scores[str(a)] = score(sel_part, frozen_cache[(a, SELECT_YEAR)])["wape"]
    chosen = float(select(scores))
    selection["order5_ridge"] = {"metric": "2024 frozen WAPE", "scores": scores, "chosen_alpha": chosen}
    for a in RIDGE_ALPHAS:
        for year in YEARS:
            rec.add("5", "ridge_expanding", f"alpha={a}", year, "frozen", frozen_cache[(a, year)], selected=a == chosen)
    spec = ridge_spec(chosen)
    fn = lambda te, ms: predict_spec(table, history, te, ms, spec, clip=True, log_features=True)  # noqa: E731
    for year in YEARS:
        rec.add("5", "ridge_expanding", f"alpha={chosen}", year, "monthly", batch_year(fn, year, "monthly"))
    timings["order5"] = time.time() - s

    # ---- outputs
    results = pd.DataFrame(rec.rows)
    results.to_csv(HERE / "results.csv", index=False)
    pd.DataFrame(rec.month_rows).to_csv(HERE / "monthly_wape.csv", index=False)
    pred_frame = table.loc[table["year_month"].between("2023-01", "2025-12"), ["province_id", "year_month", "guest_arrivals_total", "guest_lag_12"]].copy()
    for (model, year, mode), pred in rec.preds.items():
        col = f"{model}|{mode}"
        if col not in pred_frame:
            pred_frame[col] = np.nan
        pred_frame.loc[rec.parts[year].index, col] = pred
    pred_frame.reset_index(drop=True).to_parquet(HERE / "predictions.parquet", index=False)
    timings["total"] = time.time() - t0
    payload = {
        "protocol": {
            "frozen": "fit on 2019-01..(Y-1)-12, forecast Y (time-series models: 12-step forecast from December)",
            "monthly": "expanding refit (batch) or predict-then-learn (online) using rows before month t only",
            "selection": "every variant / hyperparameter / alpha / threshold / eta chosen on 2024 only; 2023 and 2025 are held out",
            "note_2024": "2024 was also the selection year of RF logdiff (Protocol B); 2024 scores are optimistic.",
        },
        "order0_reproduction": repro,
        "validation": {"n_warnings": validation.get("n_warnings"), "n_hard": validation.get("n_hard")},
        "dq_scores": quality["scores"],
        "selection": selection,
        "notes": notes,
        "timings_seconds": timings,
        "results": rec.rows,
    }
    (HERE / "results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print("done in", round(timings["total"]), "s", flush=True)


if __name__ == "__main__":
    main()
