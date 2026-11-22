"""Selection hygiene: choices are made on 2024 rows only and frozen before held-out scoring."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from experiments.online_replay import selection as S


def _part(year: int, n: int = 5) -> pd.DataFrame:
    months = [f"{year}-{m:02d}" for m in range(1, 13)]
    return pd.DataFrame({"year_month": np.repeat(months, n), "guest_arrivals_total": 100.0 + np.arange(12 * n)})


def _experts(part, skew=0.0):
    y = part["guest_arrivals_total"].to_numpy(dtype=float)
    return {"logdiff": y * (1.05 + skew), "level": y * 0.9}


def test_selection_refuses_non_2024_rows():
    for year in (2023, 2025):
        p = _part(year)
        with pytest.raises(S.SelectionError):
            S.select_eta(p, _experts(p), [1.0, 10.0])
        with pytest.raises(S.SelectionError):
            S.select_covid(p, {"C0": _experts(p)}, [1.0], 0.05)
        with pytest.raises(S.SelectionError):
            S.select_experts(p, _experts(p), _experts(p), [1.0], 0.02)
    mixed = pd.concat([_part(2024), _part(2025)])
    with pytest.raises(S.SelectionError):
        S.select_eta(mixed, _experts(mixed), [1.0])


def test_scorer_refuses_without_a_2024_stamp():
    with pytest.raises(S.SelectionError):
        S.frozen_choice({"selection": None})
    with pytest.raises(S.SelectionError):
        S.frozen_choice({"selection": {"covid_variant": "C0", "selected_on": 2025}})
    assert S.frozen_choice({"selection": {"covid_variant": "C0", "selected_on": 2024}})["covid_variant"] == "C0"


def test_near_tie_prefers_fewer_features_and_clear_win_does_not():
    p = _part(2024)
    y = p["guest_arrivals_total"].to_numpy(dtype=float)
    c0 = {"a": y * 1.0500, "b": y * 1.0500}
    c3_tie = {"a": y * 1.0497, "b": y * 1.0497}
    c3_win = {"a": y * 1.02, "b": y * 1.02}
    assert S.select_covid(p, {"C0": c0, "C3": c3_tie}, [10.0], 0.05)["chosen"] == "C0"
    assert S.select_covid(p, {"C0": c0, "C3": c3_win}, [10.0], 0.05)["chosen"] == "C3"


def test_third_expert_needs_a_real_gain():
    p = _part(2024)
    y = p["guest_arrivals_total"].to_numpy(dtype=float)
    e2 = {"a": y * 1.05, "b": y * 1.10}
    assert S.select_experts(p, e2, {**e2, "c": y * 1.5}, [10.0], 0.02)["chosen"] == "E2"
    assert S.select_experts(p, e2, {**e2, "c": y.copy()}, [10.0], 0.02)["chosen"] == "E3"


def test_freeze_writes_the_stamp(tmp_path):
    path = tmp_path / "config.json"
    cfg = S.freeze({"etas": [1.0]}, {"covid_variant": "C0"}, path)
    assert S.frozen_choice(S.load_config(path))["selected_on"] == 2024
    assert cfg["selection"]["covid_variant"] == "C0"


def test_readme_headline_numbers_equal_results_csv():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "experiments" / "online_replay"
    res = pd.read_csv(root / "results" / "results.csv")
    text = (root / "README.md").read_text(encoding="utf-8")
    block = text.split("<!-- headline:start -->")[1].split("<!-- headline:end -->")[0]
    lines = [l for l in block.splitlines() if l.startswith("|") and "---" not in l][1:]
    names = ["seasonal_naive", "old_champion_rf_logdiff", "ewa_old_logdiff_level_eta10", "champion_baseline_C0_E2_eta10"]
    final = [m for m in res["model"].unique() if m.startswith("final_champion") and not m.endswith("no_rain")][0]
    names += [final, final + "_no_rain"]
    assert len(lines) == len(names)
    for line, name in zip(lines, names):
        cells = [c.strip().strip("*") for c in line.strip("|").split("|")][1:]
        got = [float(c) for c in cells]
        want = [float(res[(res.model == name) & (res.year == y)]["wape_pct"].iloc[0]) for y in (2023, 2024, 2025)]
        assert got == want, (name, got, want)
