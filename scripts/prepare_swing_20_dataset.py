"""Prepare frozen SWING_20 dataset artifacts.

Examples:
    python scripts/prepare_swing_20_dataset.py --symbols AAPL MSFT NVDA --format csv
    python scripts/prepare_swing_20_dataset.py --universe full_us --max-symbols 250 --period 5y
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_analyzer.datasets.swing_20.artifacts import StorageFormat
from stock_analyzer.datasets.swing_20.config import Swing20Config
from stock_analyzer.datasets.swing_20.prepare import prepare_frozen_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare frozen SWING_20 dataset artifacts.")
    parser.add_argument(
        "--universe",
        choices=["full_us"],
        default="full_us",
        help="Universe source to freeze when --symbols is not supplied.",
    )
    parser.add_argument("--symbols", nargs="+", help="Optional ticker symbols for a custom diagnostic universe.")
    parser.add_argument("--period", default="5y", help="Yahoo Finance period, e.g. 5y.")
    parser.add_argument("--output-dir", default="artifacts/swing_20", help="Dataset artifact directory.")
    parser.add_argument(
        "--format",
        choices=["parquet", "csv"],
        default="parquet",
        help="Frozen artifact storage format.",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        help="Optional cap for diagnostic runs. Applied as a deterministic seeded random "
        "sample of the resolved universe, not a positional head() cut.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for the deterministic --max-symbols sample (default: 42).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = prepare_frozen_dataset(
        symbols=args.symbols,
        universe_source=args.universe,
        period=args.period,
        output_dir=Path(args.output_dir),
        storage_format=args.format,
        config=Swing20Config(),
        max_symbols=args.max_symbols,
        seed=args.seed,
    )

    print(json.dumps(
        {
            "snapshot_dir": manifest.get("snapshot_dir"),
            "manifest": manifest.get("manifest"),
            "dataset_version": manifest.get("dataset_version"),
            "storage_format": manifest.get("storage_format"),
            "universe_source": manifest.get("universe_source"),
            "sample_seed": manifest.get("sample_seed"),
            "symbol_count_requested": manifest.get("symbol_count_requested"),
            "symbol_count_with_prices": manifest.get("symbol_count_with_prices"),
            "symbol_count_failed": manifest.get("symbol_count_failed"),
            "symbols_without_prices": len(manifest.get("symbols_without_prices", [])),
        },
        indent=2,
    ))
    print(f"\nPass this snapshot to the audit script:\n  --dataset-dir {manifest.get('snapshot_dir')}")


if __name__ == "__main__":
    main()
