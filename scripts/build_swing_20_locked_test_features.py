"""Build the SWING_20 point-in-time feature dataset for the locked_test split ONLY.

Companion to build_swing_20_feature_dataset.py (which deliberately excludes
locked_test). This script exists specifically for the one-shot Locked Test
evaluation pre-registered in docs/09_experiments/EXP-003_SWING20_Locked_Test.md, and
must not be run before that pre-registration is committed. It applies the exact same
quarantine, target-already-reached-at-entry exclusion, and temporal split assignment
as the train+validation build, just filtered to split == "locked_test" instead.

Example:
    python scripts/build_swing_20_locked_test_features.py \
        --dataset-dir artifacts/swing_20/snapshots/swing20_20260718T135238Z
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_analyzer.data.data_fetcher import get_stock_data
from stock_analyzer.datasets.swing_20.artifacts import file_sha256, write_frame, write_manifest
from stock_analyzer.datasets.swing_20.audit import apply_data_quality_quarantine, exclude_target_already_reached_at_entry
from stock_analyzer.datasets.swing_20.config import Swing20Config
from stock_analyzer.datasets.swing_20.features import build_feature_dataset, build_lineage, compute_market_context
from stock_analyzer.datasets.swing_20.prepare import _allocate_snapshot_dir, load_frozen_dataset, verify_frozen_dataset
from stock_analyzer.datasets.swing_20.splits import assign_temporal_splits


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the SWING_20 locked_test feature dataset (one-shot use).")
    parser.add_argument("--dataset-dir", required=True, help="Frozen SWING_20 snapshot directory.")
    parser.add_argument("--output-dir", default="artifacts/swing_20_features_locked_test", help="Feature dataset artifact root.")
    parser.add_argument("--progress-every", type=int, default=200, help="Print progress every N symbols.")
    parser.add_argument("--skip-verify", action="store_true", help="Skip SHA-256 verification of the frozen snapshot.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = Swing20Config()

    if not args.skip_verify:
        verification = verify_frozen_dataset(args.dataset_dir)
        failed = sorted(name for name, ok in verification.items() if not ok)
        if failed:
            raise SystemExit(f"Refusing to build features from a snapshot with hash mismatches: {', '.join(failed)}.")

    frozen = load_frozen_dataset(Path(args.dataset_dir))
    print(f"[locked_test-features] loaded snapshot {frozen['manifest'].get('dataset_version')}", flush=True)

    labels_frame = frozen["labels"]
    if "target_already_reached_at_entry" not in labels_frame.columns and "large_gap_at_entry" in labels_frame.columns:
        labels_frame = labels_frame.copy()
        labels_frame["target_already_reached_at_entry"] = labels_frame["large_gap_at_entry"] >= config.label.target_return
        frozen["labels"] = labels_frame
        print("[locked_test-features] derived target_already_reached_at_entry from large_gap_at_entry", flush=True)

    labels, eligibility, quality_counts, quarantine_summary = apply_data_quality_quarantine(
        frozen["labels"], frozen["eligibility"], frozen["prices"], frozen["quality_counts"], config
    )
    print(f"[locked_test-features] quarantine: {quarantine_summary['data_quality_excluded_symbol_count']} symbol(s) excluded", flush=True)

    labels_with_splits = assign_temporal_splits(labels, config.splits)
    primary_labels, gap_diagnostics = exclude_target_already_reached_at_entry(labels_with_splits)
    print(f"[locked_test-features] target-already-reached-at-entry: {gap_diagnostics['excluded_row_count']} row(s) excluded", flush=True)

    locked_test = primary_labels[primary_labels["split"] == "locked_test"].copy()
    print(f"[locked_test-features] locked_test population: {len(locked_test)} rows, {locked_test['symbol'].nunique()} symbols", flush=True)

    print("[locked_test-features] fetching SPY for market context...", flush=True)
    spy_prices = get_stock_data("SPY", "5y")
    vix_close = None
    try:
        vix_raw = get_stock_data("^VIX", "5y")
        if vix_raw is not None and not vix_raw.empty:
            vix_close = vix_raw["Close"]
    except Exception:  # noqa: BLE001 - VIX is optional, regime falls back to realized vol
        vix_close = None
    market_context = compute_market_context(spy_prices, vix_close=vix_close)

    features = build_feature_dataset(locked_test, frozen["prices"], market_context, progress_every=args.progress_every)

    output_root = Path(args.output_dir)
    base_version = datetime.now(timezone.utc).strftime("swing20_locked_test_features_%Y%m%dT%H%M%SZ")
    snapshot_dir, dataset_version = _allocate_snapshot_dir(output_root, base_version)
    features_path = snapshot_dir / "features.parquet"
    write_frame(features, features_path, "parquet")

    lineage = build_lineage(args.dataset_dir, frozen["manifest"], features)
    lineage["dataset_version"] = dataset_version
    lineage["data_quality_quarantine"] = quarantine_summary
    lineage["target_already_reached_at_entry"] = {k: v for k, v in gap_diagnostics.items() if k not in ("excluded_by_symbol",)}
    lineage["split"] = "locked_test"
    lineage["artifact_hash"] = file_sha256(features_path)
    write_manifest(lineage, snapshot_dir / "manifest.json")

    print(f"[locked_test-features] wrote {features_path} ({len(features)} rows)", flush=True)
    print(json.dumps({"snapshot_dir": str(snapshot_dir), "dataset_version": dataset_version, "rows": len(features)}, indent=2))


if __name__ == "__main__":
    main()
