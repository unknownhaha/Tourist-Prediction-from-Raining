"""Shadow comparison on Champion forecasts; never trains models or changes studio state."""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from src.model.leakage import build_forecast_features
from src.stream.drift import PageHinkley
from src.stream.studio_models import _proportions, psi_score


HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "detector_comparison"
FEATURES = ["guest_lag_1", "guest_lag_12", "rain_lag_1", "rain_roll3"]
METHODS = ["EWMA", "Page-Hinkley", "PSI-3", "PSI-6"]
ONSET = 6  # six normal months before the known change


def compare():
    historical = pd.read_csv(HERE / "results/monthly_wape.csv")
    seed = historical.loc[(historical.model == "final_champion_C0_E2_eta5")
                          & historical.year_month.str.startswith("2023")].sort_values("year_month")
    online = pd.read_csv(HERE / "results/stream_2024_2025/monthly.csv").sort_values("month")
    dates = seed.year_month.tolist() + online.month.tolist()
    assert dates == pd.period_range("2023-01", "2025-12", freq="M").astype(str).tolist()
    panel = pd.read_parquet(HERE.parents[1] / "data/model/province_month_unified.parquet")
    features = np.stack([build_forecast_features(panel, m).sort_values("province_id")[FEATURES]
                         .to_numpy(dtype=float) for m in dates])
    assert features.shape == (36, 77, 4) and np.isfinite(features).all()
    # Identical province order, new codes for a controlled shift in the two rain inputs only.
    perturbed = features[:12].copy()
    perturbed[:, :, 2:] *= 1.5
    features = np.concatenate([features, perturbed])
    errors = np.r_[seed.wape, online.served_wape]
    mu, sigma = errors[:12].mean(), errors[:12].std(ddof=1)

    @lru_cache(maxsize=None)
    def rolling_psi(codes):
        past = features[list(codes[:-1])].reshape(-1, len(FEATURES))
        now = features[codes[-1]]
        edges = np.quantile(past, [.25, .5, .75], axis=0)
        return tuple(psi_score(_proportions(past[:, i], edges[:, i]), _proportions(now[:, i], edges[:, i]))
                     for i in range(len(FEATURES)))

    def trace(codes, error_shift=0., rain_shift=False):
        history = list(range(6, 12))
        ewma = 0.
        ph = PageHinkley(delta=.5, threshold=np.inf, initial_count=12)
        output = []
        for i, code in enumerate(codes):
            z = (errors[code] + (error_shift if i >= ONSET else 0.) - mu) / sigma
            ewma = .3 * z + .7 * ewma
            ph.update(z)
            history.append(int(code) + (36 if rain_shift and i >= ONSET else 0))
            psi = [rolling_psi(tuple(history[-(w+1):])) for w in (3, 6)]
            output.append([ewma, ph.statistic, max(psi[0]), max(psi[1]), *psi[0], *psi[1]])
        return np.array(output)

    def simulations(random_seed):
        rng = np.random.default_rng(random_seed)
        # Paired WAPE and 77-province feature panels travel together in circular 3-month blocks.
        starts = rng.integers(12, size=(400, 8, 1))
        return ((starts + np.arange(3)) % 12).reshape(400, 24)

    calibrate = np.array([trace(x)[:, :4].max(axis=0) for x in simulations(42)])
    thresholds = np.quantile(calibrate, .95, axis=0, method="higher")
    cases = simulations(43)
    rows = []
    for case, shift, rain in [("control", 0., False), ("wape+3pp", .03, False),
                              ("wape+5pp", .05, False), ("rain+50%", 0., True),
                              ("both", .05, True)]:
        flags = np.array([trace(x, shift, rain)[:, :4] > thresholds for x in cases])
        for i, name in enumerate(METHODS):
            alarms = flags[:, :, i]
            eligible = ~alarms[:, :ONSET].any(axis=1)
            post = alarms[eligible, ONSET:]
            hit = post.any(axis=1)
            delays = post[hit].argmax(axis=1) + 1
            rows.append(dict(scenario=case, method=name, threshold=thresholds[i], runs=len(cases),
                             any_alarm_pct=100*alarms.any(axis=1).mean(), eligible=int(eligible.sum()),
                             within_3_pct=100*post[:, :3].any(axis=1).mean(),
                             within_6_pct=100*post[:, :6].any(axis=1).mean(),
                             within_18_pct=100*hit.mean(),
                             median_month_if_detected=np.median(delays) if hit.any() else np.nan))
    OUT.mkdir(exist_ok=True, parents=True)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(OUT / "simulation.csv", index=False)
    real = trace(range(12, 36))
    monthly = pd.DataFrame({"month": dates[12:], "served_wape": errors[12:]})
    for i, name in enumerate(METHODS):
        monthly[name] = real[:, i]
        monthly[name+"_alarm"] = real[:, i] > thresholds[i]
    for i, window in enumerate((3, 6)):
        monthly[f"PSI-{window}_watch_at_0.15"] = real[:, 2+i] >= .15
        for j, name in enumerate(FEATURES):
            monthly[f"psi{window}_{name}"] = real[:, 4+4*i+j]
    monthly.to_csv(OUT / "monthly.csv", index=False)
    print(metrics.round(2).to_string(index=False))
    for name in METHODS:
        print(name, monthly.loc[monthly[name+"_alarm"], "month"].tolist())


if __name__ == "__main__":
    compare()
