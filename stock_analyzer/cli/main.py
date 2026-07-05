"""CLI entry point for stock analysis."""

from __future__ import annotations

import argparse
from typing import Sequence

from stock_analyzer.services.analysis_service import (    DEFAULT_PERIOD,
    DEFAULT_SCORING_MODE,
    SUPPORTED_SCORING_MODES,
    analyze_symbol_data,
    confidence_interpretation,
)

def _format_value(value: float | None, precision: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{precision}f}"


def _print_result(symbol: str, signal_data: dict[str, object], explanation: str) -> None:
    print("===================================")
    print(f"Symbol: {symbol.upper()}")
    print(f"Price: {_format_value(signal_data.get('price'))}")
    print(f"RSI: {_format_value(signal_data.get('rsi'))}")
    print(f"SMA50: {_format_value(signal_data.get('sma50'))}")
    print(f"SMA200: {_format_value(signal_data.get('sma200'))}")
    print(f"MACD: {_format_value(signal_data.get('macd'))}")
    print(f"Volume: {_format_value(signal_data.get('volume'), 0)}")
    print(f"Score: {signal_data.get('score', 0)}")
    confidence = _format_value(signal_data.get("confidence"), 2)
    adjusted_confidence = _format_value(signal_data.get("adjusted_confidence"), 2)
    confidence_label = signal_data.get("confidence_label", "low")
    print(f"Confidence: {confidence} ({confidence_label})")
    print(f"Adjusted confidence: {adjusted_confidence}")
    print(f"Interpretation: {confidence_interpretation(str(confidence_label))}")
    print(f"Trend: {signal_data.get('trend_strength', 'unknown')}")
    print(f"Market: {signal_data.get('market_bias', 'neutral')}")
    print(f"Technical rank: {_format_value(signal_data.get('technical_rank'), 2)}")
    print(f"Fundamental score: {_format_value(signal_data.get('fundamental_score'), 2)}")
    print(f"Fundamental completeness: {_format_value(signal_data.get('fundamental_completeness'), 2)}")
    print(f"Fundamental bias: {signal_data.get('fundamental_bias', 'neutral')}")
    print(f"Rank: {_format_value(signal_data.get('rank'), 2)}")
    print(f"Type: {signal_data.get('opportunity_type', 'mixed')}")
    print(f"Signal: {signal_data.get('signal', 'HOLD')}")
    print("-----------------------------------")
    print("Explanation:")
    print(explanation)
    print("===================================")


def analyze_symbol(symbol: str, period: str = DEFAULT_PERIOD, mode: str = DEFAULT_SCORING_MODE, debug: bool = False) -> None:
    """Run the full analysis pipeline for a single symbol and print the result."""

    result = analyze_symbol_data(symbol, period, mode=mode, debug=debug)
    _print_result(symbol, result, str(result["explanation"]))


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and execute the CLI."""

    parser = argparse.ArgumentParser(description="Analyze stock signals from Yahoo Finance.")
    parser.add_argument("symbols", nargs="+", help="Stock ticker symbols, for example AAPL MSFT.")
    parser.add_argument(
        "--period",
        default=DEFAULT_PERIOD,
        help="Yahoo Finance history period (default: 1y).",
    )
    parser.add_argument(
        "--mode",
        default=DEFAULT_SCORING_MODE,
        choices=list(SUPPORTED_SCORING_MODES),
        help="Fundamentals scoring mode (growth, balanced, defensive, auto).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print per-symbol scoring debug lines.",
    )
    args = parser.parse_args(argv)

    exit_code = 0
    for symbol in args.symbols:
        try:
            analyze_symbol(symbol, args.period, mode=args.mode, debug=args.debug)
        except (ValueError, RuntimeError) as exc:
            exit_code = 1
            print("===================================")
            print(f"Symbol: {symbol.upper()}")
            print(f"Error: {exc}")
            print("===================================")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
