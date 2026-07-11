"""Generate SWING_20 dataset audit artifacts.

Example:
    python scripts/run_swing_20_dataset_audit.py --symbols AAPL MSFT NVDA --period 5y
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_analyzer.data.data_fetcher import get_stock_data
from stock_analyzer.datasets.swing_20.audit import run_audit, run_audit_from_frames
from stock_analyzer.datasets.swing_20.config import Swing20Config
from stock_analyzer.datasets.swing_20.prepare import load_frozen_dataset
from stock_analyzer.evaluation.swing_20_dataset_audit_report import render_markdown


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate SWING_20 dataset audit artifacts.")
    parser.add_argument("--symbols", nargs="+", help="Ticker symbols to audit.")
    parser.add_argument(
        "--dataset-dir",
        help="Optional frozen dataset directory created by scripts/prepare_swing_20_dataset.py.",
    )
    parser.add_argument("--period", default="5y", help="Yahoo Finance period, e.g. 5y.")
    parser.add_argument("--json-out", default="artifacts/swing_20_dataset_audit.json")
    parser.add_argument("--markdown-out", default="artifacts/swing_20_dataset_audit.md")
    args = parser.parse_args()
    if not args.dataset_dir and not args.symbols:
        parser.error("Provide either --dataset-dir or --symbols.")
    return args


def main() -> None:
    args = parse_args()
    config = Swing20Config()
    if args.dataset_dir:
        frozen = load_frozen_dataset(Path(args.dataset_dir))
        result = run_audit_from_frames(
            labels=frozen["labels"],
            eligibility=frozen["eligibility"],
            quality_counts=frozen["quality_counts"],
            config=config,
        )
    else:
        price_data = {}
        for symbol in args.symbols:
            price_data[symbol.upper()] = get_stock_data(symbol, args.period)
        result = run_audit(price_data, config=config)

    json_path = Path(args.json_out)
    markdown_path = Path(args.markdown_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
    markdown_path.write_text(render_markdown(result), encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {markdown_path}")
    print(f"Decision: {result.decision.status}")


if __name__ == "__main__":
    main()
