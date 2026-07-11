"""Prepare frozen SWING_20 dataset artifacts.

Examples:
    python scripts/prepare_swing_20_dataset.py --symbols AAPL MSFT NVDA --format csv
    python scripts/prepare_swing_20_dataset.py --max-symbols 250 --period 5y
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
    parser.add_argument("--symbols", nargs="+", help="Optional ticker symbols. Defaults to the full US universe.")
    parser.add_argument("--period", default="5y", help="Yahoo Finance period, e.g. 5y.")
    parser.add_argument("--output-dir", default="artifacts/swing_20", help="Dataset artifact directory.")
    parser.add_argument(
        "--format",
        choices=["parquet", "csv"],
        default="parquet",
        help="Frozen artifact storage format.",
    )
    parser.add_argument("--max-symbols", type=int, help="Optional cap for diagnostic runs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = prepare_frozen_dataset(
        symbols=args.symbols,
        period=args.period,
        output_dir=Path(args.output_dir),
        storage_format=args.format,
        config=Swing20Config(),
        max_symbols=args.max_symbols,
    )

    print(json.dumps(
        {
            "manifest": manifest.get("manifest"),
            "storage_format": manifest.get("storage_format"),
            "symbol_count_requested": manifest.get("symbol_count_requested"),
            "symbol_count_with_prices": manifest.get("symbol_count_with_prices"),
            "symbols_without_prices": len(manifest.get("symbols_without_prices", [])),
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
