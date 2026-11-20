"""Replay 2025-01..2025-12 one month at a time.

Order each month: validate, clean, re-validate, data-quality report,
features through t-1, predict, reveal y_t, log, learn, optional drift hook.

    python -m src.stream.replay --start 2025-01 --end 2025-12
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd

from src.common.paths import DATA, ROOT
from src.model.leakage import TIME_SPLIT, assert_no_future_leakage, build_forecast_features, forecast_source_months
from src.stream.clean import clean_panel
from src.stream.drift import NullDrift, hook_from_name
from src.stream.lineage import sha256_file, sha256_frame, write_json
from src.stream.metrics import seasonal_naive_from_log, summarize
from src.stream.model import SeasonalNaive, model_from_name
from src.stream.quality import assess_month
from src.stream.reference import build_reference
from src.stream.validate import N_PROVINCES, StreamHalt, validate_features, validate_month, validate_working_set

STREAM_START, STREAM_END = TIME_SPLIT["online_stream"]
DEFAULT_OUTPUT = ROOT / "outputs" / "stream_2025"
SNAPSHOT_NAME = "province_month_unified.parquet"


def _load_staging_rain(path: Path | None) -> pd.DataFrame | None:
    if path is None or not path.exists():
        return None
    return pd.read_parquet(path)


def _month_slice(frame: pd.DataFrame, year_month: str) -> pd.DataFrame:
    return frame.loc[frame["year_month"].astype(str) == year_month].copy()


def replay(
    *,
    output_dir: Path,
    snapshot_path: Path | None = None,
    panel: pd.DataFrame | None = None,
    staging_rain: pd.DataFrame | None = None,
    start: str = STREAM_START,
    end: str = STREAM_END,
    seed: int = 2025,
    model_name: str = "seasonal-naive",
    drift_name: str = "none",
    model=None,
    drift=None,
) -> dict:
    """Run the stream and write logs under `output_dir`. Return the metrics."""
    if (start, end) != (STREAM_START, STREAM_END):
        raise StreamHalt(
            f"replay accepts only {STREAM_START}..{STREAM_END}, got {start}..{end}"
        )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if panel is None:
        if snapshot_path is None:
            snapshot_path = DATA / "model" / SNAPSHOT_NAME
        snapshot_path = Path(snapshot_path)
        source_hash = sha256_file(snapshot_path)
        snapshot_copy = output_dir / "snapshots" / f"unified_{source_hash}.parquet"
        snapshot_copy.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot_copy.exists():
            shutil.copy2(snapshot_path, snapshot_copy)
        panel = pd.read_parquet(snapshot_copy)
    else:
        source_hash = sha256_frame(panel)
        snapshot_path = None

    panel = panel.sort_values(["year_month", "province_id"]).reset_index(drop=True)
    months = [f"2025-{month:02d}" for month in range(1, 13)]
    reference, stats = build_reference(panel, source_hash)
    reference.to_parquet(output_dir / "reference_2023_2024.parquet", index=False)
    write_json(output_dir / "reference_2023_2024_stats.json", stats)

    online = model if model is not None else model_from_name(model_name, seed)
    hook = drift if drift is not None else hook_from_name(drift_name)
    logs: list[pd.DataFrame] = []
    feature_frames: dict[str, pd.DataFrame] = {}

    for replay_index, year_month in enumerate(months, start=1):
        working = panel.loc[panel["year_month"].astype(str) <= year_month].copy()
        validate_working_set(working, year_month)
        raw_month = _month_slice(working, year_month)
        if raw_month.empty:
            raise StreamHalt(f"missing month {year_month}; halt and alert, do not skip or impute")
        checks_before = validate_month(raw_month, year_month, stage="validate")
        cleaned, actions = clean_panel(working, staging_rain)
        cleaned_month = _month_slice(cleaned, year_month)
        checks_after = validate_month(cleaned_month, year_month, stage="re-validate")
        report, flags = assess_month(cleaned_month, cleaned, year_month)
        report["data_version"] = source_hash
        dq_dir = output_dir / "dq"
        write_json(dq_dir / f"{year_month}.json", report)
        flags.to_parquet(dq_dir / f"{year_month}_rows.parquet", index=False)
        if report["hard_fail"]:
            _write_lineage(
                output_dir,
                year_month,
                source_hash,
                sha256_frame(raw_month),
                checks_before + checks_after + report["checks"],
                actions,
                None,
                online.model_version,
                stats["version"],
                f"dq/{year_month}.json",
            )
            raise StreamHalt(f"data-quality hard fail for {year_month}", report["checks"])

        features = build_forecast_features(cleaned, year_month)
        features["province_id"] = features["province_id"].astype(str)
        features = features.sort_values("province_id").reset_index(drop=True)
        assert_no_future_leakage(forecast_source_months(year_month).values(), year_month)
        feature_checks = validate_features(features, year_month)
        y_true = cleaned_month.copy()
        y_true["province_id"] = y_true["province_id"].astype(str)
        y_true = y_true.sort_values("province_id").reset_index(drop=True)
        if not features["province_id"].equals(y_true["province_id"]):
            raise StreamHalt(f"feature provinces do not match actuals for {year_month}")
        if "guest_arrivals_total" in features.columns:
            raise StreamHalt("features include the target")

        y_hat = online.predict(features).astype(float).reset_index(drop=True)
        actual = y_true["guest_arrivals_total"].astype(float).reset_index(drop=True)
        error = actual - y_hat
        error.attrs["y_true"] = actual.to_numpy()
        error.attrs["y_pred"] = y_hat.to_numpy()
        alarm = bool(hook.update_errors(error) or hook.update_features(features))
        log = pd.DataFrame(
            {
                "province_id": features["province_id"],
                "year_month": year_month,
                "y_pred": y_hat,
                "y_true": actual,
                "error": error.to_numpy(),
                "abs_error": error.abs().to_numpy(),
                "model_version": online.model_version,
                "drift_flag": alarm,
                "drift_detector": hook.name if alarm and not isinstance(hook, NullDrift) else None,
                "retrain_triggered": False,
                "seed": int(seed),
                "split_role": "online_stream",
                "replay_index": replay_index,
            }
        )
        logs.append(log)
        feature_frames[year_month] = features
        feature_hash = sha256_frame(features.assign(year_month=year_month))
        _write_lineage(
            output_dir,
            year_month,
            source_hash,
            sha256_frame(raw_month),
            checks_before + checks_after + feature_checks + report["checks"],
            actions,
            feature_hash,
            online.model_version,
            stats["version"],
            f"dq/{year_month}.json",
        )
        online.learn(features, actual)

    prediction_log = pd.concat(logs, ignore_index=True)
    prediction_log.to_parquet(output_dir / "predictions.parquet", index=False)
    prediction_log.to_csv(output_dir / "predictions.csv", index=False, lineterminator="\n")
    naive = seasonal_naive_from_log(prediction_log, feature_frames)
    metrics = summarize(prediction_log, naive)
    metrics["model_version"] = online.model_version
    metrics["seed"] = int(seed)
    metrics["n_predictions"] = int(len(prediction_log))
    metrics["data_version"] = source_hash
    write_json(output_dir / "metrics.json", metrics)
    if len(prediction_log) != 12 * N_PROVINCES:
        raise StreamHalt(f"expected {12 * N_PROVINCES} predictions, got {len(prediction_log)}")
    return metrics


def _write_lineage(
    output_dir: Path,
    year_month: str,
    data_version: str,
    input_hash: str,
    checks: list[dict],
    actions: list[dict],
    feature_hash: str | None,
    model_version: str,
    reference_version: str,
    dq_report: str,
) -> None:
    write_json(
        output_dir / "lineage" / f"{year_month}.json",
        {
            "year_month": year_month,
            "data_version": data_version,
            "input_hash": input_hash,
            "checks": checks,
            "cleaning_actions": actions,
            "feature_table_hash": feature_hash,
            "model_version": model_version,
            "reference_version": reference_version,
            "dq_report": dq_report.replace("\\", "/"),
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay the 2025 province-month stream.")
    parser.add_argument("--start", default=STREAM_START)
    parser.add_argument("--end", default=STREAM_END)
    parser.add_argument("--seed", type=int, default=2025)
    parser.add_argument("--model", default="seasonal-naive", choices=("seasonal-naive", "lag-12", "online-linear"))
    parser.add_argument("--drift", default="none", choices=("none", "page-hinkley"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--snapshot", type=Path, default=DATA / "model" / SNAPSHOT_NAME)
    parser.add_argument(
        "--staging-rain",
        type=Path,
        default=DATA / "staging" / "hii" / "fact_rainfall.parquet",
    )
    args = parser.parse_args(argv)
    try:
        staging = _load_staging_rain(args.staging_rain)
        replay(
            output_dir=args.output_dir,
            snapshot_path=args.snapshot,
            staging_rain=staging,
            start=args.start,
            end=args.end,
            seed=args.seed,
            model_name=args.model,
            drift_name=args.drift,
        )
    except StreamHalt as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
