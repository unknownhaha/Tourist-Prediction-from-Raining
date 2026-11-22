"""2025 online replay: reproduce, DQ, COVID A/B, EWA 2 vs 3, replay, 2023 demo, rain, charts.

Run: ./.venv/bin/python experiments/online_replay/run.py --stage all
Stages: reproduce dq covid ewa replay2025 demo2023 rain results charts (or all).

Protocol: rolling origin, monthly predict-then-learn (fit on 2019-01..t-1, predict t).
Every choice is made on 2024 monthly WAPE and frozen in config.json before 2023/2025
are scored (selection.frozen_choice refuses otherwise).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.backtest.run import run_year, score  # noqa: E402
from experiments.new_features.dq import assess_feature_inputs, load_config as load_dq_config  # noqa: E402
from experiments.new_features.features import build_new_features  # noqa: E402
from experiments.new_features.validate_inputs import validate_feature_inputs  # noqa: E402
from experiments.online_replay import experts as X  # noqa: E402
from experiments.online_replay import selection as S  # noqa: E402
from experiments.online_trials.models import ewa  # noqa: E402
from src.stream.metrics import wape  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning)
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CHARTS = HERE / "charts"
CACHE = RESULTS / "predictions.parquet"
YEARS = (2023, 2024, 2025)
ONLINE_TRIALS = HERE.parent / "online_trials" / "results.csv"
# Data-review reference (pred_monthly_*.csv), checked to +-0.01 pt because the port produces them.
CHAMPION_REF = {2023: 6.50, 2024: 5.59, 2025: 4.54}
NO_RAIN_REF = {2023: 6.66, 2024: 5.72, 2025: 4.44}


def _write(name: str, payload) -> Path:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / name
    if isinstance(payload, pd.DataFrame):
        payload.to_csv(path, index=False)
    else:
        path.write_text(json.dumps(payload, indent=2, default=float) + "\n", encoding="utf-8")
    return path


def _read(name: str):
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def metrics(part: pd.DataFrame, pred: np.ndarray) -> dict:
    m = score(part, pred)
    m["gap_vs_naive"] = m["wape"] - m["naive_wape"]
    return m


def third_expert(part: pd.DataFrame) -> np.ndarray:
    """No-ML seasonal last-month forecast; lag-12 where the profile is undefined."""
    s = part["seasonal_last_month_forecast"].to_numpy(dtype=float)
    return np.where(np.isfinite(s), s, part["guest_lag_12"].to_numpy(dtype=float))


def expert_preds(ctx: X.Context, variant: str, year: int, n: int = 2, drop=()) -> dict[str, np.ndarray]:
    months = X.months_of(year)
    out = {"logdiff": X.monthly(ctx, "logdiff", variant, months, drop),
           "level": X.monthly(ctx, "level", variant, months, drop)}
    if n == 3:
        out["seasonal"] = third_expert(X.part_of(ctx, months))
    return out


def champion(ctx: X.Context, sel: dict, year: int, drop=()) -> tuple[np.ndarray, list[dict], dict]:
    """The frozen Champion: EWA of the selected experts, weights reset each January."""
    n = 3 if sel["experts"] == "E3" else 2
    ex = expert_preds(ctx, sel["covid_variant"], year, n, drop)
    pred, trace = ewa(X.part_of(ctx, X.months_of(year)), ex, float(sel["eta"]))
    return pred, trace, ex


# --------------------------------------------------------------------------- step 0 + 2


def stage_reproduce(ctx: X.Context) -> dict:
    ref = pd.read_csv(ONLINE_TRIALS)
    out = {"online_trials": {}, "champion": {}, "no_rain": {}}
    ok = True
    for year in YEARS:
        part = X.part_of(ctx, X.months_of(year))
        y = part["guest_arrivals_total"].to_numpy(dtype=float)
        logd = run_year(ctx.table, ctx.history, year, "rf_logdiff", "monthly")
        lev = run_year(ctx.table, ctx.history, year, "rf_level", "monthly")
        port_lev = X.monthly(ctx, "level", "C0", X.months_of(year))
        mix, _ = ewa(part, {"logdiff": logd, "level": lev}, 10.0)
        rows = {"rf_logdiff": wape(y, logd), "rf_level": wape(y, lev), "ewa_logdiff_level": wape(y, mix),
                "seasonal_naive": wape(y, part["guest_lag_12"].to_numpy(dtype=float))}
        for model, value in rows.items():
            r = ref.loc[(ref["model"] == model) & (ref["year"] == year) & (ref["mode"] == "monthly")
                        & ((ref["config"] == "eta=10.0") if model.startswith("ewa") else (ref["order"].astype(str) == "0"))]
            target = float(r["wape"].iloc[0])
            match = round(value, 4) == round(target, 4)
            ok &= match
            out["online_trials"][f"{model}|{year}"] = {"wape": value, "reference": target, "match_4dp": match}
        out["online_trials"][f"level_port_equals_run_year|{year}"] = {"max_abs_diff": float(np.abs(port_lev - lev).max())}
        ok &= float(np.abs(port_lev - lev).max()) < 1e-6
        champ = X.monthly(ctx, "logdiff", "C0", X.months_of(year))
        cmix, _ = ewa(part, {"logdiff": champ, "level": port_lev}, 10.0)
        nr = X.monthly(ctx, "logdiff", "C0", X.months_of(year), X.RAIN, exp=("exp_lr_recent",))
        nmix, _ = ewa(part, {"logdiff": nr, "level": port_lev}, 10.0)
        for key, value, target in (("champion", wape(y, cmix), CHAMPION_REF[year]), ("no_rain", wape(y, nmix), NO_RAIN_REF[year])):
            match = abs(value * 100 - target) <= 0.01 + 1e-9
            ok &= match
            out[key][year] = {"wape": value, "wape_pct": round(value * 100, 4), "reference_pct": target, "within_0.01pt": match,
                              "logdiff_expert_wape": wape(y, champ if key == "champion" else nr)}
        print("reproduce", year, {k: round(v * 100, 2) for k, v in rows.items()}, "champion", round(wape(y, cmix) * 100, 4),
              "no_rain", round(wape(y, nmix) * 100, 4), flush=True)
    out["all_match"] = bool(ok)
    _write("reproduce.json", out)
    if not ok:
        raise SystemExit(f"reproduction failed: {json.dumps(out, default=float)[:2000]}")
    return out


# --------------------------------------------------------------------------- step 1


def stage_dq(ctx: X.Context) -> dict:
    extra = build_new_features(ctx.panel)
    validation = validate_feature_inputs(ctx.panel, extra)
    report = assess_feature_inputs(ctx.panel, extra)
    legacy = assess_feature_inputs(ctx.panel, extra, {**load_dq_config(), "accuracy_rule": "v1"})
    report["legacy_v1_scores"] = legacy["scores"]
    _write("validation_report.json", validation)
    _write("dq_report.json", report)
    a = report["accuracy"]
    print("DQ v2 overall", report["scores"]["overall"], "normal", a["accuracy_normal"], "covid", a["accuracy_covid"],
          "covid_base", a["accuracy_covid_base"], "legacy", a["accuracy_legacy_v1"], "validation warnings",
          validation.get("n_warnings"), "hard", validation.get("n_hard"), flush=True)
    return report


# --------------------------------------------------------------------------- step 3 + 4 (selection on 2024)


def stage_select(ctx: X.Context) -> dict:
    cfg = S.load_config()
    part24 = X.part_of(ctx, X.months_of(2024))
    candidates = {v: expert_preds(ctx, v, 2024) for v in cfg["covid_candidates"]}
    covid = S.select_covid(part24, candidates, cfg["etas"], cfg["covid_tie_break_pt"])
    v = covid["chosen"]
    ex = S.select_experts(part24, expert_preds(ctx, v, 2024, 2), expert_preds(ctx, v, 2024, 3), cfg["etas"], cfg["e3_min_gain_pt"])
    chosen = ex[ex["chosen"]]
    sel = {"covid_variant": v, "experts": ex["chosen"], "eta": chosen["eta"], "covid_ab": covid, "ewa_ab": ex,
           "rule": "lowest 2024 monthly EWA WAPE; COVID tie < 0.05 pt -> fewer features; E3 kept only if >= 0.02 pt better"}
    S.freeze(cfg, sel)
    print("SELECTED on 2024:", v, ex["chosen"], "eta", chosen["eta"], "2024", round(chosen["wape_2024"] * 100, 4), flush=True)
    return sel


def stage_covid(ctx: X.Context) -> pd.DataFrame:
    cfg = S.load_config()
    sel = S.frozen_choice(cfg)
    grid = sel["covid_ab"]["grid"]
    rows = []
    for variant in list(cfg["covid_candidates"]) + list(cfg["covid_report_only"]):
        eta = grid[variant]["eta"] if variant in grid else S.select_eta(X.part_of(ctx, X.months_of(2024)), expert_preds(ctx, variant, 2024), cfg["etas"])[0]
        for year in YEARS:
            part = X.part_of(ctx, X.months_of(year))
            pred, _ = ewa(part, expert_preds(ctx, variant, year), eta)
            m = metrics(part, pred)
            rows.append({"variant": variant, "desc": X.VARIANTS[variant]["desc"], "year": year, "eta": eta,
                         "selected": variant == sel["covid_variant"], "candidate": variant in cfg["covid_candidates"],
                         **{k: m[k] for k in ("wape", "mae", "rmse", "mase", "bias", "naive_wape", "gap_vs_naive", "provinces_beating_naive")}})
    frame = pd.DataFrame(rows)
    _write("covid_ab.csv", frame)
    chosen = sel["covid_variant"]
    table = []
    for label, variant in ((f"with: selected ({chosen})", chosen), ("with: binary flags (C0)", "C0"), ("without COVID info (C4)", "C4")):
        if label.endswith("(C0)") and chosen == "C0":
            continue
        r = frame.loc[frame["variant"] == variant].set_index("year")["wape"]
        table.append({"row": label, "variant": variant, **{f"wape_{y}_pct": round(r[y] * 100, 2) for y in YEARS}})
    _write("covid_with_without.csv", pd.DataFrame(table))
    best_heldout = {y: frame.loc[frame["year"] == y].query("candidate").sort_values("wape")["variant"].iloc[0] for y in (2023, 2025)}
    print(frame[["variant", "year", "eta", "wape"]].to_string(), "\nheld-out best among candidates:", best_heldout, flush=True)
    return frame


def stage_ewa(ctx: X.Context) -> pd.DataFrame:
    cfg = S.load_config()
    sel = S.frozen_choice(cfg)
    ab = sel["ewa_ab"]
    rows, wrows = [], []
    for name, n in (("E2", 2), ("E3", 3)):
        eta = ab[name]["eta"]
        for year in YEARS:
            part = X.part_of(ctx, X.months_of(year))
            ex = expert_preds(ctx, sel["covid_variant"], year, n)
            pred, trace = ewa(part, ex, eta)
            m = metrics(part, pred)
            y = part["guest_arrivals_total"].to_numpy(dtype=float)
            rows.append({"variant": name, "experts": "+".join(ex), "year": year, "eta": eta, "selected": name == sel["experts"],
                         **{k: m[k] for k in ("wape", "mae", "rmse", "mase", "bias", "naive_wape", "gap_vs_naive", "provinces_beating_naive")},
                         **{f"expert_wape_{k}": wape(y, v) for k, v in ex.items()}})
            for t in trace:
                wrows.append({"year": year, "month": t["year_month"], "variant": name,
                              "w_logdiff": t["w_logdiff"], "w_level": t["w_level"], "w_seasonal": t.get("w_seasonal", 0.0)})
    frame = pd.DataFrame(rows)
    _write("ewa_ab.csv", frame)
    _write("ewa_weights.csv", pd.DataFrame(wrows))
    print(frame[["variant", "year", "eta", "wape"]].to_string(), flush=True)
    return frame


STAGES = ["reproduce", "dq", "select", "covid", "ewa", "replay2025", "demo2023", "rain", "results", "charts"]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="all", choices=STAGES + ["all"])
    args = parser.parse_args(argv)
    stages = STAGES if args.stage == "all" else [args.stage]
    t0 = time.time()
    ctx = X.Context.build()
    X.load_cache(ctx, CACHE)
    timings = {}
    try:
        for stage in stages:
            s = time.time()
            if stage == "charts":
                from experiments.online_replay import charts
                charts.main()
            elif stage in ("replay2025", "demo2023"):
                from experiments.online_replay import replay
                getattr(replay, f"stage_{stage}")(ctx)
            elif stage in ("rain",):
                from experiments.online_replay import rain_report
                rain_report.stage_rain(ctx)
            elif stage == "results":
                from experiments.online_replay import report
                report.stage_results(ctx)
            else:
                globals()[f"stage_{stage}"](ctx)
            timings[stage] = round(time.time() - s, 1)
            X.save_cache(ctx, CACHE)
            print(f"== {stage} done in {timings[stage]}s", flush=True)
    finally:
        X.save_cache(ctx, CACHE)
    if args.stage == "all":
        _write("timings.json", {**timings, "total_s": round(time.time() - t0, 1)})


if __name__ == "__main__":
    main()
