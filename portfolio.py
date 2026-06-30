"""Portfolio construction helpers for ranked opportunities."""

from __future__ import annotations

from typing import Any, Sequence


MAX_PER_SECTOR = 2


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


def build_portfolio(opportunities: list[dict[str, Any]], max_positions: int = 10, debug: bool = False) -> list[dict[str, Any]]:
    """
    Build a portfolio from ranked opportunities.
    Returns list of positions with weights.
    """

    sorted_items = sorted((dict(item) for item in opportunities), key=lambda x: float(x.get("rank", 0) or 0), reverse=True)
    selected: list[dict[str, Any]] = []
    sector_counts: dict[str, int] = {}

    for item in sorted_items:
        if len(selected) >= max_positions:
            break
        sector = _sector_key(item)
        if sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
            continue
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        selected.append(item)

    if not selected:
        return []

    total_rank = sum(float(item.get("rank", 0) or 0) for item in selected)
    for item in selected:
        rank = float(item.get("rank", 0) or 0)
        base_weight = rank / total_rank if total_rank > 0 else 0.0
        risk = float((item.get("fundamental_factors") or {}).get("risk", 0.5) or 0.5)
        item["sector"] = _sector_key(item)
        item["weight"] = base_weight * _risk_multiplier(risk) * _type_multiplier(str(item.get("investment_type") or ""))

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
                f"sector={item.get('sector', 'unknown')}",
                f"risk={risk:.2f}",
                f"weight={float(item.get('weight', 0) or 0):.2%}",
            )

    return selected


def summarize_portfolio(portfolio: Sequence[dict[str, Any]]) -> dict[str, float | int]:
    if not portfolio:
        return {"positions": 0, "avg_rank": 0.0, "avg_confidence": 0.0, "avg_fundamental": 0.0}

    positions = len(portfolio)
    return {
        "positions": positions,
        "avg_rank": sum(float(x.get("rank", 0) or 0) for x in portfolio) / positions,
        "avg_confidence": sum(float(x.get("confidence", 0) or 0) for x in portfolio) / positions,
        "avg_fundamental": sum(float(x.get("fundamental_score", 0) or 0) for x in portfolio) / positions,
    }
