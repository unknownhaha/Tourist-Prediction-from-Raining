"""Build portable presentation data from the real replay and counterfactual monthly replays."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from src.common.paths import ROOT
from src.stream.lineage import sha256_file, sha256_frame
from src.stream.studio import MONTHS, SNAPSHOT, Studio
from src.stream.studio_models import prepare

STARTS = (3, 6, 9)
DEST = ROOT / "site" / "drift-lab.html"


def compact(result):
    return {key: result[key] for key in ("month", "metrics", "psi", "ph")}


def inject(panel, mode, start):
    changed = panel.copy()
    for number in range(start, 13):
        mask = changed.year_month == MONTHS[number - 1]
        if mode == "rain":
            for col in ("rain_min_mm", "rain_avg_mm", "rain_max_mm"):
                changed.loc[mask, col] = changed.loc[mask, col] * 2
        else:
            increase = 0.8 if mode == "sudden" else min(0.8, 0.2 * (number - start + 1))
            for col in ("guest_arrivals_total", "guest_arrivals_thai", "guest_arrivals_foreign"):
                changed.loc[mask, col] = (changed.loc[mask, col] * (1 + increase)).round()
    return changed


def build():
    panel = pd.read_parquet(SNAPSHOT)
    source_hash = sha256_file(SNAPSHOT)
    with TemporaryDirectory() as scratch:
        work = Path(scratch)
        studio = Studio(work)
        models = prepare(panel[panel.year_month < MONTHS[0]], work / "holdout.json")
        history, checkpoints = [], {}
        for number, month in enumerate(MONTHS, 1):
            if number in STARTS:
                checkpoints[number] = copy.deepcopy(models)
            history.append(compact(studio.month(models, panel, month, source_hash)))
        runs = {"observed": history}
        for start in STARTS:
            for mode in ("sudden", "gradual", "rain"):
                scenario = inject(panel, mode, start)
                version = sha256_frame(scenario)
                branch = copy.deepcopy(checkpoints[start])
                output = list(history[:start-1])
                for month in MONTHS[start-1:]:
                    output.append(compact(studio.month(branch, scenario, month, version)))
                runs[f"{mode}-{start}"] = output
                print(mode, start, flush=True)
    template = (ROOT / "src/stream/drift_lab_template.html").read_text(encoding="utf-8")
    page = template.replace("__REPLAY_DATA__", json.dumps(runs, allow_nan=False, separators=(",", ":")))
    target = DEST.with_suffix(".tmp")
    target.write_text(page, encoding="utf-8")
    target.replace(DEST)


if __name__ == "__main__":
    build()
