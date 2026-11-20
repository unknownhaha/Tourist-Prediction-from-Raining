"""Replay 2025-01..2025-12 one month at a time.

Order each month: validate, clean, re-validate, data-quality report,
features through t-1, predict, reveal y_t, log, learn, optional drift hook.

A halt writes that month's DQ report and lineage, then stops. It does not
write predictions or metrics. Months that already finished keep their files.

`--model online-linear` and `--drift page-hinkley` are examples for wiring
only. DS/MLE own the real model, the detector, and its thresholds.

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
from src.stream.quality import FLAG_COLUMNS, assess_month
from src.stream.reference import REFERENCE_VERSION, build_reference
from src.stream.validate import (
    N_PROVINCES,
    StreamHalt,
    validate_features,
    validate_history,
    validate_month,
    validate_working_set,
)

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
    """Run the stream and write logs under `output_dir`. Return the metrics.

    MASE in the returned metrics is the model's MAE divided by the seasonal-naive
    MAE on these same out-of-sample months. It is not the textbook MASE that
    scales by in-sample one-step errors from the training period.
    """
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

    online = model if model is not None else model_from_name(model_name, seed)
    hook = drift if drift is not None else hook_from_name(drift_name)
    try:
        validate_history(panel, months)
    except StreamHalt as exc:
        _record_halt(output_dir, exc, source_hash, panel, online.model_version, REFERENCE_VERSION)
        raise
    reference, stats = build_reference(panel, source_hash)
    reference.to_parquet(output_dir / "reference_2023_2024.parquet", index=False)
    write_json(output_dir / "reference_2023_2024_stats.json", stats)

    logs: list[pd.DataFrame] = []
    feature_frames: dict[str, pd.DataFrame] = {}

    for replay_index, year_month in enumerate(months, start=1):
        try:
            _replay_month(
                panel=panel,
                year_month=year_month,
                replay_index=replay_index,
                output_dir=output_dir,
                source_hash=source_hash,
                staging_rain=staging_rain,
                online=online,
                hook=hook,
                seed=seed,
                reference_version=stats["version"],
                logs=logs,
                feature_frames=feature_frames,
            )
        except StreamHalt as exc:
            _record_halt(
                output_dir,
                exc,
                source_hash,
                panel,
                online.model_version,
                stats["version"],
            )
            raise

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


def _replay_month(
    *,
    panel: pd.DataFrame,
    year_month: str,
    replay_index: int,
    output_dir: Path,
    source_hash: str,
    staging_rain: pd.DataFrame | None,
    online,
    hook,
    seed: int,
    reference_version: str,
    logs: list[pd.DataFrame],
    feature_frames: dict[str, pd.DataFrame],
) -> None:
    working = panel.loc[panel["year_month"].astype(str) <= year_month].copy()
    validate_working_set(working, year_month)
    raw_month = _month_slice(working, year_month)
    if raw_month.empty:
        raise StreamHalt(
            f"missing month {year_month}; halt and alert, do not skip or impute",
            [{"name": "row_count", "severity": "hard", "passed": False, "detail": "0 rows"}],
            year_month=year_month,
        )
    checks_before = validate_month(raw_month, year_month, stage="validate")
    cleaned, actions = clean_panel(working, staging_rain)
    cleaned_month = _month_slice(cleaned, year_month)
    checks_after = validate_month(cleaned_month, year_month, stage="re-validate")
    report, flags = assess_month(cleaned_month, cleaned, year_month)
    report["data_version"] = source_hash
    _write_dq(output_dir, year_month, report, flags)
    if report["hard_fail"]:
        raise StreamHalt(
            f"data-quality hard fail for {year_month}",
            report["checks"],
            year_month=year_month,
        )

    features = build_forecast_features(cleaned, year_month)
    features["province_id"] = features["province_id"].astype(str)
    features = features.sort_values("province_id").reset_index(drop=True)
    features.insert(1, "year_month", year_month)
    assert_no_future_leakage(forecast_source_months(year_month).values(), year_month)
    feature_checks = validate_features(features, year_month)
    y_true = cleaned_month.copy()
    y_true["province_id"] = y_true["province_id"].astype(str)
    y_true = y_true.sort_values("province_id").reset_index(drop=True)
    if not features["province_id"].equals(y_true["province_id"]):
        raise StreamHalt(
            f"feature provinces do not match actuals for {year_month}",
            year_month=year_month,
        )
    if "guest_arrivals_total" in features.columns:
        raise StreamHalt("features include the target", year_month=year_month)

    feature_hash = _write_feature_table(output_dir, year_month, features)
    y_hat = online.predict(features).astype(float).reset_index(drop=True)
    actual = y_true["guest_arrivals_total"].astype(float).reset_index(drop=True)
    error = actual - y_hat
    error.attrs["y_true"] = actual.to_numpy()
    error.attrs["y_pred"] = y_hat.to_numpy()
    alarm = bool(hook.update_errors(error) or hook.update_features(features))
    log = pd.DataFrame(
        {
            "province_id": features["province_id"].to_numpy(),
            "year_month": year_month,
            "y_pred": y_hat.to_numpy(),
            "y_true": actual.to_numpy(),
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
    _write_lineage(
        output_dir,
        year_month,
        source_hash,
        sha256_frame(raw_month),
        checks_before + checks_after + feature_checks + report["checks"],
        actions,
        feature_hash,
        online.model_version,
        reference_version,
        f"dq/{year_month}.json",
    )
    online.learn(features, actual)


def _write_feature_table(output_dir: Path, year_month: str, features: pd.DataFrame) -> str:
    """Write a stable parquet file and return its sha256."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = output_dir / "features" / f"{year_month}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = features.sort_values("province_id").reset_index(drop=True)
    table = pa.Table.from_pandas(saved, preserve_index=False)
    table = table.replace_schema_metadata({})
    pq.write_table(table, path, compression="snappy")
    return sha256_file(path)


