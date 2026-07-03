"""Portfolio construction helpers for ranked opportunities."""

from __future__ import annotations

import logging
from typing import Any, Sequence


LOGGER = logging.getLogger(__name__)

MAX_PER_SECTOR = 2
MAX_POSITION_WEIGHT = 0.20
MAX_SECTOR_WEIGHT = 0.25
# Intentionally stricter than opportunity_service.DEFAULT_MIN_RANK (0.40): the
# opportunity list is a broad watchlist filter, whereas a portfolio position needs
# higher conviction before we allocate capital to it. Keep these two in sync only
# on purpose — see opportunity_service.DEFAULT_MIN_RANK.
MIN_RANK = 0.45
CASH_BUFFER = 0.10
ELITE_RANK_THRESHOLD = 0.55
MIN_CONFIDENCE = 0.60
MIN_ACTIVE_POSITIONS = 3
USE_TOP_HEAVY = False


def _sector_key(item: dict[str, Any]) -> str:
    """Resolve the real GICS sector for concentration limits.

    Deliberately does NOT fall back to ``universe_category`` (e.g. "thematic",
    "sector"): a universe bucket is not a real sector, and using it as one masks
    true concentration risk (a utility from a "thematic" universe would otherwise
    look like its own sector instead of grouping with other utilities). When no
    real sector is available we return "unknown" so the exposure is visible and
    still counted against the per-sector caps.
    """

    sector = str(item.get("fundamental_sector") or item.get("sector") or "").strip().lower()
    return sector or "unknown"


def _risk_multiplier(risk: float) -> float:
    return 0.8 + (0.4 * max(0.0, min(1.0, risk)))


def _type_multiplier(investment_type: str | None) -> float:
    if investment_type == "high_conviction":
        return 1.2
    if investment_type == "short_term_trade":
        return 0.8
    return 1.0


def _is_bullish(item: dict[str, Any]) -> bool:
    return str(item.get("market_bias", item.get("market", ""))).lower().strip() == "bullish"


def _rank_spread_weight(rank: float, min_rank: float, max_rank: float) -> float:
    spread = max_rank - min_rank
    if spread <= 0:
        return 1.0
    return max(0.05, (rank - min_rank) / (spread + 1e-6))


def _apply_sector_cap(
    items: list[dict[str, Any]],
    max_sector_weight: float = MAX_SECTOR_WEIGHT,
    max_iterations: int = 10,
    epsilon: float = 1e-9,
) -> list[dict[str, Any]]:
    """Iteratively enforce the per-sector weight cap by water-filling.

    Over-cap sectors are trimmed to exactly ``max_sector_weight`` and the freed
    weight is redistributed only across sectors still under the cap (proportional
    to their current weight), never back onto the sectors we just trimmed. The
    process repeats because a redistribution can push a previously-safe sector
    over the cap.

    If the freed weight exceeds the total remaining headroom of the under-cap
    sectors (e.g. every sector wants more than the cap, which is infeasible while
    summing to 1.0), only the headroom is filled and the leftover is intentionally
    left unallocated (the caller routes it to cash) rather than re-inflating a
    capped sector to force ``sum == 1.0``.

    Note: freed weight is distributed proportional to each under-cap sector's
    *remaining headroom*, not its current weight. Weight-proportional
    redistribution (the obvious approach) overshoots and oscillates in tight
    cases, exiting with sectors still over the cap; headroom-proportional filling
    guarantees no under-cap sector crosses the cap, so it converges in one pass.
    """

    for _ in range(max_iterations):
        sector_weights: dict[str, float] = {}
        for item in items:
            sector = str(item.get("sector", "unknown"))
            sector_weights[sector] = sector_weights.get(sector, 0.0) + float(item.get("weight", 0) or 0)

        over_cap = {sector: weight for sector, weight in sector_weights.items() if weight > max_sector_weight + epsilon}
        if not over_cap:
            break

        excess = 0.0
        for item in items:
            sector = str(item.get("sector", "unknown"))
            if sector in over_cap:
                factor = max_sector_weight / sector_weights[sector]
                new_weight = float(item.get("weight", 0) or 0) * factor
                excess += float(item.get("weight", 0) or 0) - new_weight
                item["weight"] = new_weight

        headroom = {
            sector: max_sector_weight - weight
            for sector, weight in sector_weights.items()
            if sector not in over_cap and weight < max_sector_weight - epsilon
        }
        total_headroom = sum(headroom.values())
        if total_headroom <= epsilon or excess <= epsilon:
            # No safe headroom for the freed weight: leave it unallocated (-> cash).
            break

        distributable = min(excess, total_headroom)
        for sector, room in headroom.items():
            add = distributable * (room / total_headroom)
            current = sector_weights[sector]
            if current <= epsilon:
                continue
            grow = (current + add) / current
            for item in items:
                if str(item.get("sector", "unknown")) == sector:
                    item["weight"] = float(item.get("weight", 0) or 0) * grow
        if distributable >= excess - epsilon:
            # All freed weight placed within headroom; nothing left to iterate on.
            break

    return items


