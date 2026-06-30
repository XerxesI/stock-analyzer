"""Scan all indices for buy opportunities with confidence filter."""

from __future__ import annotations

import argparse
from typing import Sequence

from analysis_service import analyze_symbols_data
from opportunities import classify_opportunity, is_buy_opportunity, rank_opportunities
from strategy import apply_universe_weight
from universes import UNIVERSES, get_meta


DEFAULT_PERIOD = "1y"
DEFAULT_TOP = 20
DEFAULT_CONFIDENCE = 0.5


def deduplicate_by_symbol(results: list[dict]) -> list[dict]:
    """Remove duplicate symbols, keeping the one with highest rank."""
    seen = {}
    for item in results:
        symbol = item.get("symbol")
        if symbol not in seen or float(item.get("rank", 0) or 0) > float(seen[symbol].get("rank", 0) or 0):
            seen[symbol] = item
    return list(seen.values())


def run(
    min_confidence: float = DEFAULT_CONFIDENCE,
    top_n: int = DEFAULT_TOP,
    period: str = DEFAULT_PERIOD,
) -> int:
    """Scan all indices and find buy opportunities with confidence filter."""

    all_results = []

    print(f"Scanning all {len(UNIVERSES)} indices (confidence >= {min_confidence})...\n")

    for market_name, symbols in UNIVERSES.items():
        print(f"  Scanning {market_name.upper()}... ", end="", flush=True)

        try:
            results = analyze_symbols_data(symbols, period)
            failed = [item for item in results if "error" in item]
            meta = get_meta(market_name)
            category = meta.get("category", "sector")
            filtered = [
                {
                    **item,
                    "market": market_name,
                    "universe_category": category,
                    "rank": float(item.get("rank", 0) or 0) * apply_universe_weight(1.0, category),
                }
                for item in results
                if "error" not in item
                and is_buy_opportunity(item)
                and float(item.get("confidence") or 0.0) >= min_confidence
            ]
            print(f"found {len(filtered)} opportunities")
            if failed:
                failed_symbols = ", ".join(str(item.get("symbol", "UNKNOWN")) for item in failed[:5])
                suffix = "..." if len(failed) > 5 else ""
                print(f"    skipped {len(failed)} symbol(s): {failed_symbols}{suffix}")
            all_results.extend(filtered)
        except (ValueError, RuntimeError) as exc:
            print(f"error: {exc}")

    if not all_results:
        print(f"\nNo buy opportunities with confidence >= {min_confidence} found.")
        return 0

    # Deduplicate by symbol, keeping highest ranked
    unique_results = deduplicate_by_symbol(all_results)
    ranked = rank_opportunities(unique_results)[:top_n]

    print(f"\n{'=' * 90}")
    print(f"TOP BUY OPPORTUNITIES (All Indices, confidence >= {min_confidence}, deduplicated):")
    print(f"{'=' * 90}\n")

    for index, item in enumerate(ranked, start=1):
        signal_type = classify_opportunity(item)
        market = item.get("market", "unknown")
        category = item.get("universe_category", "unknown")
        confidence = float(item.get("confidence", 0) or 0)
        rank_score = float(item.get("rank", 0) or 0)

        print(
            f"{index:2d}. {item['symbol']:8s} [{market:15s}] ({category:12s}) "
            f"-> {item['signal']:11s} (rank: {rank_score:.2f}, conf: {confidence:.2f})"
        )

    print(f"\n{'=' * 90}\n")
    print(f"Total opportunities: {len(ranked)} / {len(all_results)} found")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and run the scanner."""

    parser = argparse.ArgumentParser(
        description="Scan all market indices for buy opportunities with confidence filter."
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_CONFIDENCE,
        help=f"Minimum confidence threshold (0.0-1.0, default: {DEFAULT_CONFIDENCE}).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=f"How many opportunities to show (default: {DEFAULT_TOP}).",
    )
    parser.add_argument(
        "--period",
        default=DEFAULT_PERIOD,
        help=f"Yahoo Finance history period (default: {DEFAULT_PERIOD}).",
    )
    args = parser.parse_args(argv)

    if not (0.0 <= args.confidence <= 1.0):
        print("Error: confidence must be between 0.0 and 1.0.")
        return 1

    try:
        return run(args.confidence, args.top, args.period)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
