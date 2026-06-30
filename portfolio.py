"""Portfolio construction helpers for ranked opportunities."""

from __future__ import annotations

from typing import Any, Sequence


MAX_PER_SECTOR = 2
MAX_POSITION_WEIGHT = 0.20
MAX_SECTOR_WEIGHT = 0.25
MIN_RANK = 0.45
CASH_BUFFER = 0.10
ELITE_RANK_THRESHOLD = 0.55
MIN_CONFIDENCE = 0.60
MIN_ACTIVE_POSITIONS = 3


def _sector_key(item: dict[str, Any]) -> str:
    sector = str(item.get("fundamental_sector") or item.get("sector") or item.get("universe_category") or "unknown")
    return sector.strip().lower() or "unknown"


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

    total_rank_sq = sum((float(item.get("rank", 0) or 0) ** 2) for item in selected)
    for item in selected:
        rank = float(item.get("rank", 0) or 0)
        base_weight = (rank**2) / total_rank_sq if total_rank_sq > 0 else 0.0
        risk = float((item.get("fundamental_factors") or {}).get("risk", 0.5) or 0.5)
        item["sector"] = _sector_key(item)
        item["weight"] = base_weight * _risk_multiplier(risk) * _type_multiplier(str(item.get("investment_type") or ""))

    for item in selected:
        if item["weight"] > MAX_POSITION_WEIGHT:
            item["weight"] = MAX_POSITION_WEIGHT

    total_weight = sum(float(item.get("weight", 0) or 0) for item in selected)
    for item in selected:
        item["weight"] = float(item.get("weight", 0) or 0) / total_weight if total_weight > 0 else 0.0

    sector_weights: dict[str, float] = {}
    for item in selected:
        sector = str(item.get("sector", "unknown"))
        sector_weights[sector] = sector_weights.get(sector, 0.0) + float(item.get("weight", 0) or 0)

    for item in selected:
        sector = str(item.get("sector", "unknown"))
        if sector_weights.get(sector, 0.0) > MAX_SECTOR_WEIGHT:
            reduction = MAX_SECTOR_WEIGHT / sector_weights[sector]
            item["weight"] *= reduction

    total_weight = sum(float(item.get("weight", 0) or 0) for item in selected)
    for item in selected:
        item["weight"] = float(item.get("weight", 0) or 0) / total_weight if total_weight > 0 else 0.0

    non_cash_total = sum(float(item.get("weight", 0) or 0) for item in selected)
    if non_cash_total > 0:
        scale = 1.0 - CASH_BUFFER
        for item in selected:
            item["weight"] = round(float(item.get("weight", 0) or 0) * scale, 4)
        selected.append(
            {
                "symbol": "CASH",
                "weight": round(CASH_BUFFER, 4),
                "rank": 0.0,
                "confidence": 0.0,
                "fundamental_score": 0.0,
                "investment_type": "cash",
                "sector": "cash",
                "fundamental_factors": {"risk": 0.0},
            }
        )

    total_weight = sum(float(item.get("weight", 0) or 0) for item in selected)
    for item in selected:
        item["weight"] = round(float(item.get("weight", 0) or 0) / total_weight, 4) if total_weight > 0 else 0.0
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
