"""Screen multiple symbols and rank the strongest signals."""

from __future__ import annotations

import argparse
from typing import Sequence

from stock_analyzer.services.analysis_service import analyze_symbols_data
from stock_analyzer.services.opportunity_service import rank_analysis_results

DEFAULT_PERIOD = "1y"
DEFAULT_SYMBOLS = ["AAPL", "MSFT", "TSLA", "NVDA", "AMZN"]


def run(symbols: Sequence[str], period: str = DEFAULT_PERIOD) -> int:
    """Analyze a set of symbols and print the strongest signals."""

    results = analyze_symbols_data(symbols, period)
    valid_results = rank_analysis_results(results)

    print("TOP SIGNALS TODAY:")
    for index, item in enumerate(valid_results, start=1):
        print(
            f"{index}. {item['symbol']} -> {item['signal']} "
            f"(rank {float(item.get('rank', 0) or 0):.2f}, confidence {float(item.get('confidence', 0) or 0):.2f})"
        )

    for item in results:
        if "error" in item:
            print(f"{item['symbol']} -> ERROR: {item['error']}")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the screener."""

    parser = argparse.ArgumentParser(description="Screen multiple stocks and rank their signals.")
    parser.add_argument(
        "symbols",
        nargs="*",
        default=DEFAULT_SYMBOLS,
        help="Stock ticker symbols, for example AAPL MSFT TSLA.",
    )
    parser.add_argument(
        "--period",
        default=DEFAULT_PERIOD,
        help="Yahoo Finance history period (default: 1y).",
    )
    args = parser.parse_args(argv)
    return run(args.symbols, args.period)


if __name__ == "__main__":
    raise SystemExit(main())
