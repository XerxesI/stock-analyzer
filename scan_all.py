"""Scan all indices for buy opportunities with confidence filter."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Sequence

from analysis_service import DEFAULT_SCORING_MODE, SUPPORTED_SCORING_MODES, analyze_symbols_data
from opportunity_service import rank_buy_opportunities, select_buy_opportunities
from runtime_limits import UNIVERSE_SCAN_WORKERS
from universes import UNIVERSES, get_meta


DEFAULT_PERIOD = "1y"
DEFAULT_TOP = 20
DEFAULT_CONFIDENCE = 0.5
MAX_UNIVERSE_WORKERS = UNIVERSE_SCAN_WORKERS


def _scan_single_universe(
    market_name: str,
    symbols: Sequence[str],
    period: str,
    min_confidence: float,
    mode: str,
    min_growth_score: float | None,
    max_risk_score: float | None,
    debug: bool,
) -> dict[str, object]:
    """Analyze one universe and return filtered opportunities plus errors."""

    meta = get_meta(market_name)
    category = str(meta.get("category", "sector"))
    results = analyze_symbols_data(
        symbols,
        period,
        mode=mode,
        market=market_name,
        universe_category=category,
        debug=debug,
    )
    failed = [item for item in results if "error" in item]
    filtered = select_buy_opportunities(
        results,
        min_confidence=min_confidence,
        min_growth_score=min_growth_score,
        max_risk_score=max_risk_score,
        market=market_name,
        universe_category=category,
        weight_by_universe=True,
        debug=debug,
    )
    return {
        "market": market_name,
        "category": category,
        "filtered": filtered,
        "failed": failed,
    }


def run(
    min_confidence: float = DEFAULT_CONFIDENCE,
    top_n: int = DEFAULT_TOP,
    period: str = DEFAULT_PERIOD,
    mode: str = DEFAULT_SCORING_MODE,
    min_growth_score: float | None = None,
    max_risk_score: float | None = None,
    debug: bool = False,
) -> int:
    """Scan all indices and find buy opportunities with confidence filter."""

    all_results = []

    print(f"Scanning all {len(UNIVERSES)} indices (confidence >= {min_confidence})...\n")

    workers = min(MAX_UNIVERSE_WORKERS, len(UNIVERSES))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _scan_single_universe,
                market_name,
                symbols,
                period,
                min_confidence,
                mode,
                min_growth_score,
                max_risk_score,
                debug,
            ): market_name
            for market_name, symbols in UNIVERSES.items()
        }
        for future in as_completed(futures):
            market_name = futures[future]
            print(f"  Scanning {market_name.upper()}... ", end="", flush=True)
            try:
                scan_data = future.result()
                filtered = list(scan_data["filtered"])
                failed = list(scan_data["failed"])
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

    ranked = rank_buy_opportunities(all_results, top_n=top_n, deduplicate=True)

    print(f"\n{'=' * 90}")
    print(f"TOP BUY OPPORTUNITIES (All Indices, confidence >= {min_confidence}, deduplicated):")
    print(f"{'=' * 90}\n")

    for index, item in enumerate(ranked, start=1):
        market = item.get("market", "unknown")
        category = item.get("universe_category", "unknown")
        confidence = float(item.get("confidence", 0) or 0)
        rank_score = float(item.get("rank", 0) or 0)

        print(
            f"{index:2d}. {item['symbol']:8s} [{market:15s}] ({category:12s}) "
            f"-> {item['signal']:11s} (rank: {rank_score:.2f}, conf: {confidence:.2f}, "
            f"type: {item.get('opportunity_type', 'mixed')})"
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
    parser.add_argument(
        "--mode",
        default=DEFAULT_SCORING_MODE,
        choices=list(SUPPORTED_SCORING_MODES),
        help="Fundamentals scoring mode (growth, balanced, defensive, auto).",
    )
    parser.add_argument(
        "--min-growth-score",
        type=float,
        default=None,
        help="Optional minimum growth factor score (0.0-1.0).",
    )
    parser.add_argument(
        "--max-risk-score",
        type=float,
        default=None,
        help="Optional maximum risk factor score (0.0-1.0).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print per-symbol scoring debug lines.",
    )
    args = parser.parse_args(argv)

    if not (0.0 <= args.confidence <= 1.0):
        print("Error: confidence must be between 0.0 and 1.0.")
        return 1

    try:
        return run(
            args.confidence,
            args.top,
            args.period,
            args.mode,
            args.min_growth_score,
            args.max_risk_score,
            args.debug,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
