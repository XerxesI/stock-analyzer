"""Tests for EXP-005's NO_CAPACITY opportunity-cost diagnostics -- Revision 5,
Section 24, Stage 13 (corrected in the Stage 11-15 closure cycle, finding 3:
capacity occupancy is reconstructed from LOGICAL replay event dates, never
wall-clock created_at/resolved_at timestamps).

Real SandboxRepository/PortfolioRepository backed by a real, FK-enforced SQLite
connection are used throughout (rather than duck-typed fakes) since occupancy
reconstruction now reads across admissions/orders/attempts/positions/reservations
-- exactly the kind of multi-table join that's easy to get subtly wrong with a
hand-maintained fake and that a real schema catches for free.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import FILLED_AT_OPEN, EntryOrder, EntryOrderAttempt
from stock_analyzer.sandbox.domain.position import CLOSED, OPEN, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import SELL_TARGET
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.exp005.diagnostics._shared import END_OF_EXPERIMENT, full_market_calendar
from stock_analyzer.sandbox.exp005.diagnostics.opportunity_cost import (
    CapacityOccupancyReconciliationError,
    OpportunityCostComputationError,
    compute_opportunity_cost,
)
from stock_analyzer.sandbox.exp005.domain.admission import ACCEPTED, NO_CAPACITY, PortfolioAdmission, SlotReservation
from stock_analyzer.sandbox.exp005.domain.equity_snapshot import PortfolioEquitySnapshot
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

REPLAY_ID = "replay-1"
NOW = datetime.now(timezone.utc)


def _bar(symbol: str, d: date, o: float, h: float, low: float, c: float) -> dict:
    return {"symbol": symbol, "date": pd.Timestamp(d), "Open": o, "High": h, "Low": low, "Close": c}


class _FakeManifest:
    def __init__(self, outcome_data_end_date: date) -> None:
        self.outcome_data_end_date = outcome_data_end_date


class _FakeContext:
    def __init__(self, prices_df: pd.DataFrame, outcome_data_end_date: date, sandbox_repo, portfolio_repo) -> None:
        self.manifest = _FakeManifest(outcome_data_end_date)
        self.replay_id = REPLAY_ID
        self.prices_df = prices_df
        self.portfolio_repo = portfolio_repo
        self.sandbox_repo = sandbox_repo


def _repos():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    return SandboxRepository(conn), PortfolioRepository(conn)


def _insert_candidate(sandbox_repo, candidate_id, symbol, as_of_date, rank, signal_close=10.0, max_entry_price=10.1):
    run_id = f"run-{as_of_date.isoformat()}"
    sandbox_repo.create_run(
        SandboxRun(run_id=run_id, as_of_date=as_of_date, command="generate-candidates", started_at=NOW, configuration_hash="test")
    )
    candidate = RankedCandidate(
        candidate_id=candidate_id, run_id=run_id, as_of_date=as_of_date, symbol=symbol, daily_rank=rank,
        model_score=0.5, signal_close=signal_close, atr14=0.5, max_entry_price=max_entry_price, shadow_top10=True,
        actionable=True, exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
    )
    sandbox_repo.insert_ranked_candidate(candidate)
    return candidate


def _insert_admission(portfolio_repo, candidate, as_of_date, rank, decision, created_at=NOW):
    admission = PortfolioAdmission(
        admission_id=candidate.candidate_id, replay_id=REPLAY_ID, candidate_id=candidate.candidate_id,
        symbol=candidate.symbol, as_of_date=as_of_date, decision=decision, rank_at_admission=rank,
        slot_budget_units=1_000_000 if decision == ACCEPTED else None,
        reason=None if decision == ACCEPTED else "no free slots", created_at=created_at,
    )
    portfolio_repo.insert_admission(admission)
    return admission


def _insert_reservation(portfolio_repo, admission, created_at=NOW, resolved_at=None, status="RESERVED"):
    reservation = SlotReservation(
        reservation_id=f"{admission.admission_id}:reservation", replay_id=REPLAY_ID, admission_id=admission.admission_id,
        candidate_id=admission.candidate_id, symbol=admission.symbol, reserved_amount_units=1_000_000,
        status=status, created_at=created_at, resolved_at=resolved_at,
    )
    portfolio_repo.insert_reservation(reservation)
    return reservation


def _insert_filled_order_and_position(sandbox_repo, candidate, fill_date, status=OPEN, exit_date=None):
    order = EntryOrder(
        order_id=EntryOrder.make_id(candidate.candidate_id), candidate_id=candidate.candidate_id,
        symbol=candidate.symbol, signal_date=candidate.as_of_date, created_date=candidate.as_of_date,
        valid_until=fill_date, max_entry_price=candidate.max_entry_price, status="FILLED",
        fill_date=fill_date, fill_price=candidate.signal_close, fill_reason=FILLED_AT_OPEN,
    )
    sandbox_repo.create_entry_order(order)
    position = VirtualPosition(
        position_id=VirtualPosition.make_id(candidate.symbol, fill_date), symbol=candidate.symbol,
        candidate_id=candidate.candidate_id, order_id=order.order_id, signal_date=candidate.as_of_date,
        entry_date=fill_date, entry_price=candidate.signal_close, quantity=10.0, initial_rank=candidate.daily_rank,
        initial_model_score=0.5, signal_close=candidate.signal_close, max_entry_price=candidate.max_entry_price,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=candidate.signal_close * 1.2,
        planned_time_exit_date=fill_date, status=status, exit_date=exit_date,
        exit_price=(candidate.signal_close * 1.1 if exit_date else None), exit_reason=(SELL_TARGET if exit_date else None),
    )
    sandbox_repo.create_position(position)
    return order, position


def _insert_pending_order(sandbox_repo, candidate):
    """A real ACCEPTED admission always gets a matching entry_orders row created
    atomically alongside its reservation (Section 8.2) -- these test fixtures
    mirror that even when the order's own eventual fill/expiry is irrelevant to
    the scenario under test, so occupancy reconstruction (which looks up the
    order, not just the reservation) has something to find."""

    order = EntryOrder(
        order_id=EntryOrder.make_id(candidate.candidate_id), candidate_id=candidate.candidate_id,
        symbol=candidate.symbol, signal_date=candidate.as_of_date, created_date=candidate.as_of_date,
        valid_until=candidate.as_of_date, max_entry_price=candidate.max_entry_price, status="PENDING",
    )
    sandbox_repo.create_entry_order(order)
    return order


def _insert_equity_snapshot(portfolio_repo, as_of_date, open_position_count, reserved_order_count):
    portfolio_repo.append_equity_snapshot(
        PortfolioEquitySnapshot(
            snapshot_id=f"{REPLAY_ID}:{as_of_date.isoformat()}", replay_id=REPLAY_ID, as_of_date=as_of_date,
            cash_units=1_000_000, reserved_capital_units=0, open_position_market_value_units=0,
            total_equity_units=1_000_000, open_position_count=open_position_count,
            reserved_order_count=reserved_order_count, cumulative_commissions_units=0,
            cumulative_slippage_cost_units=0, created_at=NOW,
        )
    )


PRICES = pd.DataFrame(
    [
        _bar("AAA", date(2026, 1, 5), 10.0, 10.2, 9.8, 10.0),
        _bar("AAA", date(2026, 1, 6), 10.05, 10.6, 9.9, 10.3),
        _bar("AAA", date(2026, 1, 7), 10.3, 11.0, 10.1, 10.8),
        _bar("AAA", date(2026, 1, 8), 10.8, 12.0, 10.6, 11.5),
        _bar("AAA", date(2026, 1, 9), 11.5, 11.6, 11.2, 11.4),
        _bar("AAA", date(2026, 1, 10), 11.4, 11.5, 11.0, 11.3),
        _bar("BBB", date(2026, 1, 5), 20.0, 20.2, 19.8, 20.0),
        _bar("BBB", date(2026, 1, 6), 20.05, 20.6, 19.9, 20.3),
        _bar("CCC", date(2026, 1, 5), 30.0, 30.2, 29.8, 30.0),
        _bar("CCC", date(2026, 1, 6), 30.05, 30.6, 29.9, 30.3),
    ]
)
CALENDAR = full_market_calendar(PRICES)


def test_reservation_then_position_occupancy_from_logical_dates():
    sandbox_repo, portfolio_repo = _repos()
    # AAA: rank 1, ACCEPTED on Jan 5, order fills Jan 6 (entry_date=Jan6), stays open.
    aaa = _insert_candidate(sandbox_repo, "AAA:c", "AAA", date(2026, 1, 5), rank=1)
    aaa_admission = _insert_admission(portfolio_repo, aaa, date(2026, 1, 5), rank=1, decision=ACCEPTED)
    _insert_reservation(portfolio_repo, aaa_admission)
    _insert_filled_order_and_position(sandbox_repo, aaa, fill_date=date(2026, 1, 6))

    # BBB: rank 2, NO_CAPACITY on Jan 5 -- AAA (still just a reservation, not yet
    # filled) should occupy the slot.
    bbb = _insert_candidate(sandbox_repo, "BBB:c", "BBB", date(2026, 1, 5), rank=2)
    bbb_admission = _insert_admission(portfolio_repo, bbb, date(2026, 1, 5), rank=2, decision=NO_CAPACITY)
    _insert_equity_snapshot(portfolio_repo, date(2026, 1, 5), open_position_count=0, reserved_order_count=1)

    # CCC: rank 1, NO_CAPACITY on Jan 6 -- AAA has now filled; the SAME reservation
    # has become an open position that should occupy the slot instead.
    ccc = _insert_candidate(sandbox_repo, "CCC:c", "CCC", date(2026, 1, 6), rank=1)
    ccc_admission = _insert_admission(portfolio_repo, ccc, date(2026, 1, 6), rank=1, decision=NO_CAPACITY)
    _insert_equity_snapshot(portfolio_repo, date(2026, 1, 6), open_position_count=1, reserved_order_count=0)

    context = _FakeContext(PRICES, date(2026, 1, 10), sandbox_repo, portfolio_repo)

    bbb_result = compute_opportunity_cost(context, bbb_admission, CALENDAR)
    assert [r.candidate_id for r in bbb_result.occupying_reservations] == ["AAA:c"]
    assert bbb_result.occupying_open_positions == ()

    ccc_result = compute_opportunity_cost(context, ccc_admission, CALENDAR)
    assert ccc_result.occupying_reservations == ()
    assert [p.candidate_id for p in ccc_result.occupying_open_positions] == ["AAA:c"]


def test_occupancy_reconstruction_ignores_wallclock_timestamps_far_from_replay_dates():
    """The reviewer's exact failure scenario: replay dates in 2024, but the rows
    were physically written (created_at) in 2026 -- a real gap between a
    historical backtest's own dates and when the diagnostics happen to run.
    Reconstruction must use only the LOGICAL dates (admission.as_of_date,
    order.fill_date) and ignore created_at/resolved_at entirely."""

    sandbox_repo, portfolio_repo = _repos()
    wallclock_2026 = datetime(2026, 7, 20, tzinfo=timezone.utc)

    aaa = _insert_candidate(sandbox_repo, "AAA:c", "AAA", date(2024, 3, 1), rank=1)
    aaa_admission = _insert_admission(portfolio_repo, aaa, date(2024, 3, 1), rank=1, decision=ACCEPTED, created_at=wallclock_2026)
    _insert_reservation(portfolio_repo, aaa_admission, created_at=wallclock_2026)
    _insert_pending_order(sandbox_repo, aaa)

    bbb = _insert_candidate(sandbox_repo, "BBB:c", "BBB", date(2024, 3, 1), rank=2)
    bbb_admission = _insert_admission(
        portfolio_repo, bbb, date(2024, 3, 1), rank=2, decision=NO_CAPACITY, created_at=wallclock_2026
    )
    _insert_equity_snapshot(portfolio_repo, date(2024, 3, 1), open_position_count=0, reserved_order_count=1)

    prices_2024 = pd.DataFrame(
        [
            _bar("AAA", date(2024, 3, 1), 10.0, 10.2, 9.8, 10.0),
            _bar("AAA", date(2024, 3, 4), 10.05, 10.6, 9.9, 10.3),
            _bar("BBB", date(2024, 3, 1), 20.0, 20.2, 19.8, 20.0),
            _bar("BBB", date(2024, 3, 4), 20.05, 20.6, 19.9, 20.3),
        ]
    )
    calendar_2024 = full_market_calendar(prices_2024)
    context = _FakeContext(prices_2024, date(2024, 3, 4), sandbox_repo, portfolio_repo)

    result = compute_opportunity_cost(context, bbb_admission, calendar_2024)

    # AAA's reservation (created_at=2026, resolved_at=2026 -- both AFTER the
    # 2024 replay dates entirely) must still be found occupying via its LOGICAL
    # admission date (2024-03-01), not excluded by the wall-clock mismatch.
    assert [r.candidate_id for r in result.occupying_reservations] == ["AAA:c"]


def test_same_day_later_ranked_acceptance_does_not_occupy_for_earlier_ranked_candidate():
    """Admissions within one day are decided in rank order (Section 8.4) -- a
    later-ranked candidate's own acceptance cannot have been visible to an
    earlier-ranked candidate's admission decision that same day. This scenario
    is deliberately constructed to isolate that ordering rule in the
    reconstruction logic; it does not claim to be a capacity sequence
    CapacityAdmissionOrchestrator would itself produce (rank-ordered admission
    could not actually reject a better-ranked candidate while accepting a
    worse-ranked one the same day)."""

    sandbox_repo, portfolio_repo = _repos()
    aaa = _insert_candidate(sandbox_repo, "AAA:c", "AAA", date(2026, 1, 5), rank=1)
    aaa_admission = _insert_admission(portfolio_repo, aaa, date(2026, 1, 5), rank=1, decision=NO_CAPACITY)

    bbb = _insert_candidate(sandbox_repo, "BBB:c", "BBB", date(2026, 1, 5), rank=2)
    bbb_admission = _insert_admission(portfolio_repo, bbb, date(2026, 1, 5), rank=2, decision=ACCEPTED)
    _insert_reservation(portfolio_repo, bbb_admission)
    _insert_pending_order(sandbox_repo, bbb)
    _insert_equity_snapshot(portfolio_repo, date(2026, 1, 5), open_position_count=0, reserved_order_count=1)

    context = _FakeContext(PRICES, date(2026, 1, 10), sandbox_repo, portfolio_repo)

    result = compute_opportunity_cost(context, aaa_admission, CALENDAR)

    assert result.occupying_reservations == ()
    assert result.occupying_open_positions == ()


def test_reconciliation_error_on_equity_snapshot_mismatch():
    sandbox_repo, portfolio_repo = _repos()
    aaa = _insert_candidate(sandbox_repo, "AAA:c", "AAA", date(2026, 1, 5), rank=1)
    aaa_admission = _insert_admission(portfolio_repo, aaa, date(2026, 1, 5), rank=1, decision=ACCEPTED)
    _insert_reservation(portfolio_repo, aaa_admission)
    _insert_pending_order(sandbox_repo, aaa)

    bbb = _insert_candidate(sandbox_repo, "BBB:c", "BBB", date(2026, 1, 5), rank=2)
    bbb_admission = _insert_admission(portfolio_repo, bbb, date(2026, 1, 5), rank=2, decision=NO_CAPACITY)
    # Wrong on purpose: the real reconstruction finds 1 reserved/0 open, not 0/0.
    _insert_equity_snapshot(portfolio_repo, date(2026, 1, 5), open_position_count=0, reserved_order_count=0)

    context = _FakeContext(PRICES, date(2026, 1, 10), sandbox_repo, portfolio_repo)

    with pytest.raises(CapacityOccupancyReconciliationError):
        compute_opportunity_cost(context, bbb_admission, CALENDAR)


def test_hand_computed_horizons_and_hypothetical_fill():
    sandbox_repo, portfolio_repo = _repos()
    candidate = _insert_candidate(sandbox_repo, "c1", "AAA", date(2026, 1, 5), rank=11, signal_close=10.0, max_entry_price=10.1)
    admission = _insert_admission(portfolio_repo, candidate, date(2026, 1, 5), rank=11, decision=NO_CAPACITY)
    # No other admissions exist in this fixture, so a reconcilable snapshot for
    # this date is 0 reserved / 0 open -- this test is about horizon/hypothetical-
    # fill arithmetic, not capacity state, so the pass-through values themselves
    # don't need to be large.
    _insert_equity_snapshot(portfolio_repo, date(2026, 1, 5), open_position_count=0, reserved_order_count=0)
    context = _FakeContext(PRICES, date(2026, 1, 10), sandbox_repo, portfolio_repo)

    result = compute_opportunity_cost(context, admission, CALENDAR)

    assert result.rank_at_admission == 11
    assert result.signal_close == pytest.approx(10.0)
    assert result.open_position_count == 0
    assert result.reserved_order_count == 0

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


def test_missing_equity_snapshot_gives_none_and_skips_reconciliation():
    sandbox_repo, portfolio_repo = _repos()
    candidate = _insert_candidate(sandbox_repo, "c2", "AAA", date(2026, 1, 5), rank=11)
    admission = _insert_admission(portfolio_repo, candidate, date(2026, 1, 5), rank=11, decision=NO_CAPACITY)
    context = _FakeContext(PRICES, date(2026, 1, 10), sandbox_repo, portfolio_repo)

    result = compute_opportunity_cost(context, admission, CALENDAR)

    assert result.open_position_count is None
    assert result.reserved_order_count is None


def test_no_fill_within_validity_window():
    sandbox_repo, portfolio_repo = _repos()
    candidate = _insert_candidate(sandbox_repo, "c3", "AAA", date(2026, 1, 5), rank=11, signal_close=10.0, max_entry_price=9.0)
    admission = _insert_admission(portfolio_repo, candidate, date(2026, 1, 5), rank=11, decision=NO_CAPACITY)
    context = _FakeContext(PRICES, date(2026, 1, 10), sandbox_repo, portfolio_repo)

    result = compute_opportunity_cost(context, admission, CALENDAR)

    assert result.hypothetical_would_have_filled is False
    assert result.hypothetical_fill_date is None
    assert result.hypothetical_raw_fill_price is None


def test_none_max_entry_price_never_fills_and_does_not_raise():
    sandbox_repo, portfolio_repo = _repos()
    candidate = _insert_candidate(sandbox_repo, "c4", "AAA", date(2026, 1, 5), rank=11, signal_close=10.0, max_entry_price=None)
    admission = _insert_admission(portfolio_repo, candidate, date(2026, 1, 5), rank=11, decision=NO_CAPACITY)
    context = _FakeContext(PRICES, date(2026, 1, 10), sandbox_repo, portfolio_repo)

    result = compute_opportunity_cost(context, admission, CALENDAR)

    assert result.hypothetical_would_have_filled is False


def test_missing_candidate_raises():
    sandbox_repo, portfolio_repo = _repos()
    admission = PortfolioAdmission(
        admission_id="ghost", replay_id=REPLAY_ID, candidate_id="ghost", symbol="AAA", as_of_date=date(2026, 1, 5),
        decision=NO_CAPACITY, rank_at_admission=1, slot_budget_units=None, reason="x", created_at=NOW,
    )
    context = _FakeContext(PRICES, date(2026, 1, 10), sandbox_repo, portfolio_repo)

    with pytest.raises(OpportunityCostComputationError):
        compute_opportunity_cost(context, admission, CALENDAR)


def test_non_no_capacity_admission_raises():
    sandbox_repo, portfolio_repo = _repos()
    candidate = _insert_candidate(sandbox_repo, "c5", "AAA", date(2026, 1, 5), rank=1)
    admission = _insert_admission(portfolio_repo, candidate, date(2026, 1, 5), rank=1, decision=ACCEPTED)
    context = _FakeContext(PRICES, date(2026, 1, 10), sandbox_repo, portfolio_repo)

    with pytest.raises(OpportunityCostComputationError):
        compute_opportunity_cost(context, admission, CALENDAR)
