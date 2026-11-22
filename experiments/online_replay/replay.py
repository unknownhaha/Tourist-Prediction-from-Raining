"""Month-by-month replay with visible drift logging (2025) and the 2023 drift demo.

Per month t, in order: input gate (DQ) -> serve the Champion forecast for t (fit on ..t-1)
-> observe t -> ADWIN on the monthly WAPE -> PSI (Seasonal-2Y reference) on the four
stream features -> psi_and_adwin trigger -> prepare_retrain (challengers fit on ..t-1
scored on t, preset margin_0005_confirm_2, rollback after 2 months) -> EWA weights for
t+1 use t's losses. The loop itself is ``experiments.psi_adwin.policy.replay``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.online_replay import experts as X
from experiments.online_replay import selection as S
from experiments.online_replay.gate import check_inputs
from experiments.psi_adwin.policy import replay as policy_replay
from experiments.psi_adwin.run import Models, _psi_call, champion_oos_wape
from src.model.leakage import shift_year_month
from src.stream.champion_policy import promotion_rule
from src.stream.metrics import _block, wape
from src.stream.studio_models import PSI_THRESHOLD, _proportions, psi_alarm, psi_score

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
OLD_2025_SERVED_WAPE = 0.04788460348726727


def psi_values(table: pd.DataFrame, month: str, reference_end: str, features: list[str]) -> dict[str, float]:
    """PSI of month t's feature rows vs the same calendar month in the two years up to reference_end."""
    start = shift_year_month(reference_end, 23)
    ref = table.loc[table["year_month"].between(start, reference_end) & (table["year_month"].str[-2:] == month[-2:])]
    cur = table.loc[table["year_month"] == month]
    out = {}
    for name in features:
        r = ref[name].to_numpy(dtype=float)
        edges = np.quantile(r, [0.25, 0.5, 0.75])
        out[name] = psi_score(_proportions(r, edges), _proportions(cur[name].to_numpy(dtype=float), edges))
    return out


def monthly_wape_series(part: pd.DataFrame, pred: np.ndarray) -> list[float]:
    y = part["guest_arrivals_total"].to_numpy(dtype=float)
    months = part["year_month"].to_numpy()
    return [wape(y[months == m], pred[months == m]) for m in sorted(set(months))]


class Server:
    """predict(key, month) for the policy loop: the Champion, single experts, and challengers."""

    def __init__(self, ctx: X.Context, served: dict[str, dict[str, np.ndarray]], variant: str):
        self.ctx = ctx
        self.models = Models(ctx.table, ctx.history)
        self.served = served
        self.variant = variant

    def __call__(self, key: str, month: str) -> np.ndarray:
        name = key.split("@", 1)[0]
        if name in self.served:
            return self.served[name][month]
        if name == "rf_logdiff_monthly":
            return self.models.predict(f"logdiff_expanding@{shift_year_month(month, 1)}", month)
        if name in ("expert_logdiff", "expert_level"):
            # Single experts are refit monthly on ..t-1 (same as inside the Champion).
            return X.monthly(self.ctx, name.split("_", 1)[1], self.variant, [month])
        return self.models.predict(key, month)


def month_action(st: dict) -> str:
    """The action label of one month of the policy loop (written to the replay log)."""
    kinds = [e["event"] for e in st["events"]]
    if st["trigger"]:
        action = "prepare_retrain"
    elif st["psi"]:
        action = "watch"
    elif st["adwin_cut"]:
        action = "alert" if st["adwin_alarm"] else "adwin_cut_ignored_down"
    else:
        action = "none"
    if st["adwin_cut"] and not st["adwin_alarm"] and not action.startswith("adwin_cut"):
        action += "+adwin_cut_ignored_down"
    for k in ("promote", "rollback"):
        if k in kinds:
            action += f"+{k}"
    return action


