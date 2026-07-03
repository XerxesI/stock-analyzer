"""Tests for portfolio construction, focused on the per-sector weight cap."""

from __future__ import annotations

import portfolio
from portfolio import (
    CASH_BUFFER,
    MAX_SECTOR_WEIGHT,
    _apply_sector_cap,
    _sector_key,
    build_portfolio,
)

EPS = 1e-6
# Final (post cash-buffer) ceiling for any single sector's combined weight.
FINAL_SECTOR_CEILING = MAX_SECTOR_WEIGHT * (1.0 - CASH_BUFFER)


def _opp(symbol: str, sector: str, rank: float = 0.7, confidence: float = 0.8) -> dict:
    """Build a synthetic opportunity that passes build_portfolio's selection filters."""
    return {
        "symbol": symbol,
        "rank": rank,
        "confidence": confidence,
        "market_bias": "bullish",
        "investment_type": "mixed",
        "fundamental_score": 0.6,
        "fundamental_sector": sector,
        "fundamental_factors": {"risk": 0.5},
    }


def _sector_totals(portfolio_positions: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for pos in portfolio_positions:
        if str(pos.get("symbol", "")).upper() == "CASH":
            continue
        totals[pos["sector"]] = totals.get(pos["sector"], 0.0) + float(pos.get("weight", 0) or 0)
    return totals


# --- direct unit tests of the water-filling cap ------------------------------

def test_apply_sector_cap_single_over_cap_redistributes():
    items = [
        {"symbol": "A", "sector": "tech", "weight": 0.20},
        {"symbol": "B", "sector": "tech", "weight": 0.20},
        {"symbol": "C", "sector": "health", "weight": 0.20},
        {"symbol": "D", "sector": "energy", "weight": 0.20},
        {"symbol": "E", "sector": "financial", "weight": 0.20},
    ]
    _apply_sector_cap(items, MAX_SECTOR_WEIGHT)
    totals: dict[str, float] = {}
    for it in items:
        totals[it["sector"]] = totals.get(it["sector"], 0.0) + it["weight"]
    assert totals["tech"] <= MAX_SECTOR_WEIGHT + EPS
    # Feasible case: freed weight is fully redistributed, so nothing leaks to cash.
    assert abs(sum(it["weight"] for it in items) - 1.0) < EPS


def test_apply_sector_cap_infeasible_leaves_excess_unallocated():
    # Three sectors that each want > cap: impossible to keep all <= cap at sum 1.0.
    items = [
        {"symbol": "A", "sector": "tech", "weight": 0.40},
        {"symbol": "B", "sector": "util", "weight": 0.40},
        {"symbol": "C", "sector": "health", "weight": 0.20},
    ]
    _apply_sector_cap(items, MAX_SECTOR_WEIGHT)
    totals: dict[str, float] = {}
    for it in items:
        totals[it["sector"]] = totals.get(it["sector"], 0.0) + it["weight"]
    # No sector may exceed the cap (this is exactly what the naive sketch got wrong).
    assert all(w <= MAX_SECTOR_WEIGHT + EPS for w in totals.values())
    # And the infeasible remainder is left unallocated rather than re-inflated.
    assert sum(it["weight"] for it in items) < 1.0 - EPS


def test_apply_sector_cap_all_same_sector_no_crash():
    items = [
        {"symbol": "A", "sector": "tech", "weight": 0.5},
        {"symbol": "B", "sector": "tech", "weight": 0.5},
    ]
    _apply_sector_cap(items, MAX_SECTOR_WEIGHT)
    total = sum(it["weight"] for it in items)
    assert total <= MAX_SECTOR_WEIGHT + EPS  # trimmed to cap, rest unallocated
    assert all(it["weight"] >= 0.0 and it["weight"] == it["weight"] for it in items)  # no NaN/negatives


# --- end-to-end build_portfolio scenarios ------------------------------------

def test_scenario_a_dominant_sector_capped():
    """One sector dominates; its final combined weight must respect the cap."""
    opps = [
        _opp("TECH1", "technology", rank=0.80),
        _opp("TECH2", "technology", rank=0.78),
        _opp("HLTH1", "healthcare", rank=0.70),
        _opp("ENRG1", "energy", rank=0.68),
        _opp("FIN1", "financial services", rank=0.66),
    ]
    result = build_portfolio(opps, max_positions=10)
    totals = _sector_totals(result)
    assert totals.get("technology", 0.0) <= FINAL_SECTOR_CEILING + EPS
    for sector, weight in totals.items():
        assert weight <= FINAL_SECTOR_CEILING + EPS, f"{sector}={weight} exceeds cap"
    assert abs(sum(float(p.get("weight", 0) or 0) for p in result) - 1.0) < 1e-3


def test_scenario_b_two_sectors_over_limit():
    """The real DUK/EXC/EIX shape: two sectors both start over the cap."""
    opps = [
        _opp("TECH1", "technology", rank=0.80),
        _opp("TECH2", "technology", rank=0.79),
        _opp("UTIL1", "utilities", rank=0.78),
        _opp("UTIL2", "utilities", rank=0.77),
        _opp("HLTH1", "healthcare", rank=0.70),
        _opp("ENRG1", "energy", rank=0.69),
    ]
    result = build_portfolio(opps, max_positions=10)
    totals = _sector_totals(result)
    assert totals.get("technology", 0.0) <= FINAL_SECTOR_CEILING + EPS
    assert totals.get("utilities", 0.0) <= FINAL_SECTOR_CEILING + EPS
    for sector, weight in totals.items():
        assert weight <= FINAL_SECTOR_CEILING + EPS, f"{sector}={weight} exceeds cap"


def test_scenario_c_all_same_sector_valid_output():
    """Extreme case: every candidate is one sector -> valid output, no crash/NaN."""
    opps = [_opp(f"T{i}", "technology", rank=0.80 - i * 0.01) for i in range(5)]
    result = build_portfolio(opps, max_positions=10)
    assert result, "expected a non-empty portfolio result"
    weights = [float(p.get("weight", 0) or 0) for p in result]
    assert all(w >= 0.0 and w == w for w in weights)  # no negatives / NaN
    assert sum(weights) <= 1.0 + 1e-3


def test_sector_key_does_not_use_universe_category():
    # Missing real sector must NOT masquerade as the universe bucket.
    item = {"symbol": "DUK", "fundamental_sector": None, "universe_category": "thematic"}
    assert _sector_key(item) == "unknown"
    # Real sector wins when present.
    item2 = {"symbol": "DUK", "fundamental_sector": "utilities", "universe_category": "thematic"}
    assert _sector_key(item2) == "utilities"
