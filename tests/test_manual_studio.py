"""Exercise the real manual training endpoint without advancing the 2024 session."""
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from experiments.online_replay.manual_studio import (
    Handler, Studio, apply_champion, monitor, month_rain, needs_retrain, PH_THRESHOLD,
)
from experiments.online_replay.selection import load_config


def test_retrain_is_allowed_only_after_a_drift_alert():
    assert needs_retrain({"ph_alarm": False, "action": "watch", "psi_watch": True}) is False
    assert needs_retrain({"ph_alarm": False, "action": "none"}) is False
    assert needs_retrain({"ph_alarm": True, "action": "prepare_retrain"}) is True
    assert needs_retrain({"ph_alarm": False, "action": "prepare_retrain+promote"}) is True
    assert needs_retrain({"ph_alarm": True, "action": "prepare_retrain", "retrained": True}) is False


def test_month_rain_follows_province_order():
    panel = pd.DataFrame({
        "year_month": ["2024-01", "2024-01", "2024-02"],
        "province_th": ["ก", "ข", "ก"],
        "rain_avg_mm": [10.0, 30.5, 99.0],
    })
    assert month_rain(panel, "2024-01", ["ข", "ก"]) == [30.5, 10.0]


def test_retrain_switches_to_random_forest_when_its_wape_is_lower():
    history = [
        {"month": "2024-10", "retrained": True, "metrics": {"linear": {"wape": 0.09}, "random_forest": {"wape": 0.08}}},
        {"month": "2024-11", "retrained": False, "metrics": {"linear": {"wape": 0.04}, "random_forest": {"wape": 0.07}}},
    ]
    apply_champion(history, "2023-12")
    assert history[0]["switched"] is True and history[0]["served"] == "random_forest"
    assert history[0]["registry"][-1]["effective_from"] == "2024-10"
    assert history[1]["served"] == "random_forest"


def test_retrain_keeps_the_served_model_when_random_forest_is_not_lower():
    history = [{"month": "2024-10", "retrained": True, "metrics": {"linear": {"wape": 0.05}, "random_forest": {"wape": 0.05}}}]
    apply_champion(history, "2023-12")
    assert history[0]["switched"] is False and history[0]["served"] == "linear"
    assert history[0]["registry"] == [{
        "id": "v001", "model": "linear", "effective_from": "2024-01", "rolled_back": False,
    }]


def test_retrain_rejects_a_watch_without_drift(tmp_path):
    app = Studio(tmp_path / "qa.sqlite", recipe="level")
    history = [{"month": "2024-01", "ph_alarm": False, "action": "watch", "psi_watch": True, "retrained": False}]
    with sqlite3.connect(app.db) as conn:
        conn.execute("UPDATE session SET revision=1, history=? WHERE id=1", (json.dumps(history),))
    with pytest.raises(ValueError, match="Retrain"):
        app.change("retrain", 1)


def test_ph_can_promote_without_psi_and_preserves_legacy_decisions():
    seed = [.05, .06] * 6
    history = []
    for month in ("2024-01", "2024-02", "2024-03"):
        history.append(dict(month=month, psi={}, actual=[100.], naive=[90.],
                            predictions={"champion": [65.], "rf_logdiff": [98.], "yoy3": [95.]}))
        result = monitor(history, seed, load_config()["drift"], "2023-12")
        history[-1].update(result)
    assert history[0]["ph_score"] > PH_THRESHOLD and not history[0]["psi_watch"]
    assert history[0]["action"] == "prepare_retrain"
    assert history[1]["decision"] == "PROMOTE" and history[2]["served"] == "rf_logdiff"
    history[0].update(adwin=False, action="none")
    result = monitor(history[:2], seed, load_config()["drift"], "2023-12")
    assert result["ph_alarm"] and result["decision"] == "KEEP"


@pytest.mark.slow
def test_step_resume_and_reset_before_2024(tmp_path):
    app = Studio(tmp_path / "qa.sqlite", start="2022-12", stop="2023-02")
    app.initialize()
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.studio = app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def call(action, revision=None):
            body = None if revision is None else json.dumps({"revision": revision}).encode()
            req = urllib.request.Request(base + "/api/" + action, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.load(response)

        try:
            assert call("state")["history"] == []
            first = call("train", 0)
            assert first["through"] == "2023-01"
            record = first["history"][0]
            assert len(record["actual"]) == 77
            assert set(record["predictions"]) == {"champion", "rf_logdiff", "yoy3"}
            with pytest.raises(urllib.error.HTTPError) as error:
                call("train", 0)
            assert error.value.code == 409
            second = call("train", 1)
            assert second["through"] == "2023-02" and second["next"] is None
            assert Studio(app.db, app.start, app.stop).read() == second
            assert call("reset", 2)["history"] == []
            repeated = call("train", 3)["history"][0]
            for key in record["predictions"]:
                np.testing.assert_allclose(repeated["predictions"][key], record["predictions"][key])
        finally:
            server.shutdown()
            thread.join()