def run_track(ctx: X.Context, cfg: dict, months: list[str], served: dict, initial: str, trained_through: str,
              reference_end: str, seed: list[float], variant: str, delta: float | None = None,
              weights: dict | None = None, experts: dict | None = None, gate: bool = True,
              detector=None, strict_gate: bool = True) -> dict:
    drift = cfg["drift"]
    server = Server(ctx, served, variant)
    y, naive = {}, {}
    for m in months:
        part = X.part_of(ctx, [m]).sort_values("province_id")
        y[m] = part["guest_arrivals_total"].to_numpy(dtype=float)
        naive[m] = part["guest_lag_12"].to_numpy(dtype=float)
    psi_log = {}

    def psi(month, ref_end):
        values = psi_values(ctx.table, month, ref_end, drift["psi_features"])
        psi_log[month] = values
        alarms = [k for k, v in values.items() if psi_alarm(v)]
        return bool(alarms), alarms, "rain_lag_1" in alarms

    states = []
    gate_rows: dict[str, dict] = {}

    def before_month(month):
        # Input gate first: runs before month t is served or decided; a hard failure stops the loop.
        gate_rows[month] = _gate(ctx, month, strict_gate) if gate else {}

    result = policy_replay(
        months, y, naive, server, psi, tuple(drift["challengers"]), promotion_rule(drift["promotion_preset"]),
        trigger_mode=drift["challenger_trigger"], delta=float(delta if delta is not None else drift["adwin_delta"]),
        gate_months=int(drift["psi_gate_months"]), rollback_months=int(drift["rollback_months"]),
        refresh=bool(drift["refresh_reference_after_promotion"]), error_mode=drift["adwin_input"], seed=seed,
        initial_model=initial, trained_through=trained_through, reference_end=reference_end,
        log=states.append, adwin_direction=drift["adwin_direction"], detector=detector, before_month=before_month)
    rows = []
    for st in states:
        m = st["month"]
        dq = gate_rows[m]
        action = month_action(st)
        row = {"month": m, **{k: v for k, v in dq.items() if k != "as_of"},
               "served_model": st["served_model"], "served_wape": st["served"]["wape"], "served_mae": st["served"]["mae"],
               "served_rmse": st["served"]["rmse"], "served_bias": float(server(st["served_model"], m).sum() / y[m].sum() - 1),
               "naive_wape": wape(y[m], naive[m]),
               "adwin_window_len": st["adwin_window_len"], "adwin_mean": st["adwin_mean"], "adwin_cut": st["adwin_cut"],
               "adwin_alarm": st["adwin_alarm"],
               "adwin_mean_before": st["cut"]["mean_before"] if st["cut"] else None,
               "adwin_mean_after": st["cut"]["mean_after"] if st["cut"] else None,
               "adwin_direction": st["cut"]["direction"] if st["cut"] else None,
               **{f"psi_{k}": v for k, v in psi_log[m].items()},
               "psi_alarm": st["psi"], "psi_alarm_features": " ".join(st["psi_features"]), "rain_confirmed": st["rain_confirmed"],
               "trigger": st["trigger"], "action": action, "decision": st["decision"]}
        ch = [e for e in st["events"] if e["event"] == "challengers"]
        if ch:
            row["challenger_wapes"] = json.dumps({k: round(v["wape"], 6) for k, v in ch[0]["scores"].items()})
        if experts is not None:
            for name, pred_by_month in experts.items():
                row[f"wape_expert_{name}"] = wape(y[m], pred_by_month[m])
        if weights is not None and m in weights:
            row.update({f"w_{k}": v for k, v in weights[m].items()})
        rows.append(row)
    return {"result": result, "log": pd.DataFrame(rows), "y": y, "naive": naive, "server": server}


def _gate(ctx: X.Context, month: str, strict: bool = True) -> dict:
    """The gate result depends only on the context's panel and the month, so tracks on the same
    context share it. It is cached on the context object itself, never in a module-level dict."""
    key = (month, strict)
    if key not in ctx.gate_cache:
        ctx.gate_cache[key] = check_inputs(ctx.panel, ctx.table, month, strict=strict)
    return ctx.gate_cache[key]


def _by_month(part: pd.DataFrame, pred: np.ndarray) -> dict[str, np.ndarray]:
    part = part.assign(_p=pred).sort_values(["year_month", "province_id"])
    return {m: g["_p"].to_numpy(dtype=float) for m, g in part.groupby("year_month")}


def _champion_inputs(ctx: X.Context, sel: dict, year: int):
    from experiments.online_replay.run import champion
    part = X.part_of(ctx, X.months_of(year))
    pred, trace, ex = champion(ctx, sel, year)
    weights = {t["year_month"]: {k[2:]: v for k, v in t.items() if k.startswith("w_")} for t in trace}
    return part, pred, weights, {k: _by_month(part, v) for k, v in ex.items()}


