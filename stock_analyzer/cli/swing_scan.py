"""Prototype CLI: run the swing-trade Trade Score across one or more universes.

Usage:
    python -m stock_analyzer.cli.swing_scan --universes ai nuclear_energy --period 1y
"""

from __future__ import annotations

import argparse
from typing import Sequence

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.data_fetcher import get_stock_data
from stock_analyzer.data.universes import get_universe
from stock_analyzer.swing.support_zones import SupportZone
from stock_analyzer.swing.trade_score import calculate_trade_score

DEFAULT_PERIOD = "1y"


def _combined_symbols(universe_names: Sequence[str]) -> list[str]:
    seen: list[str] = []
    for name in universe_names:
        for symbol in get_universe(name):
            if symbol not in seen:
                seen.append(symbol)
    return seen


def scan(universe_names: Sequence[str], period: str = DEFAULT_PERIOD) -> list[dict[str, object]]:
    """Run the Trade Score across all symbols in the given universes."""

    results: list[dict[str, object]] = []
    for symbol in _combined_symbols(universe_names):
        try:
            raw = get_stock_data(symbol, period)
            enriched = calculate_indicators(raw)
            score_data = calculate_trade_score(enriched)
            results.append({"symbol": symbol, **score_data})
        except (ValueError, RuntimeError) as exc:
            results.append({"symbol": symbol, "error": str(exc)})

    results.sort(key=lambda r: r.get("trade_score", -1), reverse=True)
    return results


def _print_results(results: list[dict[str, object]]) -> None:
    print(f"{'Symbol':<8}{'Score':>7}  {'Class':<10}{'Nearest Support':<20}Top reasons")
    print("-" * 100)
    for r in results:
        if "error" in r:
            print(f"{r['symbol']:<8}{'ERR':>7}  {r['error']}")
            continue

        zone = r.get("nearest_support_zone")
        zone_str = f"{zone.low:.2f}-{zone.high:.2f}" if isinstance(zone, SupportZone) else "n/a"
        reasons = r.get("reasons", [])
        top_reasons = "; ".join(reasons[:2]) if isinstance(reasons, list) else ""
        print(f"{r['symbol']:<8}{r['trade_score']:>7}  {r['classification']:<10}{zone_str:<20}{top_reasons}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan universes with the swing-trade Trade Score.")
    parser.add_argument(
        "--universes",
        nargs="+",
        default=["ai", "nuclear_energy"],
        help="Universe keys from data/universes.py (default: ai nuclear_energy).",
    )
    parser.add_argument("--period", default=DEFAULT_PERIOD, help="Yahoo Finance period (default: 1y).")
    args = parser.parse_args(argv)

    results = scan(args.universes, args.period)
    _print_results(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
