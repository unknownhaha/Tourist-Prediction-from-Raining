"""Step 8: one results table (results.csv / results.json) and monthly WAPE per model."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from experiments.backtest.run import per_month, run_year
from experiments.online_replay import experts as X
from experiments.online_replay import selection as S
from experiments.online_trials.models import ewa

COLS = ("wape", "mae", "rmse", "mase", "bias", "naive_wape", "gap_vs_naive", "provinces_beating_naive", "provinces")


def stage_results(ctx: X.Context) -> pd.DataFrame:
    from experiments.online_replay.rain_report import RAIN_MODEL_COLUMNS
    from experiments.online_replay.run import RESULTS, _read, _write, champion, expert_preds, metrics
    cfg = S.load_config()
    sel = S.frozen_choice(cfg)
    rows, mrows = [], []

    def add(model, desc, year, part, pred, selected=False):
        m = metrics(part, pred)
        rows.append({"model": model, "desc": desc, "year": year, "selected_on_2024": selected, **{k: m[k] for k in COLS}})
        for month, v in per_month(part, pred).items():
            mrows.append({"model": model, "year_month": month, "wape": v})

    for year in (2023, 2024, 2025):
        part = X.part_of(ctx, X.months_of(year))
        add("seasonal_naive", "lag-12", year, part, part["guest_lag_12"].to_numpy(dtype=float))
        logd = run_year(ctx.table, ctx.history, year, "rf_logdiff", "monthly")
        add("old_champion_rf_logdiff", "RF logdiff, refit monthly (old Champion)", year, part, logd)
        lev = X.monthly(ctx, "level", "C0", X.months_of(year))
        old_ewa, _ = ewa(part, {"logdiff": logd, "level": lev}, 10.0)
        add("ewa_old_logdiff_level_eta10", "EWA(RF logdiff, RF level), eta 10 (online_trials)", year, part, old_ewa)
        base = expert_preds(ctx, "C0", year)
        b, _ = ewa(part, base, float(cfg["champion_baseline_eta"]))
        add("champion_baseline_C0_E2_eta10", "EWA(logdiff+exp_lr_recent3, level), eta 10 (data-review baseline)", year, part, b)
        final, _, ex = champion(ctx, sel, year)
        name = f"final_champion_{sel['covid_variant']}_{sel['experts']}_eta{sel['eta']:g}"
        add(name, "frozen choice from 2024", year, part, final, True)
        for k, v in ex.items():
            add(f"expert_{k}_{sel['covid_variant']}", f"single expert inside the final Champion: {k}", year, part, v)
        nr, _, _ = champion(ctx, sel, year, drop=RAIN_MODEL_COLUMNS)
        add(name + "_no_rain", "final Champion with every rain column dropped (ablation)", year, part, nr)
    frame = pd.DataFrame(rows)
    frame["wape_pct"] = (frame["wape"] * 100).round(2)
    _write("results.csv", frame)
    monthly = pd.DataFrame(mrows)
    _write("monthly_wape.csv", monthly)
    payload = {
        "selection": sel,
        "reproduce": _read("reproduce.json"),
        "dq": {k: v for k, v in _read("dq_report.json").items() if k in ("scores", "hard_fail")},
        "dq_accuracy": {k: v for k, v in _read("dq_report.json")["accuracy"].items() if k.startswith("accuracy")},
        "replay_2025": {k: v for k, v in _read("replay_2025_summary.json").items() if k not in ("registry",)},
        "demo_2023": _read("replay_2023_demo_summary.json"),
        "rows": frame.to_dict("records"),
    }
    _write("results.json", payload)
    print(frame.pivot_table(index="model", columns="year", values="wape_pct").to_string(), flush=True)
    return frame
