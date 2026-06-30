"""Shared service layer for filtering, ranking, and aggregating opportunities."""

from __future__ import annotations

from typing import Any, Sequence

from analysis_service import analyze_symbols_data
from opportunities import classify_opportunity, is_buy_opportunity, rank_opportunities
from strategy import apply_universe_weight


DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_MIN_RANK = 0.4


def select_buy_opportunities(
    results: Sequence[dict[str, Any]],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_rank: float = DEFAULT_MIN_RANK,
    min_fundamental_score: float | None = None,
    market: str | None = None,
    universe_category: str | None = None,
    weight_by_universe: bool = False,
) -> list[dict[str, Any]]:
    """Filter analysis results down to buy opportunities with optional metadata/weighting."""

    selected: list[dict[str, Any]] = []
    for item in results:
        if "error" in item:
            continue
        if not is_buy_opportunity(
            item,
            min_confidence=min_confidence,
            min_rank=min_rank,
            min_fundamental_score=min_fundamental_score,
        ):
            continue
        candidate = dict(item)
        if market is not None:
            candidate["market"] = market
        if universe_category is not None:
            candidate["universe_category"] = universe_category
            if weight_by_universe:
                base_rank = float(candidate.get("rank", 0) or 0)
                candidate["rank"] = base_rank * apply_universe_weight(1.0, universe_category)
        candidate["opportunity_type"] = classify_opportunity(candidate)
        selected.append(candidate)
    return selected


def deduplicate_by_symbol(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate symbols and keep the highest-ranked row for each."""

    seen: dict[str, dict[str, Any]] = {}
    for item in results:
        symbol = str(item.get("symbol", "")).upper()
        if not symbol:
            continue
        current = seen.get(symbol)
        if current is None or float(item.get("rank", 0) or 0) > float(current.get("rank", 0) or 0):
            seen[symbol] = dict(item)
    return list(seen.values())


def rank_buy_opportunities(
    items: Sequence[dict[str, Any]],
    top_n: int | None = None,
    deduplicate: bool = False,
) -> list[dict[str, Any]]:
    """Rank buy opportunities with optional deduplication and top-N limit."""

    ranked_input = deduplicate_by_symbol(items) if deduplicate else [dict(item) for item in items]
    ranked = rank_opportunities(ranked_input)
    if top_n is None:
        return ranked
    return ranked[:top_n]


def analyze_and_rank_opportunities(
    symbols: Sequence[str],
    period: str,
    top_n: int,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_rank: float = DEFAULT_MIN_RANK,
    min_fundamental_score: float | None = None,
    market: str | None = None,
    universe_category: str | None = None,
    weight_by_universe: bool = False,
) -> list[dict[str, Any]]:
    """Analyze a symbol list and return ranked buy opportunities."""

    results = analyze_symbols_data(symbols, period)
    selected = select_buy_opportunities(
        results,
        min_confidence=min_confidence,
        min_rank=min_rank,
        min_fundamental_score=min_fundamental_score,
        market=market,
        universe_category=universe_category,
        weight_by_universe=weight_by_universe,
    )
    return rank_buy_opportunities(selected, top_n=top_n)


def rank_analysis_results(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank non-error analysis rows by rank and confidence."""

    valid_rows = [item for item in results if "error" not in item]
    return sorted(
        valid_rows,
        key=lambda item: (
            float(item.get("rank", 0) or 0),
            float(item.get("confidence", 0) or 0),
        ),
        reverse=True,
    )