def build_portfolio(opportunities: list[dict[str, Any]], max_positions: int = 10, debug: bool = False) -> list[dict[str, Any]]:
    """
    Build a portfolio from ranked opportunities.
    Returns list of positions with weights.
    """

    sorted_items = sorted((dict(item) for item in opportunities), key=lambda x: float(x.get("rank", 0) or 0), reverse=True)
    elite = [item for item in sorted_items if float(item.get("rank", 0) or 0) >= ELITE_RANK_THRESHOLD]
    selected = elite if len(elite) >= MIN_ACTIVE_POSITIONS else sorted_items[:max_positions]
    selected = [item for item in selected if float(item.get("rank", 0) or 0) >= MIN_RANK]
    selected = [item for item in selected if float(item.get("confidence", 0) or 0) >= MIN_CONFIDENCE]
    selected = [item for item in selected if _is_bullish(item)]
    if len(selected) > max_positions:
        selected = selected[:max_positions]

    constrained: list[dict[str, Any]] = []
    sector_counts: dict[str, int] = {}
    for item in selected:
        sector = _sector_key(item)
        if sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
            continue
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        constrained.append(item)
    selected = constrained

    if len(selected) < MIN_ACTIVE_POSITIONS:
        return [
            {
                "symbol": "CASH",
                "weight": 1.0,
                "rank": 0.0,
                "confidence": 0.0,
                "fundamental_score": 0.0,
                "investment_type": "cash",
                "sector": "cash",
                "fundamental_factors": {"risk": 0.0},
            }
        ]

    max_rank = max(float(item.get("rank", 0) or 0) for item in selected)
    min_rank = min(float(item.get("rank", 0) or 0) for item in selected)
    spread_scores = [
        _rank_spread_weight(float(item.get("rank", 0) or 0), min_rank=min_rank, max_rank=max_rank)
        for item in selected
    ]
    total_spread = sum(spread_scores)
    for item in selected:
        rank = float(item.get("rank", 0) or 0)
        if USE_TOP_HEAVY:
            score = _rank_spread_weight(rank, min_rank=min_rank, max_rank=max_rank)
            base_weight = score / total_spread if total_spread > 0 else 0.0
        else:
            base_weight = 1.0 / len(selected)
        risk = float((item.get("fundamental_factors") or {}).get("risk", 0.5) or 0.5)
        item["sector"] = _sector_key(item)
        if item["sector"] == "unknown":
            LOGGER.warning(
                "No real sector for %s (fundamental_sector=%r, universe_category=%r); "
                "grouping under 'unknown' for concentration limits.",
                item.get("symbol", "?"),
                item.get("fundamental_sector"),
                item.get("universe_category"),
            )
        item["weight"] = base_weight * _risk_multiplier(risk) * _type_multiplier(str(item.get("investment_type") or ""))

    for item in selected:
        if item["weight"] > MAX_POSITION_WEIGHT:
            item["weight"] = MAX_POSITION_WEIGHT

    total_weight = sum(float(item.get("weight", 0) or 0) for item in selected)
    for item in selected:
        item["weight"] = float(item.get("weight", 0) or 0) / total_weight if total_weight > 0 else 0.0

    # Enforce the sector cap via water-filling. This must NOT be followed by a
    # global re-normalization to 1.0: doing so would proportionally re-inflate the
    # sectors we just trimmed straight back over the cap (the original bug).
    _apply_sector_cap(selected, MAX_SECTOR_WEIGHT)

    # Reserve the cash buffer and let any weight left unallocated by the sector cap
    # (extreme case: every sector at the cap) fall through to cash as well. Scaling
    # every equity weight by the same factor preserves the capped sector proportions,
    # and computing cash as the remainder avoids re-normalizing above the cap.
    non_cash_total = sum(float(item.get("weight", 0) or 0) for item in selected)
    if non_cash_total > 0:
        scale = 1.0 - CASH_BUFFER
        equity_total = 0.0
        for item in selected:
            scaled = float(item.get("weight", 0) or 0) * scale
            item["weight"] = scaled
            equity_total += scaled
        cash_weight = max(0.0, 1.0 - equity_total)
        if equity_total < scale - 1e-6:
            LOGGER.warning(
                "Equity allocation %.4f below target %.4f due to sector caps; routing remainder to cash.",
                equity_total,
                scale,
            )
        selected.append(
            {
                "symbol": "CASH",
                "weight": cash_weight,
                "rank": 0.0,
                "confidence": 0.0,
                "fundamental_score": 0.0,
                "investment_type": "cash",
                "sector": "cash",
                "fundamental_factors": {"risk": 0.0},
            }
        )

    for item in selected:
        item["weight"] = round(float(item.get("weight", 0) or 0), 4)
        item["rank"] = round(float(item.get("rank", 0) or 0), 2)
        item["confidence"] = round(float(item.get("confidence", 0) or 0), 2)
        item["fundamental_score"] = round(float(item.get("fundamental_score", 0) or 0), 2)
        item["investment_type"] = item.get("investment_type") or "mixed"

    if debug:
        for item in selected:
            risk = float((item.get("fundamental_factors") or {}).get("risk", 0.5) or 0.5)
            print(
                item["symbol"],
                f"rank={float(item.get('rank', 0) or 0):.2f}",
                f"sector={item.get('sector', 'unknown')}",
                f"risk={risk:.2f}",
                f"weight={float(item.get('weight', 0) or 0):.2%}",
            )

    return selected


def summarize_portfolio(portfolio: Sequence[dict[str, Any]]) -> dict[str, float | int]:
    if not portfolio:
        return {
            "positions": 0,
            "avg_rank": 0.0,
            "avg_confidence": 0.0,
            "avg_fundamental": 0.0,
            "portfolio_risk": 0.0,
            "diversification": 0.0,
        }

    positions = len([item for item in portfolio if str(item.get("symbol", "")).upper() != "CASH"])
    return {
        "positions": positions,
        "avg_rank": sum(float(x.get("rank", 0) or 0) for x in portfolio) / len(portfolio),
        "avg_confidence": sum(float(x.get("confidence", 0) or 0) for x in portfolio) / len(portfolio),
        "avg_fundamental": sum(float(x.get("fundamental_score", 0) or 0) for x in portfolio) / len(portfolio),
        "portfolio_risk": sum(
            float(pos.get("weight", 0) or 0) * (1.0 - float((pos.get("fundamental_factors") or {}).get("risk", 0.5) or 0.5))
            for pos in portfolio
        ),
        "diversification": (
            len({pos.get("sector") for pos in portfolio if str(pos.get("symbol", "")).upper() != "CASH"}) / positions
            if positions > 0
            else 0.0
        ),
    }