def _summary(track: dict, label: str) -> dict:
    log, res = track["log"], track["result"]
    return {
        "label": label,
        "served": res["served"],
        "seed_length": res["seed_length"], "seed_cuts": res["seed_cuts"], "window_after_seed": res["window_after_seed"],
        "adwin_cut_months": log.loc[log["adwin_cut"], "month"].tolist(),
        "adwin_alarm_months": res["adwin_alarm_months"],
        "psi_alarm_months": log.loc[log["psi_alarm"], "month"].tolist(),
        "watch_months": log.loc[log["action"].str.startswith("watch"), "month"].tolist(),
        "prepare_retrain_months": log.loc[log["trigger"], "month"].tolist(),
        "promotions": [e for e in res["events"] if e["event"] == "promote"],
        "rollbacks": [e for e in res["events"] if e["event"] == "rollback"],
        "registry": res["registry"],
    }


def _explain(summary: dict) -> str:
    if summary["prepare_retrain_months"]:
        return f"prepare_retrain in {', '.join(summary['prepare_retrain_months'])}"
    parts = ["no prepare_retrain"]
    parts.append("no ADWIN cut" if not summary["adwin_cut_months"] else f"ADWIN cut(s) {summary['adwin_cut_months']} (alarms {summary['adwin_alarm_months']})")
    parts.append(f"PSI watches: {', '.join(summary['watch_months']) or 'none'}")
    return "; ".join(parts)


def reproduce_old_2025(ctx: X.Context) -> dict:
    """The refactored policy.replay with its defaults must give the approved 2025 result."""
    cfg = json.loads((HERE.parent / "psi_adwin" / "config.json").read_text(encoding="utf-8"))
    models = Models(ctx.table, ctx.history)
    months = X.months_of(2025)
    y, naive, by_month, rows = {}, {}, {}, []
    for m in pd.period_range("2023-01", "2025-12", freq="M").strftime("%Y-%m"):
        part = ctx.table.loc[ctx.table["year_month"] == m].sort_values("province_id")
        if m.startswith("2025"):
            by_month[m] = part
            y[m] = part["guest_arrivals_total"].to_numpy(dtype=float)
            naive[m] = part["guest_lag_12"].to_numpy(dtype=float)
        rows.append(part[["province_id", "year_month", "guest_lag_1", "guest_lag_12", "rain_lag_1", "rain_roll3"]])
    store = {"psi_frame": pd.concat(rows, ignore_index=True), "bins": {}, "by_month": by_month}
    seed = [float(r["wape"]) for r in champion_oos_wape(models, ctx.table, "2023-01", "2024-12")]
    res = policy_replay(months, y, naive, models.predict, lambda m, e: _psi_call(store, m, "2024-12"),
                        tuple(cfg["challengers"]), promotion_rule(cfg["promotion_preset"]),
                        trigger_mode=cfg["challenger_trigger"], delta=float(cfg["adwin_delta"]),
                        gate_months=int(cfg["psi_gate_months"]), rollback_months=int(cfg["rollback_months"]),
                        refresh=False, error_mode=cfg["adwin_input"], seed=seed)
    promos = sum(e["event"] == "promote" for e in res["events"])
    rollbacks = sum(e["event"] == "rollback" for e in res["events"])
    out = {"served_wape": res["served"]["wape"], "target": OLD_2025_SERVED_WAPE,
           "match": abs(res["served"]["wape"] - OLD_2025_SERVED_WAPE) < 1e-12, "promotions": promos,
           "rollbacks": rollbacks}
    if not out["match"] or promos or rollbacks:
        raise SystemExit(f"refactored policy.replay does not reproduce the approved 2025 result: {out}")
    return out