def _write_dq(output_dir: Path, year_month: str, report: dict, flags: pd.DataFrame) -> None:
    dq_dir = output_dir / "dq"
    write_json(dq_dir / f"{year_month}.json", report)
    if flags.empty:
        flags = pd.DataFrame(columns=list(FLAG_COLUMNS))
    flags.to_parquet(dq_dir / f"{year_month}_rows.parquet", index=False)


def _record_halt(
    output_dir: Path,
    exc: StreamHalt,
    source_hash: str,
    panel: pd.DataFrame,
    model_version: str,
    reference_version: str,
) -> None:
    """Write the failing month's DQ report and lineage, then let the caller raise."""
    year_month = exc.year_month or "unknown"
    sliver = panel.loc[panel["year_month"].astype(str) == year_month] if "year_month" in panel.columns else panel.iloc[0:0]
    report = {
        "year_month": year_month,
        "data_version": source_hash,
        "n_rows": int(len(sliver)),
        "hard_fail": True,
        "n_warnings": 0,
        "checks": exc.checks,
    }
    _write_dq(output_dir, year_month, report, pd.DataFrame(columns=list(FLAG_COLUMNS)))
    _write_lineage(
        output_dir,
        year_month,
        source_hash,
        sha256_frame(sliver) if len(sliver) else source_hash,
        exc.checks,
        [],
        None,
        model_version,
        reference_version,
        f"dq/{year_month}.json",
    )


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
    parser.add_argument(
        "--model",
        default="seasonal-naive",
        choices=("seasonal-naive", "lag-12", "online-linear"),
        help=(
            "seasonal-naive is the shipped baseline. online-linear is an EXAMPLE "
            "for wiring only; DS/MLE own the real model. Its learning step is not tuned."
        ),
    )
    parser.add_argument(
        "--drift",
        default="none",
        choices=("none", "page-hinkley"),
        help=(
            "none records no alarm. page-hinkley is an EXAMPLE hook only; "
            "DS/MLE choose the detector and thresholds. This flag does not retrain."
        ),
    )
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
