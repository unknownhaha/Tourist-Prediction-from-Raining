"""Local, click-driven monthly training. Run python -m experiments.online_replay.manual_studio."""
import io
import json
import sqlite3
import tempfile
import traceback
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from experiments.online_replay.features import EXP_COLUMNS
from experiments.psi_adwin.policy import replay
from src.stream.champion_policy import promotion_rule
from src.stream.drift import PageHinkley
from src.stream.metrics import _block, wape
from src.stream.studio_models import psi_alarm


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PANEL = ROOT / "data/model/province_month_unified.parquet"
INITIAL = HERE / "results/stream_2024_2025"
RECIPES = {"logdiff": ("logdiff", EXP_COLUMNS), "level": ("level", ()),
           "rf_logdiff": ("logdiff", ()), "yoy3": ("logdiff", ("yoy_3m_log",))}
CHALLENGERS = ("rf_logdiff", "yoy3")
# Small delta accumulates modest WAPE rises. Threshold 0.7 is the alarm gate.
PH_DELTA = 0.005
PH_THRESHOLD = 0.7


def retrain_choice(record, current):
    """Switch to Random Forest when its month WAPE is strictly lower. No margin and no second month."""
    metrics = record.get("metrics") or {}
    current_wape = (metrics.get(current) or {}).get("wape")
    forest_wape = (metrics.get("random_forest") or {}).get("wape")
    if current_wape is None or forest_wape is None:
        return current
    if not (np.isfinite(current_wape) and np.isfinite(forest_wape)):
        return current
    if forest_wape < current_wape:
        return "random_forest"
    return current


def apply_champion(history, start):
    """The served model stays until Retrain finds a lower Random Forest WAPE."""
    champion = "linear"
    version = 1
    registry = [{
        "id": "v001",
        "model": "linear",
        "effective_from": str(pd.Period(start, "M") + 1),
        "rolled_back": False,
    }]
    for record in history:
        if record.get("retrained"):
            chosen = record["next_model"] if record.get("next_model") else retrain_choice(record, champion)
            record["next_model"] = chosen
            record["switched"] = chosen != champion
            if chosen != champion:
                version += 1
                champion = chosen
                registry.append({
                    "id": f"v{version:03d}",
                    "model": chosen,
                    "effective_from": record["month"],
                    "rolled_back": False,
                })
        record["served"] = champion
        record["registry"] = [dict(item) for item in registry]


def needs_retrain(record):
    """True only when this month's detector asked for a retrain and it has not been run."""
    if record.get("retrained"):
        return False
    action = str(record.get("action") or "")
    return bool(record.get("ph_alarm")) or "prepare_retrain" in action


def month_rain(panel, month, provinces):
    """Same-month rainfall (mm), one value per province name, in that order."""
    rows = panel.loc[panel["year_month"].astype(str) == str(month), ["province_th", "rain_avg_mm"]]
    lookup = {}
    for name, value in zip(rows["province_th"], rows["rain_avg_mm"]):
        amount = float(value)
        if not np.isfinite(amount):
            continue
        lookup[str(name)] = amount
    missing = [name for name in provinces if name not in lookup]
    if missing:
        raise ValueError(f"missing rainfall for {month}: {', '.join(missing[:3])}")
    return [lookup[name] for name in provinces]


def ph_updates(seed):
    baseline = np.asarray(seed[-12:], dtype=float)
    mean, scale = baseline.mean(), baseline.std(ddof=1)
    if len(baseline) != 12 or not np.isfinite(scale) or scale <= 0:
        raise ValueError("Page-Hinkley requires 12 variable baseline errors")
    detector = PageHinkley(delta=PH_DELTA, threshold=PH_THRESHOLD, initial_count=12)
    def update(error):
        alarm = detector.update((error - mean) / scale)
        return {"ph_alarm": bool(alarm), "ph_score": float(detector.statistic)}
    return update


def _month_action(st: dict) -> str:
    """Same labels as experiments.online_replay.replay.month_action, without that import chain."""
    kinds = [event["event"] for event in st["events"]]
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
    for kind in ("promote", "rollback"):
        if kind in kinds:
            action += f"+{kind}"
    return action


def _ewa():
    from experiments.beat_seasonal_naive.run import matrix_for, reconstruct
    from experiments.online_replay import experts as expert_module
    from experiments.online_replay import replay as replay_module
    from experiments.online_replay.selection import frozen_choice, load_config
    return expert_module, replay_module, matrix_for, reconstruct, frozen_choice, load_config