def stage_replay2025(ctx: X.Context) -> dict:
    cfg = S.load_config()
    sel = S.frozen_choice(cfg)
    old = reproduce_old_2025(ctx)
    print("old 2025 policy reproduced:", old, flush=True)
    seed = []
    for year in (2023, 2024):
        part, pred, _, _ = _champion_inputs(ctx, sel, year)
        seed += monthly_wape_series(part, pred)
    part, pred, weights, experts = _champion_inputs(ctx, sel, 2025)
    served = {"champion": _by_month(part, pred)}
    track = run_track(ctx, cfg, X.months_of(2025), served, "champion@2024-12", "2024-12", "2024-12", seed,
                      sel["covid_variant"], weights=weights, experts=experts)
    log = track["log"]
    log.to_csv(RESULTS / "replay_2025_log.csv", index=False)
    (RESULTS / "replay_2025_events.jsonl").write_text(
        "".join(json.dumps(e, default=float) + "\n" for e in track["result"]["events"]), encoding="utf-8")
    srows = []
    for m in X.months_of(2025):
        p = X.part_of(ctx, [m]).sort_values("province_id")
        r = log.loc[log["month"] == m].iloc[0]
        frame = pd.DataFrame({"province_id": p["province_id"].to_numpy(), "year_month": m, "y": track["y"][m],
                              "served": track["server"](r["served_model"], m), "naive": track["naive"][m],
                              **{f"expert_{k}": v[m] for k, v in experts.items()},
                              "model": r["served_model"], "decision": r["decision"]})
        srows.append(frame)
    pd.concat(srows).to_csv(RESULTS / "replay_2025_served.csv", index=False)
    summary = _summary(track, "2025 replay, frozen Champion (EWA) served")
    summary["seed"] = {"source": "Champion one-step OOS monthly WAPE 2023-01..2024-12 (EWA weights reset each January)",
                       "values": seed}
    summary["explanation"] = _explain(summary)
    summary["champion_2025_wape_offline"] = wape(part["guest_arrivals_total"].to_numpy(dtype=float), pred)
    summary["loop_matches_offline"] = abs(summary["served"]["wape"] - summary["champion_2025_wape_offline"]) * 100 <= 0.01
    summary["old_policy_reproduced"] = old
    summary["psi_threshold"] = PSI_THRESHOLD
    summary["adwin_note"] = ("The approved local ADWIN (delta 0.002, window >= 32, >= 16 points per side, monthly WAPE) "
                             "CANNOT fire on a shift that starts inside the replay year: a cut needs >= 16 post-shift "
                             "months and a mean shift of about 0.8 WAPE. Month 8 is only the first month the window is "
                             "long enough to be tested at all (24-month seed + 8), not a month in which in-year drift "
                             "can be detected. See drift_demos.py and README for the parameters that would fire.")
    (RESULTS / "replay_2025_summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n", encoding="utf-8")
    print("2025:", summary["explanation"], "served", summary["served"]["wape"], flush=True)
    return summary


def stage_demo2023(ctx: X.Context) -> dict:
    cfg = S.load_config()
    sel = S.frozen_choice(cfg)
    months = X.months_of(2023)
    models = Models(ctx.table, ctx.history)
    seed_a = [float(r["wape"]) for r in champion_oos_wape(models, ctx.table, "2021-01", "2022-12")]
    out, logs, events = {}, [], []
    # Track A: old Champion (plain RF logdiff, refit monthly).
    a = run_track(ctx, cfg, months, {}, "rf_logdiff_monthly@2022-12", "2022-12", "2022-12", seed_a, sel["covid_variant"])
    out["A"] = _summary(a, "Track A: plain RF logdiff (old Champion) served, refit monthly")
    out["A"]["seed"] = seed_a
    logs.append(a["log"].assign(track="A"))
    events += [{"track": "A", **e} for e in a["result"]["events"]]
    # Track B: the new EWA Champion.
    seed_b = []
    for year in (2021, 2022):
        part, pred, _, _ = _champion_inputs(ctx, sel, year)
        seed_b += monthly_wape_series(part, pred)
    part, pred, weights, experts = _champion_inputs(ctx, sel, 2023)
    b = run_track(ctx, cfg, months, {"champion": _by_month(part, pred)}, "champion@2022-12", "2022-12", "2022-12",
                  seed_b, sel["covid_variant"], weights=weights, experts=experts)
    out["B"] = _summary(b, "Track B: new EWA Champion served")
    out["B"]["seed"] = seed_b
    logs.append(b["log"].assign(track="B"))
    events += [{"track": "B", **e} for e in b["result"]["events"]]
    if not out["A"]["prepare_retrain_months"]:
        d = cfg["drift"]["demo_2023_sensitivity_delta"]
        s = run_track(ctx, cfg, months, {}, "rf_logdiff_monthly@2022-12", "2022-12", "2022-12", seed_a,
                      sel["covid_variant"], delta=d, gate=False)
        out["A_sensitivity"] = _summary(s, f"ILLUSTRATIVE ONLY: Track A with ADWIN delta={d} (not tuned, not a config)")
        logs.append(s["log"].assign(track=f"A_sensitivity_delta_{d}"))
        events += [{"track": "A_sensitivity", **e} for e in s["result"]["events"]]
    for k in out:
        out[k]["explanation"] = _explain(out[k])
        print("2023", k, out[k]["explanation"], "served", out[k]["served"]["wape"], flush=True)
    pd.concat(logs, ignore_index=True).to_csv(RESULTS / "replay_2023_demo_log.csv", index=False)
    (RESULTS / "replay_2023_demo_events.jsonl").write_text(
        "".join(json.dumps(e, default=float) + "\n" for e in events), encoding="utf-8")
    (RESULTS / "replay_2023_demo_summary.json").write_text(json.dumps(out, indent=2, default=float) + "\n", encoding="utf-8")
    return out
