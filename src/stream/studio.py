"""Transactional, one-month-at-a-time training studio for the 2025 replay."""
import io
import json
import math
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import joblib
import pandas as pd

from src.common.paths import DATA, ROOT
from src.stream.champion_policy import ChampionPolicy
from src.stream.lineage import sha256_file, sha256_frame, write_json
from src.stream.metrics import _block, summarize
from src.stream.reference import REFERENCE_VERSION, build_reference
from src.stream.replay import prepare_month, _load_staging_rain, _record_halt, _write_lineage
from src.stream.studio_models import prepare
from src.stream.validate import StreamHalt, validate_history

MONTHS = [f"2025-{n:02}" for n in range(1, 13)]
DEFAULT_DIR = ROOT / "outputs" / "studio_2025"
SNAPSHOT = DATA / "model" / "province_month_unified.parquet"
MODELS = ("rf_fixed", "rf_monthly", "linear")


def jsonable(value):
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class Studio:
    def __init__(self, output=DEFAULT_DIR, snapshot=SNAPSHOT, baseline_path=None, raw=False, triggered=None):
        self.output = Path(output)
        self.output.mkdir(exist_ok=True, parents=True)
        self.snapshot = Path(snapshot)
        self.raw = raw
        self.triggered = raw if triggered is None else triggered
        self.progress = {"month": None, "stage": "IDLE", "events": []}
        self.baseline_path = baseline_path or self.output / "baseline_test_2024.json"
        self.db = self.output / "studio.sqlite"
        with closing(self.connect()) as db:
            db.execute("CREATE TABLE IF NOT EXISTS run (id INTEGER PRIMARY KEY, state TEXT NOT NULL, models BLOB NOT NULL)")
            db.commit()

    def connect(self):
        return sqlite3.connect(self.db, timeout=180)

    def empty(self):
        return dict(triggered=self.triggered, rain_lag_confirmation_months=2 if self.triggered else None,
                    trained=False, months=[], baseline={}, champion=None, registry=[],
                    source={"file": "MOTS XLSX + HII CSV" if self.raw else self.snapshot.name,
                            "raw": self.raw}, halt=None)

    def state(self, db=None):
        if db is None:
            with closing(self.connect()) as reader:
                return self.state(reader)
        row = db.execute("SELECT state FROM run WHERE id=1").fetchone()
        return json.loads(row[0]) if row else self.empty()

    def stage(self, stage, detail=""):
        item = {"stage": stage, "detail": detail}
        self.progress = dict(month=self.progress["month"], **item,
                             events=[*self.progress["events"], item])

    def apply(self, action, body):
        # One writer across threads and processes. Repeated /step is idempotent.
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = self.state(db)
            if state["trained"] and bool(state["source"].get("raw")) != self.raw:
                raise ValueError("This output directory belongs to another source mode")
            if state["trained"] and bool(state.get("triggered")) != self.triggered:
                raise ValueError("Choose a separate directory for this replay policy")
            if state["trained"] and self.triggered and state.get("rain_lag_confirmation_months") != 2:
                raise ValueError("Use a new output directory for the two-month rain_lag_1 rule")
            if action == "train":
                if state["trained"]:
                    return state
                tmp = self.output / "pin.tmp"
                if self.raw:
                    history = pd.read_parquet(self.snapshot, filters=[("year_month", "<", MONTHS[0])])
                    history.to_parquet(tmp, index=False)
                    source_hash = sha256_file(tmp)
                else:
                    source_hash = sha256_file(self.snapshot)
                    shutil.copyfile(self.snapshot, tmp)
                    if sha256_file(tmp) != source_hash:
                        raise ValueError("Dataset changed while pinning the snapshot")
                pinned = self.output / f"unified_{source_hash}.parquet"
                os.replace(tmp, pinned)
                panel = pd.read_parquet(pinned)
                validate_history(panel, MONTHS)
                history = panel.loc[panel.year_month < MONTHS[0]].copy()
                models = prepare(history, self.baseline_path, champion_only=self.triggered)
                initial = "champion" if self.triggered else "rf_fixed"
                if self.triggered:
                    models = ChampionPolicy(models, models.fixed_forest)
                reference, stats = build_reference(history, source_hash)
                reference.to_parquet(self.output / "reference_2023_2024.parquet", index=False)
                write_json(self.output / "reference_2023_2024_stats.json", stats)
                state.update(trained=True, baseline=models.baseline, champion=initial,
                             source=dict(file=state["source"]["file"], raw=self.raw, sha256=source_hash,
                                         rows=len(panel), reference=REFERENCE_VERSION))
                if self.raw:
                    from src.stream.raw_2025 import source_files
                    state["source"]["raw_files"] = source_files()
                state["registry"].append(dict(month="2024-12", action="initial", model=initial,
                                              version=models.versions[initial]))
                state["deployment"] = dict(model=initial, version=models.versions[initial],
                                           effective_from=MONTHS[0])
            elif not state["trained"]:
                raise ValueError("Train the initial models first")
            elif action == "step":
                month = body.get("month")
                if any(m["month"] == month for m in state["months"]):
                    return state
                index = len(state["months"])
                if index >= len(MONTHS) or month != MONTHS[index]:
                    raise ValueError("Expected the next unprocessed month in 2025")
                self.progress = {"month": month, "stage": "FEATURES", "events": []}
                self.stage("FEATURES", "RAW / DQ")
                models = joblib.load(io.BytesIO(db.execute("SELECT models FROM run WHERE id=1").fetchone()[0]))
                source_hash = state["source"]["sha256"]
                pinned = self.output / f"unified_{source_hash}.parquet"
                if sha256_file(pinned) != source_hash:
                    raise ValueError("Pinned snapshot hash mismatch")
                panel = pd.read_parquet(pinned, filters=[("year_month", "<=", month)])
                try:
                    if self.raw:
                        from src.stream.raw_2025 import read_month, source_files
                        if source_files() != state["source"]["raw_files"]:
                            raise StreamHalt("Raw source manifest changed during replay", year_month=month)
                        raw_month = read_month(month)
                        accepted = [pd.DataFrame(m["batch"]) for m in state["months"]]
                        panel = pd.concat([panel, *accepted, raw_month], ignore_index=True)
                        source_hash = sha256_frame(panel)
                    result = self.month(models, panel, month, source_hash)
                    if self.raw:
                        result["batch"] = raw_month.to_dict("records")
                        result["batch_hash"] = sha256_frame(raw_month)
                except StreamHalt as exc:
                    _record_halt(self.output, exc, source_hash, panel, models.versions[state["champion"]], REFERENCE_VERSION)
                    state["halt"] = {"month": month, "reason": str(exc), "checks": exc.checks}
                    db.execute("UPDATE run SET state=? WHERE id=1", (json.dumps(state),))
                    db.commit()
                    raise
                serving = state.get("deployment") or {"model": state["champion"],
                                                      "version": result["versions"][state["champion"]]}
                if result["versions"][serving["model"]] != serving["version"]:
                    raise ValueError("Deployed forecast version differs from the checkpoint")
                result["champion"] = serving["model"]
                result["served_forecast"] = [
                    {"province_id": r["province_id"], "y_pred": r["y_pred"], "version": serving["version"]}
                    for r in result["rows"] if r["model"] == serving["model"]
                ]
                contest = result.get("competition")
                ranking = (sorted(contest["scores"], key=lambda k: contest["scores"][k]["wape"]) if contest is not None
                           else sorted(MODELS, key=lambda k: result["metrics"][k]["wape"]))
                next_month = str(pd.Period(month, freq="M") + 1)
                state["deployment"] = dict(model=state["champion"], version=models.versions[state["champion"]],
                                           effective_from=next_month)
                if contest and contest["decision"] == "PROMOTE":
                    state["registry"].append(dict(month=month, action="PROMOTE", **state["deployment"]))
                result["pipeline"] = dict(features={"rows": len(result["served_forecast"]),
                                                    "cutoff": str(pd.Period(month, freq="M") - 1)},
                                          predict=result["versions"], monitor={"psi": result["psi"], "ph": result["ph"]},
                                          retrain=(list(contest["scores"]) if contest is not None else
                                                   {k: models.versions[k] for k in ("rf_monthly", "linear")}),
                                          compare=ranking, deploy=state["deployment"].copy())
                state["months"].append(result)
                state["halt"] = None
                state["versions"] = dict(models.versions)
                frame = pd.DataFrame([r for m in state["months"] for r in m["rows"]])
                state["summary"] = {model: summarize(group, group.y_naive)
                                    for model, group in frame.groupby("model")}
            elif action in ("promote", "rollback"):
                if self.triggered:
                    raise ValueError("Champion selection follows the out-of-sample competition")
                choice = body.get("model")
                if choice not in MODELS or not state["months"]:
                    raise ValueError("Select an evaluated model after scoring a month")
                if choice == state["champion"]:
                    return state
                if action == "rollback" and choice not in [e["model"] for e in state["registry"]]:
                    raise ValueError("Rollback requires a previous Champion")
                state["champion"] = choice
                state["deployment"] = dict(model=choice, version=state["versions"][choice],
                                           effective_from=str(pd.Period(state["months"][-1]["month"], freq="M") + 1))
                state["registry"].append(dict(month=state["months"][-1]["month"], action=action,
                                              model=choice, version=state["versions"][choice]))
                db.execute("UPDATE run SET state=? WHERE id=1", (json.dumps(state),))
                return state
            else:
                raise ValueError("Unknown action")
            state["versions"] = dict(models.versions)
            state = jsonable(state)
            packed = io.BytesIO()
            joblib.dump(models, packed, compress=1)
            db.execute("INSERT OR REPLACE INTO run VALUES (1,?,?)",
                       (json.dumps(state, ensure_ascii=False, allow_nan=False), packed.getvalue()))
            db.commit()
            if action == "step" and self.triggered:
                self.stage("DONE")
            return state

    def month(self, models, panel, month, source_hash):
        staging = None if self.raw else _load_staging_rain(DATA / "staging/hii/fact_rainfall.parquet")
        features, labels, audit, dq = prepare_month(panel, month, self.output, source_hash, staging)
        if self.triggered:
            result = models.run(features, labels, month, self.stage)
            _write_lineage(self.output, month, source_hash, **audit, model_version=result["versions"],
                           reference_version=REFERENCE_VERSION, dq_report=f"dq/{month}.json")
            return dict(month=month, dq=dq, feature_hash=audit["feature_hash"], **result)
        psi = models.psi(features, month)
        versions = dict(models.versions)
        predictions = models.predict(features)
        actual = labels.guest_arrivals_total.to_numpy(dtype=float)
        naive = features.guest_lag_12.to_numpy(dtype=float)
        metrics, rows = {}, []
        for name, values in predictions.items():
            metrics[name] = _block(actual, values, naive)
            frame = pd.DataFrame(dict(year_month=month, province_id=features.province_id,
                                      province_th=labels.province_th, model=name, version=versions[name],
                                      y_true=actual, y_pred=values, y_naive=naive))
            rows.extend(frame.to_dict("records"))
        ph = models.ph(metrics)
        models.learn(features, pd.Series(actual), month)
        _write_lineage(self.output, month, source_hash, **audit, model_version=versions,
                       reference_version=REFERENCE_VERSION, dq_report=f"dq/{month}.json")
        return dict(month=month, rows=rows, metrics=metrics, psi=psi, ph=ph, versions=versions,
                    dq=dq, feature_hash=audit["feature_hash"])
