"""Fresh 2019–2023 Champion and continuous monthly 2024–2025 replay.

Run from the checkout: python -m experiments.online_replay.train_stream
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from experiments.online_replay import experts as X
from experiments.online_replay import replay as R
from experiments.online_replay.selection import frozen_choice, load_config
from experiments.online_trials.models import ewa
from experiments.psi_adwin.policy import _challenger_key
from src.model.leakage import shift_year_month
from src.stream.metrics import _block


OUT = Path(__file__).resolve().parent / "results" / "stream_2024_2025"


def main():
    cfg = load_config()
    selection = frozen_choice(cfg)
    variant, eta = selection["covid_variant"], selection["eta"]
    assert (variant, selection["experts"]) == ("C0", "E2")
    ctx = X.Context.build()  # Empty in-memory cache. No stored predictions or models.
    months = X.months_of(2024) + X.months_of(2025)
    keys = ["logdiff", "level"]
    baseline = ctx.table.loc[ctx.table.year_month.between("2019-01", "2023-12")]
    assert len(baseline) == 60 * 77 and not baseline.duplicated(["province_id", "year_month"]).any()
    jan = baseline.loc[baseline.year_month == "2019-01"].set_index("province_id").sort_index()
    for lag, source in [(1, "2018-12"), (12, "2018-01")]:
        hist = ctx.panel.loc[ctx.panel.year_month == source].set_index("province_id").sort_index()
        pd.testing.assert_index_equal(jan.index, hist.index)
        np.testing.assert_array_equal(jan[f"guest_lag_{lag}"], hist.guest_arrivals_total)
    OUT.mkdir(parents=True, exist_ok=True)
    seed = []
    for year in (2022, 2023):
        part, pred, _, _ = R._champion_inputs(ctx, selection, year)
        seed.extend(R.monthly_wape_series(part, pred))
        print(f"Detector history through {year}-12", flush=True)

    experts = {key: [] for key in keys}
    for month in months:
        R._gate(ctx, month)
        end = shift_year_month(month, 1)
        for key in keys:
            if month == months[0]:
                pred, _ = X.fit_predict(ctx, key, end, [month], checkpoint=OUT / f"{key}_{end}.joblib")
            else:
                pred = X.monthly(ctx, key, variant, [month])
            experts[key].append(pred)
        print(f"Forecast {month} from observations through {end}", flush=True)

    part = X.part_of(ctx, months)
    raw = {key: np.concatenate(values) for key, values in experts.items()}
    pred, trace = ewa(part, raw, eta)  # One EWA stream across both years.
    weights = {t["year_month"]: {k: t[f"w_{k}"] for k in keys} for t in trace}
    by_month = {key: R._by_month(part, values) for key, values in raw.items()}
    track = R.run_track(ctx, cfg, months, {"champion": R._by_month(part, pred)},
                        "champion@2023-12", "2023-12", "2023-12", seed, variant,
                        weights=weights, experts=by_month)
    # Exercise every standby predictor at the initial cutoff, independently of promotion.
    first = months[0]
    standby = X.part_of(ctx, [first]).sort_values("province_id")[["year_month", "province_id"]].copy()
    for name in cfg["drift"]["challengers"]:
        standby[name] = track["server"](_challenger_key(name, first), first)
    if not np.isfinite(standby[cfg["drift"]["challengers"]]).all().all():
        raise ValueError("Non-finite standby forecasts")
    standby.to_csv(OUT / "challenger_readiness.csv", index=False)
    log = track["log"]
    frames = []
    for row in log.itertuples():
        m = row.month
        actual = track["y"][m]
        if len(actual) != 77:
            raise ValueError(f"Expected 77 provinces for {m}")
        frames.append(pd.DataFrame({
            "year_month": m, "province_id": X.part_of(ctx, [m]).sort_values("province_id").province_id.to_numpy(),
            "actual": actual, "served": track["server"](row.served_model, m), "model": row.served_model,
            "naive": track["naive"][m], **{k: by_month[k][m] for k in keys}}))
    forecasts = pd.concat(frames, ignore_index=True)
    assert len(forecasts) == 1848 and np.isfinite(forecasts[["actual","served","naive"]]).all().all()
    forecasts.to_csv(OUT / "forecasts.csv", index=False)
    log.assign(champion_fit_through=[shift_year_month(m, 1) for m in months]).to_csv(OUT / "monthly.csv", index=False)
    summary = R._summary(track, "Continuous 2024-2025 replay")
    summary.update(sklearn=sklearn.__version__, panel_rows_initial=len(baseline),
                   historical_selection_year=selection["selected_on"], eta=eta,
                   challengers=cfg["drift"]["challengers"], yearly={})
    for year, g in forecasts.groupby(forecasts.year_month.str[:4]):
        summary["yearly"][year] = _block(g.actual.to_numpy(), g.served.to_numpy(), g.naive.to_numpy())
        summary["yearly"][year]["naive_wape"] = float(abs(g.actual-g.naive).sum()/g.actual.sum())
    last = months[-1]
    next_weights = np.array([weights[last][key] for key in keys])
    for i, key in enumerate(keys):
        loss = abs(track["y"][last]-by_month[key][last]).sum()/track["y"][last].sum()
        next_weights[i] *= np.exp(-eta*loss)
        X.fit_predict(ctx, key, last, [], checkpoint=OUT / f"{key}_{last}.joblib")
    summary["weights_after_last_update"] = dict(zip(keys, next_weights / next_weights.sum()))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=float), encoding="utf-8")
    (OUT / "events.jsonl").write_text("".join(json.dumps(e, default=float)+"\n" for e in track["result"]["events"]),
                                      encoding="utf-8")
    print(summary["yearly"], flush=True)


if __name__ == "__main__":
    main()
