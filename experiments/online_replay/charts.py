"""Slide charts (1600x900) from the CSVs in results/. Run after the other stages."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import matplotlib
except ImportError:  # plan: fall back to the data-review pylib only if matplotlib is missing
    sys.path.append("/workspace/data_review/pylib")
    import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
R = HERE / "results"
C = HERE / "charts"
YEARS = (2023, 2024, 2025)


def _fig(nrows=1, ncols=1, **kw):
    return plt.subplots(nrows, ncols, figsize=(16, 9), dpi=100, **kw)


def _save(fig, name, title, takeaway):
    fig.suptitle(title, fontsize=18, fontweight="bold", y=0.985)
    fig.text(0.5, 0.01, takeaway, ha="center", fontsize=13, style="italic")
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    C.mkdir(exist_ok=True)
    fig.savefig(C / name)
    plt.close(fig)


def _bars(ax, labels, series: dict[str, list[float]], colors=None):
    x = np.arange(len(labels))
    w = 0.8 / len(series)
    for i, (name, vals) in enumerate(series.items()):
        b = ax.bar(x + i * w - 0.4 + w / 2, vals, w, label=name, color=None if colors is None else colors[i])
        for rect, v in zip(b, vals):
            ax.text(rect.get_x() + rect.get_width() / 2, min(v, ax.get_ylim()[1] if ax.get_autoscaley_on() is False else v) + 0.2,
                    f"{v:.2f}", ha="center", fontsize=10)
    ax.set_xticks(x, labels, fontsize=13)
    ax.set_ylabel("monthly WAPE %", fontsize=13)
    ax.legend(fontsize=11)


def headline():
    res = pd.read_csv(R / "results.csv")
    piv = res.pivot_table(index="model", columns="year", values="wape_pct")
    final = [m for m in piv.index if m.startswith("final_champion") and not m.endswith("no_rain")][0]
    order = [("seasonal_naive", "Seasonal naive"), ("old_champion_rf_logdiff", "Old Champion (RF logdiff)"),
             ("ewa_old_logdiff_level_eta10", "Old EWA (logdiff+level)"), ("champion_baseline_C0_E2_eta10", "Champion baseline (6.50/5.59/4.54)"),
             (final, "Final Champion")]
    fig, ax = _fig()
    _bars(ax, [str(y) for y in YEARS], {lab: [piv.loc[m, y] for y in YEARS] for m, lab in order})
    _save(fig, "headline_wape.png", "Monthly WAPE by year: Champion vs old Champion vs seasonal naive",
          f"Final Champion: {', '.join(f'{y} {piv.loc[final, y]:.2f}%' for y in YEARS)} (2024 = selection year; 2023/2025 held out)")


def covid():
    df = pd.read_csv(R / "covid_ab.csv")
    fig, ax = _fig()
    _bars(ax, [str(y) for y in YEARS], {f"{v} ({'selected' if df[df.variant == v].selected.any() else 'candidate' if df[df.variant == v].candidate.any() else 'report only'})":
                                       [df[(df.variant == v) & (df.year == y)].wape.iloc[0] * 100 for y in YEARS] for v in df.variant.unique()})
    _save(fig, "covid_ab.png", "COVID information A/B (EWA Champion, eta chosen on 2024)",
          "C0 = binary COVID flags; C3 = recovery level vs last two normal years + lagged event flag; C4 = no COVID info")


def ewa_ab():
    df = pd.read_csv(R / "ewa_ab.csv")
    fig, ax = _fig()
    _bars(ax, [str(y) for y in YEARS], {f"{v} ({df[df.variant == v].experts.iloc[0]}){' selected' if df[df.variant == v].selected.any() else ''}":
                                       [df[(df.variant == v) & (df.year == y)].wape.iloc[0] * 100 for y in YEARS] for v in ("E2", "E3")})
    _save(fig, "ewa_ab.png", "EWA with 2 experts vs 3 (third = no-ML seasonal last-month forecast)",
          "E3 is kept only if it beats E2 on 2024 by >= 0.02 pt")


def weights():
    w = pd.read_csv(R / "ewa_weights.csv")
    sel = json.loads((HERE / "config.json").read_text())["selection"]
    for y in YEARS:
        d = w[(w.year == y) & (w.variant == sel["experts"])]
        fig, ax = _fig()
        cols = [c for c in ("w_logdiff", "w_level", "w_seasonal") if d[c].abs().sum() > 0]
        ax.stackplot(range(len(d)), *[d[c] for c in cols], labels=[c[2:] for c in cols], alpha=0.85)
        ax.set_xticks(range(len(d)), d.month, rotation=45, fontsize=11)
        ax.set_ylim(0, 1)
        ax.set_ylabel("EWA weight", fontsize=13)
        ax.legend(loc="upper right", fontsize=12)
        last = d.iloc[-1]
        _save(fig, f"ewa_weights_{y}.png", f"EWA expert weights by month, {y} ({sel['experts']}, eta {sel['eta']:g}, reset each January)",
              "Weights for month t use only losses of months before t. December: " + ", ".join(f"{c[2:]} {last[c]:.2f}" for c in cols))


def _drift_panels(axes, log, label):
    m = log["month"].tolist()
    x = np.arange(len(m))
    ax = axes[0]
    ax.plot(x, log.served_wape * 100, "o-", label=f"served ({label})", lw=2)
    ax.plot(x, log.naive_wape * 100, "s--", label="seasonal naive", color="grey")
    ax.plot(x, log.adwin_mean * 100, ":", label="ADWIN window mean", color="purple", lw=2)
    ax.set_ylabel("monthly WAPE %")
    ax.legend(fontsize=10)
    ax = axes[1]
    for c in [c for c in log.columns if c.startswith("psi_") and c not in ("psi_alarm", "psi_alarm_features")]:
        ax.plot(x, log[c], "o-", label=c[4:])
    from src.stream.studio_models import PSI_THRESHOLD
    ax.axhline(PSI_THRESHOLD, color="red", ls="--", label=f"PSI alarm {PSI_THRESHOLD}")
    ax.axhline(0.2, color="orange", ls=":", label="0.2 (common rule of thumb)")
    ax.set_yscale("symlog", linthresh=0.1)
    ax.set_ylabel("PSI vs Seasonal-2Y")
    ax.legend(fontsize=9, ncol=3)
    ax = axes[2]
    kinds = {"watch": ("PSI watch", "gold", "^"), "alert": ("ADWIN alert", "orange", "v"), "prepare_retrain": ("prepare_retrain", "red", "*"),
             "promote": ("promote", "green", "P"), "rollback": ("rollback", "black", "X"), "adwin_cut_ignored_down": ("ADWIN cut (down, ignored)", "blue", "x")}
    flags = {
        "watch": log.psi_alarm.astype(bool) & ~log.trigger.astype(bool),
        "alert": log.adwin_alarm.astype(bool) & ~log.trigger.astype(bool),
        "prepare_retrain": log.trigger.astype(bool),
        "promote": log.action.str.contains("promote"),
        "rollback": log.action.str.contains("rollback"),
        "adwin_cut_ignored_down": log.adwin_cut.astype(bool) & ~log.adwin_alarm.astype(bool),
    }
    for k, (lab, col, mk) in kinds.items():
        hit = np.flatnonzero(flags[k].to_numpy())
        ax.scatter(hit, [list(kinds).index(k)] * len(hit), s=250, c=col, marker=mk, label=lab)
    ax.set_yticks(range(len(kinds)), [v[0] for v in kinds.values()])
    ax.set_xticks(x, m, rotation=45)
    ax.set_xlim(-0.5, len(m) - 0.5)
    for a in axes[:2]:
        a.set_xticks(x, [])
        a.set_xlim(-0.5, len(m) - 0.5)


def drift():
    log = pd.read_csv(R / "replay_2025_log.csv")
    s = json.loads((R / "replay_2025_summary.json").read_text())
    fig, axes = _fig(3, 1, gridspec_kw={"height_ratios": [3, 3, 2]})
    _drift_panels(axes, log, "Champion")
    _save(fig, "replay_2025_drift.png", "2025 replay: error, feature drift (PSI) and actions month by month", "2025: " + s["explanation"])
    demo = pd.read_csv(R / "replay_2023_demo_log.csv")
    d = json.loads((R / "replay_2023_demo_summary.json").read_text())
    tracks = list(dict.fromkeys(demo.track))
    fig, axes = plt.subplots(3, len(tracks), figsize=(16, 9), dpi=100, gridspec_kw={"height_ratios": [3, 3, 2]}, squeeze=False)
    for j, t in enumerate(tracks):
        _drift_panels(axes[:, j], demo[demo.track == t].reset_index(drop=True), t)
        key = "A_sensitivity" if t.startswith("A_sens") else t
        short = {"A": "Track A: old RF logdiff", "B": "Track B: new EWA Champion"}.get(key, "Track A, delta 0.05 (ILLUSTRATIVE)")
        cut = d[key]["adwin_cut_months"]
        axes[0, j].set_title(f"{short}\nprepare_retrain: {', '.join(d[key]['prepare_retrain_months']) or 'none'}; "
                             f"ADWIN cuts: {', '.join(cut) or 'none'}; PSI watches: {len(d[key]['watch_months'])}/12", fontsize=11)
    _save(fig, "replay_2023_drift.png", "2023 drift demo (reference = COVID-era 2021-2022)",
          "Track A: old RF logdiff; Track B: new EWA Champion" + ("; sensitivity panel is illustrative only" if len(tracks) > 2 else ""))


def rain():
    abl = pd.read_csv(R / "rain_ablation.csv")
    eff = pd.read_csv(R / "rain_effect.csv")
    fig, axes = _fig(1, 2)
    _bars(axes[0], [str(y) for y in abl.year], {"Champion (with lagged rain)": abl.champion_wape_pct.tolist(), "without rain": abl.no_rain_wape_pct.tolist()})
    axes[0].set_title("Drop-rain ablation (monthly WAPE %)", fontsize=14)
    q = eff[eff.stat.str.startswith("mean_yoy_residual_log_q")]
    axes[1].bar(range(len(q)), q.value * 100, color=["#c8a", "#c8a", "#999", "#8ac", "#36c"])
    axes[1].set_xticks(range(len(q)), [f"Q{i + 1}" for i in range(len(q))], fontsize=13)
    axes[1].set_ylabel("mean YoY growth residual (%, log)")
    axes[1].set_title("Same-month rain anomaly quintile (Q5 = wettest), descriptive only", fontsize=14)
    d = eff.drop_duplicates("stat").set_index("stat").value
    d["delta_r2"] = eff[(eff.stat == "delta_r2") & eff.spec.str.startswith("lagged rain (log")].value.iloc[0]
    _save(fig, "rain_effect.png", "Rain has little effect on the forecast (kept for the project framing)",
          f"Lagged-rain incremental R2 = {d['delta_r2']:.4f}; wettest vs driest quintile {d['wettest_vs_driest_quintile_pct']:.1f}% arrivals; "
          f"drop-rain diff {', '.join(f'{y}: {v:+.2f} pt' for y, v in zip(abl.year, abl.diff_pt_no_rain_minus_with))}")


def dq():
    r = json.loads((R / "dq_report.json").read_text())
    a = r["accuracy"]
    labels = ["legacy v1 (all rows)", "v2 normal", "v2 normal (total+rain only)", "v2 COVID months", "v2 COVID-base months"]
    vals = [a["accuracy_legacy_v1"], a["accuracy_normal"], a["accuracy_normal_total_and_rain_only"], a["accuracy_covid"], a["accuracy_covid_base"]]
    fig, ax = _fig()
    b = ax.bar(labels, vals, color=["#999", "#2a7", "#7c9", "#c44", "#e96"])
    for rect, v in zip(b, vals):
        ax.text(rect.get_x() + rect.get_width() / 2, v + 0.01, f"{v:.3f}", ha="center", fontsize=13)
    ax.axhline(0.90, ls="--", color="red", label="warning floor 0.90")
    ax.set_ylim(0, 1.05)
    ax.legend()
    _save(fig, "dq_accuracy_buckets.png", "DQ accuracy: seasonal rain rule + COVID scored separately",
          f"overall DQ score {r['legacy_v1_scores']['overall']:.3f} (v1) -> {r['scores']['overall']:.3f} (v2); COVID buckets reported, not scored")


def main():
    for fn in (headline, covid, ewa_ab, weights, drift, rain, dq):
        fn()
        print("chart", fn.__name__, flush=True)


if __name__ == "__main__":
    main()
