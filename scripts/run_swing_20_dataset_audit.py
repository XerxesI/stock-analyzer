"""Generate SWING_20 dataset audit artifacts.

Example:
    python scripts/run_swing_20_dataset_audit.py --symbols AAPL MSFT NVDA --period 5y
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stock_analyzer.data.data_fetcher import get_stock_data
from stock_analyzer.datasets.swing_20.audit import run_audit
from stock_analyzer.datasets.swing_20.config import Swing20Config
from stock_analyzer.evaluation.swing_20_dataset_audit_report import render_markdown


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate SWING_20 dataset audit artifacts.")
    parser.add_argument("--symbols", nargs="+", required=True, help="Ticker symbols to audit.")
    parser.add_argument("--period", default="5y", help="Yahoo Finance period, e.g. 5y.")
    parser.add_argument("--json-out", default="artifacts/swing_20_dataset_audit.json")
    parser.add_argument("--markdown-out", default="artifacts/swing_20_dataset_audit.md")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    price_data = {}
    for symbol in args.symbols:
        price_data[symbol.upper()] = get_stock_data(symbol, args.period)

    result = run_audit(price_data, config=Swing20Config())

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