def context(end):
    expert_module, _, _, _, _, _ = _ewa()
    data = pd.read_parquet(PANEL, filters=[("year_month", "<=", end)])
    data["province_id"] = data.province_id.astype(str).str.zfill(2)
    return expert_module.Context.build(data.sort_values(["year_month", "province_id"]))


def fitted(ctx, end, initial=False):
    expert_module, _, _, _, _, _ = _ewa()
    models = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, (target, extra) in RECIPES.items():
            path = INITIAL / f"{name}_{end}.joblib"
            if not (initial and name in ("level", "logdiff") and path.exists()):
                path = Path(tmp) / f"{name}.joblib"
                expert_module.fit_predict(ctx, target, end, [], exp=extra, checkpoint=path)
            models[name] = joblib.load(path)
            if models[name]["train_end"] != end:
                raise ValueError("Checkpoint cutoff mismatch")
    return models


def predict(ctx, models, month, end):
    expert_module, _, matrix_for, reconstruct, _, _ = _ewa()
    part = expert_module.part_of(ctx, [month]).sort_values("province_id")
    if len(part) != 77 or not part.guest_arrivals_total.isna().all():
        raise ValueError("Expected 77 unlabeled forecast rows")
    output = {}
    for key, model in models.items():
        target, extra = RECIPES[key]
        matrix = matrix_for(part, ctx.history, "base" if target == "level" else "ext", end)
        matrix = pd.concat([matrix, part[list(extra)]], axis=1)
        features = matrix[model["columns"]].fillna(model["medians"]).to_numpy(float)
        raw = model["model"].predict(model["scaler"].transform(features))
        output[key] = reconstruct(target, raw, part.guest_lag_12.to_numpy())
    return part, output


def monitor(history, seed, cfg, start, *, challengers=None, initial="champion"):
    months = [r["month"] for r in history]
    data = {r["month"]: r for r in history}
    def psi(m, _):
        features = [k for k, v in data[m]["psi"].items() if psi_alarm(v)]
        return bool(features), features, "rain_lag_1" in features
    states = []
    update = ph_updates(seed)
    reading = {}
    def trigger(month, error):
        reading.update(update(error))
        if "adwin" in data[month]:  # Reproduce actual decisions made before migration.
            return "prepare_retrain" in data[month]["action"]
        return reading["ph_alarm"]

    names = CHALLENGERS if challengers is None else challengers
    policy = replay(months, {m: data[m]["actual"] for m in months},
                    {m: data[m]["naive"] for m in months},
                    lambda key, m: data[m]["predictions"][key.split("@")[0]], psi, names,
                    promotion_rule(cfg["promotion_preset"]), trigger_mode="performance",
                    performance_trigger=trigger, log=states.append,
                    rollback_months=cfg["rollback_months"], initial_model=f"{initial}@{start}",
                    trained_through=start, reference_end=start)
    st = states[-1]
    return dict(served=st["served_model"].split("@")[0], action=_month_action(st),
                **reading, psi_watch=st["psi"], decision=st["decision"],
                registry=policy["registry"])


