"""Opportunity finder CLI for surfacing the best buy setups."""

from __future__ import annotations

import argparse
from typing import Sequence

from opportunity_service import analyze_and_rank_opportunities
from universes import get_universe, get_universes_by_category, list_universes, UNIVERSES


DEFAULT_PERIOD = "1y"
DEFAULT_TOP = 10


def run(
    market: str | None = None,
    top_n: int = DEFAULT_TOP,
    period: str = DEFAULT_PERIOD,
    symbols: Sequence[str] | None = None,
    category: str | None = None,
) -> int:
    """Find and print the strongest buy opportunities for a universe or category."""

    if category:
        universe_names = get_universes_by_category(category)
        display_name = f"category '{category}'"
        all_symbols = []
        for u_name in universe_names:
            try:
                all_symbols.extend(get_universe(u_name))
            except ValueError:
                pass
        universe = all_symbols
    else:
        if market is None:
            market = "sp500"
        universe = get_universe(market, symbols)
        display_name = market.upper()

    ranked = analyze_and_rank_opportunities(universe, period, top_n=top_n)

    print(f"TOP BUY OPPORTUNITIES ({display_name}):")
    for index, item in enumerate(ranked, start=1):
        selection_mode = item.get("selection_mode")
        override_note = f", mode {selection_mode}" if selection_mode else ""
        print(
            f"{index}. {item['symbol']} -> {item['signal']} "
            f"(rank {float(item.get('rank', 0) or 0):.2f}, confidence {float(item.get('confidence', 0) or 0):.2f}, "
            f"type {item.get('opportunity_type', 'mixed')}{override_note})"
        )

    if not ranked:
        print("No buy opportunities matched the current filters.")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and run the finder."""

    parser = argparse.ArgumentParser(description="Find top buy opportunities in a market universe or category.")
    
    market_choices = list(UNIVERSES.keys()) + ["custom"]
    parser.add_argument(
        "--market",
        default=None,
        choices=market_choices,
        help="Market universe to scan (default: sp500 if --category not provided).",
    )
    
    category_choices = list(list_universes().keys())
    parser.add_argument(
        "--category",
        choices=category_choices,
        help="Filter by category (core, sector, thematic, experimental) to scan all indices in that category.",
    )
    
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help="How many opportunities to show (default: 10).",
    )
    parser.add_argument(
        "--period",
        default=DEFAULT_PERIOD,
        help="Yahoo Finance history period (default: 1y).",
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        help="Optional custom symbols when using --market custom.",
    )
    args = parser.parse_args(argv)

    if args.market == "custom" and not args.symbols:
        print("Error: provide at least one symbol when using --market custom.")
        return 1
    
    if args.market and args.category:
        print("Error: provide either --market or --category, not both.")
        return 1
    
    if not args.market and not args.category:
        args.market = "sp500"

    try:
        return run(args.market, args.top, args.period, args.symbols, args.category)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
