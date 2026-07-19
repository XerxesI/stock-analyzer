"""Tests for EXP-005's NO_CAPACITY opportunity-cost diagnostics -- Revision 5,
Section 24, Stage 13.

The fake portfolio/sandbox repos below deliberately define ONLY read methods (no
insert_admission/insert_reservation/append_execution/etc.) -- if
compute_opportunity_cost ever attempted a write, it would raise AttributeError
immediately. Every test below calls it to completion without error, which is
itself the "strictly observational, never writes" proof the module docstring
promises.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import FILLED_AT_OPEN
from stock_analyzer.sandbox.exp005.diagnostics._shared import END_OF_EXPERIMENT
from stock_analyzer.sandbox.exp005.diagnostics.opportunity_cost import (
    OpportunityCostComputationError,
    compute_opportunity_cost,
)
from stock_analyzer.sandbox.exp005.domain.admission import ACCEPTED, NO_CAPACITY, PortfolioAdmission, SlotReservation
from stock_analyzer.sandbox.exp005.domain.equity_snapshot import PortfolioEquitySnapshot

REPLAY_ID = "replay-1"
NOW = datetime.now(timezone.utc)
SYMBOL = "AAA"


def _bar(symbol: str, d: date, o: float, h: float, low: float, c: float) -> dict:
    return {"symbol": symbol, "date": pd.Timestamp(d), "Open": o, "High": h, "Low": low, "Close": c}


STANDARD_PRICES = pd.DataFrame(
    [
        _bar(SYMBOL, date(2026, 1, 5), 10.0, 10.2, 9.8, 10.0),
        _bar(SYMBOL, date(2026, 1, 6), 10.05, 10.6, 9.9, 10.3),
        _bar(SYMBOL, date(2026, 1, 7), 10.3, 11.0, 10.1, 10.8),
        _bar(SYMBOL, date(2026, 1, 8), 10.8, 12.0, 10.6, 11.5),
        _bar(SYMBOL, date(2026, 1, 9), 11.5, 11.6, 11.2, 11.4),
        _bar(SYMBOL, date(2026, 1, 10), 11.4, 11.5, 11.0, 11.3),
    ]
)
STANDARD_CALENDAR = tuple(sorted(pd.to_datetime(STANDARD_PRICES["date"]).dt.date))


class _FakeManifest:
    def __init__(self, outcome_data_end_date: date) -> None:
        self.outcome_data_end_date = outcome_data_end_date


class _FakePortfolioRepo:
    def __init__(self, snapshot: PortfolioEquitySnapshot | None, reservations: list[SlotReservation]) -> None:
        self._snapshot = snapshot
        self._reservations = reservations

    def get_equity_snapshot(self, replay_id: str, as_of_date: date) -> PortfolioEquitySnapshot | None:
        return self._snapshot

    def list_reservations_for_experiment(self, replay_id: str) -> list[SlotReservation]:
        return list(self._reservations)


class _FakeSandboxRepo:
    def __init__(self, candidates: dict[str, RankedCandidate]) -> None:
        self._candidates = candidates

    def get_candidate(self, candidate_id: str) -> RankedCandidate | None:
        return self._candidates.get(candidate_id)


class _FakeContext:
    def __init__(
        self, prices_df: pd.DataFrame, outcome_data_end_date: date,
        candidates: dict[str, RankedCandidate] | None = None,
        snapshot: PortfolioEquitySnapshot | None = None, reservations: list[SlotReservation] | None = None,
    ) -> None:
        self.manifest = _FakeManifest(outcome_data_end_date)
        self.replay_id = REPLAY_ID
        self.prices_df = prices_df
        self.portfolio_repo = _FakePortfolioRepo(snapshot, reservations or [])
        self.sandbox_repo = _FakeSandboxRepo(candidates or {})


def _candidate(candidate_id: str, signal_close: float, max_entry_price: float | None) -> RankedCandidate:
    return RankedCandidate(
        candidate_id=candidate_id, run_id="run-1", as_of_date=date(2026, 1, 5), symbol=SYMBOL, daily_rank=11,
        model_score=0.4, signal_close=signal_close, atr14=0.5, max_entry_price=max_entry_price, shadow_top10=False,
        actionable=True, exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
    )


def _admission(candidate_id: str, as_of_date: date, decision: str = NO_CAPACITY) -> PortfolioAdmission:
    return PortfolioAdmission(
        admission_id=candidate_id, replay_id=REPLAY_ID, candidate_id=candidate_id, symbol=SYMBOL,
        as_of_date=as_of_date, decision=decision, rank_at_admission=11,
        slot_budget_units=1_000_000 if decision == ACCEPTED else None,
        reason=None if decision == ACCEPTED else "10/10 slots reserved", created_at=NOW,
    )


def _reservation(reservation_id: str, created_at: datetime, resolved_at: datetime | None) -> SlotReservation:
    return SlotReservation(
        reservation_id=reservation_id, replay_id=REPLAY_ID, admission_id=reservation_id, candidate_id=reservation_id,
        symbol="ZZZ", reserved_amount_units=500_000, status="RESERVED", created_at=created_at, resolved_at=resolved_at,
    )


def test_hand_computed_horizons_occupying_reservations_and_hypothetical_fill():
    candidate_id = "c1"
    admission = _admission(candidate_id, date(2026, 1, 5))
    candidates = {candidate_id: _candidate(candidate_id, 10.0, 10.1)}
    snapshot = PortfolioEquitySnapshot(
        snapshot_id=f"{REPLAY_ID}:2026-01-05", replay_id=REPLAY_ID, as_of_date=date(2026, 1, 5),
        cash_units=1_000_000, reserved_capital_units=8_000_000, open_position_market_value_units=0,
        total_equity_units=10_000_000, open_position_count=8, reserved_order_count=2,
        cumulative_commissions_units=100, cumulative_slippage_cost_units=500, created_at=NOW,
    )
    tz = timezone.utc
    reservations = [
        _reservation("r1_occupying", datetime(2026, 1, 4, tzinfo=tz), None),
        _reservation("r2_resolved_before", datetime(2026, 1, 3, tzinfo=tz), datetime(2026, 1, 4, tzinfo=tz)),
        _reservation("r3_created_after", datetime(2026, 1, 6, tzinfo=tz), None),
    ]
    context = _FakeContext(STANDARD_PRICES, date(2026, 1, 10), candidates=candidates, snapshot=snapshot, reservations=reservations)

    result = compute_opportunity_cost(context, admission, STANDARD_CALENDAR)

    assert result.rank_at_admission == 11
    assert result.signal_close == pytest.approx(10.0)
    assert result.open_position_count == 8
    assert result.reserved_order_count == 2
    assert [r.reservation_id for r in result.occupying_reservations] == ["r1_occupying"]

    assert result.hypothetical_would_have_filled is True
    assert result.hypothetical_fill_date == date(2026, 1, 6)
    assert result.hypothetical_fill_reason == FILLED_AT_OPEN
    assert result.hypothetical_raw_fill_price == pytest.approx(10.05)

    h1 = result.horizons[0]
    assert h1.is_censored is False
    assert h1.mfe_price == pytest.approx(10.6)
    assert h1.mfe_pct == pytest.approx(0.06)
    assert h1.mae_price == pytest.approx(9.9)
    assert h1.mae_pct == pytest.approx(-0.01)
    assert h1.forward_return_pct == pytest.approx(0.03)

    h5 = result.horizons[1]
    assert h5.is_censored is False
    assert h5.sessions_observed == 5
    assert h5.mfe_price == pytest.approx(12.0)
    assert h5.forward_return_pct == pytest.approx(0.13)

    h10 = result.horizons[2]
    assert h10.is_censored is True
    assert h10.censoring_reason == END_OF_EXPERIMENT
    assert h10.sessions_observed == 5

    h20 = result.horizons[3]
    assert h20.is_censored is True
    assert h20.sessions_observed == 5


def test_missing_equity_snapshot_gives_none_not_zero():
    candidate_id = "c2"
    admission = _admission(candidate_id, date(2026, 1, 5))
    candidates = {candidate_id: _candidate(candidate_id, 10.0, 10.1)}
    context = _FakeContext(STANDARD_PRICES, date(2026, 1, 10), candidates=candidates, snapshot=None)

    result = compute_opportunity_cost(context, admission, STANDARD_CALENDAR)

    assert result.open_position_count is None
    assert result.reserved_order_count is None


def test_no_fill_within_validity_window():
    candidate_id = "c3"
    admission = _admission(candidate_id, date(2026, 1, 5))
    candidates = {candidate_id: _candidate(candidate_id, 10.0, 9.0)}  # ceiling too low, never touched
    context = _FakeContext(STANDARD_PRICES, date(2026, 1, 10), candidates=candidates)

    result = compute_opportunity_cost(context, admission, STANDARD_CALENDAR)

    assert result.hypothetical_would_have_filled is False
    assert result.hypothetical_fill_date is None
    assert result.hypothetical_raw_fill_price is None


def test_none_max_entry_price_never_fills_and_does_not_raise():
    candidate_id = "c4"
    admission = _admission(candidate_id, date(2026, 1, 5))
    candidates = {candidate_id: _candidate(candidate_id, 10.0, None)}
    context = _FakeContext(STANDARD_PRICES, date(2026, 1, 10), candidates=candidates)

    result = compute_opportunity_cost(context, admission, STANDARD_CALENDAR)

    assert result.hypothetical_would_have_filled is False


def test_missing_candidate_raises():
    admission = _admission("ghost", date(2026, 1, 5))
    context = _FakeContext(STANDARD_PRICES, date(2026, 1, 10), candidates={})

    with pytest.raises(OpportunityCostComputationError):
        compute_opportunity_cost(context, admission, STANDARD_CALENDAR)


def test_non_no_capacity_admission_raises():
    candidate_id = "c5"
    admission = _admission(candidate_id, date(2026, 1, 5), decision=ACCEPTED)
    candidates = {candidate_id: _candidate(candidate_id, 10.0, 10.1)}
    context = _FakeContext(STANDARD_PRICES, date(2026, 1, 10), candidates=candidates)

    with pytest.raises(OpportunityCostComputationError):
        compute_opportunity_cost(context, admission, STANDARD_CALENDAR)