class Studio:
    def __init__(self, db, start="2023-12", stop="2025-12", recipe="ewa"):
        self.db, self.start, self.stop, self.recipe = db, start, stop, recipe
        self._walk = None
        db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS initial (id INTEGER PRIMARY KEY, models BLOB, seed TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS session (id INTEGER PRIMARY KEY, revision INTEGER, history TEXT, models BLOB)")
            conn.execute("INSERT OR IGNORE INTO session VALUES (1, 0, '[]', NULL)")

    def state(self, revision, history):
        end = history[-1]["month"] if history else self.start
        nxt = str(pd.Period(end, "M")+1) if end < self.stop else None
        shown = []
        for record in history:
            item = dict(record)
            if self.recipe == "level" and not item.get("rain") and item.get("province"):
                item["rain"] = month_rain(self.walk().panel, item["month"], item["province"])
            item["needs_retrain"] = needs_retrain(item)
            shown.append(item)
        if self.recipe == "level":
            apply_champion(shown, self.start)
            for item in shown:
                item["needs_retrain"] = needs_retrain(item)
        return dict(revision=revision, through=end, next=nxt, history=shown,
                    ph_delta=PH_DELTA, ph_threshold=PH_THRESHOLD)

    def read(self):
        with closing(sqlite3.connect(self.db)) as conn:
            rev, history = conn.execute("SELECT revision,history FROM session").fetchone()
        return self.state(rev, json.loads(history))

    def walk(self):
        if self._walk is None:
            from experiments.online_replay.level_walk import LevelWalk
            self._walk = LevelWalk()
        return self._walk

    def initialize(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if self.recipe == "level":
                self._initialize_level(conn)
                return
            existing = conn.execute("SELECT seed FROM initial").fetchone()
            if existing:
                history = json.loads(conn.execute("SELECT history FROM session").fetchone()[0])
                if any("ph_score" not in r for r in history):
                    update = ph_updates(json.loads(existing[0]))
                    for r in history:
                        r.update(update(r["metrics"][r["served"]]["wape"]))
                    conn.execute("UPDATE session SET history=?, revision=revision+1 WHERE id=1",
                                 (json.dumps(history, allow_nan=False),))
                return
            ctx = context(self.start)
            seed = []
            _, replay_module, _, _, frozen_choice, load_config = _ewa()
            for year in (int(self.start[:4])-1, int(self.start[:4])):
                part, pred, _, _ = replay_module._champion_inputs(ctx, frozen_choice(load_config()), year)
                seed.extend(replay_module.monthly_wape_series(part, pred))
            blob = serialize(fitted(ctx, self.start, initial=True))
            conn.execute("INSERT INTO initial VALUES(1,?,?)", (blob, json.dumps(seed)))

    def _initialize_level(self, conn):
        if conn.execute("SELECT seed FROM initial").fetchone():
            return
        walk = self.walk()
        print(f"fitting the level models through {self.start}", flush=True)
        seed = walk.seed_wapes(self.start)
        blob = serialize(walk.fit_through(self.start))
        conn.execute("INSERT INTO initial VALUES(1,?,?)", (blob, json.dumps(seed)))
        print(f"ready · Page-Hinkley seed WAPE {seed[0]:.4f} .. {seed[-1]:.4f}", flush=True)

    def _advance_level(self, conn, history, blob, end, month):
        from experiments.online_replay.level_walk import MODEL_KEYS
        from src.stream.metrics import _block

        walk = self.walk()
        cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))["drift"]
        initial_blob, encoded_seed = conn.execute("SELECT models,seed FROM initial").fetchone()
        seed = json.loads(encoded_seed)
        models = joblib.load(io.BytesIO(blob or initial_blob))
        frame, pred = walk.predict(models, month)
        actual = frame["guest_arrivals_total"].to_numpy(dtype=float)
        if not np.isfinite(actual).all():
            raise ValueError("Incomplete observations")
        naive = frame["guest_lag_12"].to_numpy(dtype=float)
        keys = (*MODEL_KEYS, "seasonal")
        record = dict(
            month=month, cutoff=end, province=frame["province_th"].tolist(),
            actual=actual.tolist(), naive=naive.tolist(),
            predictions={key: pred[key].tolist() for key in keys},
            metrics={key: _block(actual, pred[key], naive) for key in keys},
            psi=walk.psi(month),
        )
        record["rain"] = month_rain(walk.panel, month, record["province"])
        record["retrained"] = False
        history.append(record)
        record.update(monitor(
            history, seed, cfg, self.start,
            # Signals only. Do not apply the 0.005 / two-month / MAE / RMSE promotion rule.
            challengers=(), initial="linear",
        ))
        apply_champion(history, self.start)
        return serialize(walk.fit_through(month))

    def change(self, action, revision):
        with closing(sqlite3.connect(self.db, timeout=1)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            rev, encoded, blob = conn.execute("SELECT revision,history,models FROM session").fetchone()
            if revision != rev:
                raise ValueError("สถานะเปลี่ยนแล้ว กรุณาโหลดหน้าใหม่")
            history = json.loads(encoded)
            if action == "reset":
                history, blob = [], None
            elif action == "retrain":
                blob = self._retrain(history)
            else:
                state = self.state(rev, history)
                end, month = state["through"], state["next"]
                if not month:
                    raise ValueError("ครบทุกเดือนแล้ว")
                if self.recipe == "level":
                    blob = self._advance_level(conn, history, blob, end, month)
                    conn.execute("UPDATE session SET revision=?,history=?,models=? WHERE id=1",
                                 (rev+1, json.dumps(history, allow_nan=False), blob))
                    return self.state(rev+1, history)
                expert_module, replay_module, _, _, _, load_config = _ewa()
                ctx = context(end)
                cfg = load_config()
                replay_module._gate(ctx, month)
                initial_blob, encoded_seed = conn.execute("SELECT models,seed FROM initial").fetchone()
                seed = json.loads(encoded_seed)
                models = joblib.load(io.BytesIO(blob or initial_blob))
                part, pred = predict(ctx, models, month, end)
                weights = np.array(history[-1]["next_weights"] if history else [.5,.5])
                pred["champion"] = weights[0]*pred["logdiff"] + weights[1]*pred["level"]
                drift = cfg["drift"]
                psi = replay_module.psi_values(ctx.table, month, self.start, drift["psi_features"])
                # Open this month's observations only after the four forests have predicted.
                observed = context(month)
                actual = expert_module.part_of(observed, [month]).set_index("province_id").loc[part.province_id, "guest_arrivals_total"].to_numpy(float)
                if not np.isfinite(actual).all():
                    raise ValueError("Incomplete observations")
                naive = part.guest_lag_12.to_numpy()
                weights *= np.exp(-float(cfg["selection"]["eta"])*np.array([wape(actual, pred[k]) for k in ("logdiff","level")]))
                keys = ("champion", *CHALLENGERS)
                record = dict(month=month, cutoff=end, province=part.province_th.tolist(), actual=actual.tolist(),
                              naive=naive.tolist(), predictions={k: pred[k].tolist() for k in keys},
                              metrics={k: _block(actual, pred[k], naive) for k in keys}, psi=psi,
                              next_weights=(weights/weights.sum()).tolist())
                history.append(record)
                record.update(monitor(history, seed, drift, self.start))
                blob = serialize(fitted(observed, month))
            conn.execute("UPDATE session SET revision=?,history=?,models=? WHERE id=1",
                         (rev+1,json.dumps(history, allow_nan=False),blob))
        return self.state(rev+1, history)

    def _retrain(self, history):
        if self.recipe != "level":
            raise ValueError("Retrain ใช้ได้เมื่อเดินโมเดล level")
        if not history or not needs_retrain(history[-1]):
            raise ValueError("Retrain กดได้เมื่อตัวตรวจจับแจ้ง Drift และต้องฝึกใหม่")
        fitted_blob = serialize(self.walk().fit_through(history[-1]["month"]))
        history[-1]["retrained"] = True
        history[-1].pop("next_model", None)
        apply_champion(history, self.start)
        return fitted_blob


def serialize(models):
    stream = io.BytesIO()
    joblib.dump(models, stream, compress=3)
    return stream.getvalue()


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, value, mime="application/json"):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime+"; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.reply(200,(HERE/"web/index.html").read_bytes(),"text/html")
        elif self.path == "/studio.css":
            source = (ROOT/"site/studio.html").read_text(encoding="utf8")
            self.reply(200,source.split("<style>",1)[1].split("</style>",1)[0].encode(),"text/css")
        elif self.path == "/api/state":
            self.reply(200,self.server.studio.read())
        else:
            self.reply(404,{})

    def do_POST(self):
        if self.path not in ("/api/train","/api/reset","/api/retrain"):
            return self.reply(404,{})
        address = "127.0.0.1:"+str(self.server.server_port)
        if (self.headers.get("Host") != address or self.headers.get("Origin", "http://"+address) != "http://"+address
                or self.headers.get_content_type() != "application/json"):
            return self.reply(403, {"error":"Local JSON requests only"})
        try:
            size = int(self.headers.get("Content-Length",0))
            if not 0 < size <= 256:
                raise ValueError("Invalid request")
            revision = json.loads(self.rfile.read(size))["revision"]
            state = self.server.studio.change(self.path.split("/")[-1], revision)
        except (ValueError, KeyError, sqlite3.OperationalError) as exc:
            return self.reply(409, {"error":str(exc)})
        except Exception:
            traceback.print_exc()
            return self.reply(500, {"error":"ฝึกไม่สำเร็จ ระบบยังอยู่ที่เดือนเดิม กรุณาดู server log"})
        self.reply(200,state)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8774)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port),Handler)
    server.studio = Studio(ROOT/"outputs/manual_studio/level_session.sqlite", recipe="level")
    server.studio.initialize()
    print("http://127.0.0.1:%d/" % args.port, flush=True)
    server.serve_forever()
