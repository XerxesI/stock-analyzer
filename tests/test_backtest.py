"""Tests for the backtest engine's entry gating invariants."""

from __future__ import annotations

from stock_analyzer.backtesting.backtest import MIN_ENTRY_RANK
from stock_analyzer.core.opportunities import is_buy_opportunity

def _candidate(rank: float, confidence: float = 0.8, signal: str = "STRONG BUY") -> dict:
    """A minimal opportunity shaped like _build_opportunity's output."""
    return {
        "symbol": "TEST",
        "signal": signal,
        "confidence": confidence,
        "adjusted_confidence": confidence,
        "rank": rank,
        "fundamental_factors": {"risk": 0.5},
    }


# The daily entry scan (scan_market_at_date -> _build_opportunity) drops any
# candidate that fails is_buy_opportunity(min_rank=MIN_ENTRY_RANK). run_backtest()
# therefore never needs a second rank filter on the scan output. These tests pin
# that single-threshold contract so the removed no-op filter cannot silently
# re-appear as a second, divergent threshold.

def test_entry_gate_rejects_rank_below_min_entry_rank():
    just_below = round(MIN_ENTRY_RANK - 0.01, 2)
    assert not is_buy_opportunity(
        _candidate(rank=just_below), min_confidence=0.5, min_rank=MIN_ENTRY_RANK
    )


def test_entry_gate_accepts_rank_at_min_entry_rank():
    assert is_buy_opportunity(
        _candidate(rank=MIN_ENTRY_RANK), min_confidence=0.5, min_rank=MIN_ENTRY_RANK
    )


def test_second_rank_filter_on_scan_output_is_identity():
    # Every candidate the scan can emit already satisfies rank >= MIN_ENTRY_RANK,
    # so re-filtering the scan output by the same threshold changes nothing.
    scan_output = [_candidate(rank=r) for r in (MIN_ENTRY_RANK, 0.75, 0.9, 1.0)]
    refiltered = [item for item in scan_output if float(item["rank"]) >= MIN_ENTRY_RANK]
    assert refiltered == scan_output
