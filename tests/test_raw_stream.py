"""Acceptance checks for the raw feeder and the deployed model boundary."""
import json
from pathlib import Path

import pytest

from src.common.paths import ROOT
from src.stream.raw_2025 import source_files
from src.stream.studio import Studio
from src.stream.validate import StreamHalt


def test_raw_batches_are_atomic_and_use_the_deployed_version(tmp_path, monkeypatch):
    if any(not (ROOT / item["path"]).exists() for item in source_files()):
        pytest.skip("Original MOTS/HII files are unavailable")
    studio = Studio(tmp_path, raw=True)
    state = studio.apply("train", {})
    assert state["source"]["rows"] == 77 * 84
    january = studio.apply("step", {"month": "2025-01"})
    assert studio.apply("step", {"month": "2025-01"}) == january
    result = january["months"][0]
    assert len(result["batch"]) == len(result["served_forecast"]) == 77
    assert list(result["pipeline"]) == ["features", "predict", "monitor", "retrain", "compare", "deploy"]
    assert len(result["rows"]) == 77 and not result["competition"]["scores"]
    deploy = january["deployment"]
    feb = studio.apply("step", {"month": "2025-02"})["months"][-1]
    if result["competition"]["decision"] == "PROMOTE":
        assert deploy["effective_from"] == feb["month"]
    else:
        assert deploy["effective_from"] == "2025-01"
    assert {r["version"] for r in feb["served_forecast"]} == {deploy["version"]}
    assert [r["y_pred"] for r in feb["served_forecast"]] == [r["y_pred"] for r in feb["rows"]]
    contest = feb["competition"]
    assert feb["psi"]["rain_lag_1"]["pending_confirmation"]
    assert not feb["psi"]["rain_lag_1"]["alarm"]
    assert contest["trigger"] == ["rain_roll3"]
    assert contest["fit_end"] == "2025-01" and contest["evaluation_month"] == "2025-02"
    assert len(contest["scores"]) == 3
    if contest["decision"] == "PROMOTE":
        assert feb["pipeline"]["deploy"]["effective_from"] == "2025-03"
    else:
        assert feb["pipeline"]["deploy"]["effective_from"] == deploy["effective_from"]
    assert (contest["decision"] == "PROMOTE") == (
        min(item["wape"] for item in contest["scores"].values()) < feb["metrics"]["champion"]["wape"] - 1e-12)
    assert {e["stage"] for e in studio.progress["events"]} == {"FEATURES", "PREDICT", "MONITOR", "RETRAIN", "COMPARE", "DEPLOY", "DONE"}

    def missing(month):
        raise StreamHalt("Raw file unavailable", year_month=month)
    monkeypatch.setattr("src.stream.raw_2025.read_month", missing)
    with pytest.raises(StreamHalt, match="unavailable"):
        studio.apply("step", {"month": "2025-03"})
    persisted = Studio(tmp_path, raw=True).state()
    assert len(persisted["months"]) == 2
    assert persisted["halt"]["month"] == "2025-03"
    assert json.loads((Path(tmp_path) / "dq/2025-03.json").read_text())["hard_fail"]
