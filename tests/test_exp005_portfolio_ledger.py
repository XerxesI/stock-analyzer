"""Tests for PortfolioLedger's daily equity snapshot computation (Revision 5,
Section 8.5, Stage 6). Cash/reserved/mark-to-market are pure derivations from
already-persisted facts -- these tests verify the arithmetic against hand-computed
fixtures, not just that a snapshot gets written.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import PENDING, EntryOrder
from stock_analyzer.sandbox.domain.position import VirtualPosition
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.exp005.application.portfolio_ledger import PortfolioLedger
from stock_analyzer.sandbox.exp005.domain.admission import ACCEPTED, RESERVED, PortfolioAdmission, SlotReservation
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution
from stock_analyzer.sandbox.exp005.domain.units import to_money_units, to_price_units, to_quantity_units, to_rate_units
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

REPLAY_ID = "replay-1"
SIGNAL_DATE = date(2026, 6, 15)
AS_OF = date(2026, 6, 17)
NOW = datetime.now(timezone.utc)
STARTING_CAPITAL_UNITS = to_money_units(100_000.0)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    init_exp005_schema(c)
    return c


def _seed_candidate(conn: sqlite3.Connection, candidate_id: str, symbol: str) -> None:
    conn.execute(
        "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, status, "
        " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (f"run-{symbol}", SIGNAL_DATE.isoformat(), "generate-candidates", NOW.isoformat(), None, "COMPLETED", "v1", None, None, "hash", None),
    )
    conn.commit()
    SandboxRepository(conn).insert_ranked_candidate(
        RankedCandidate(
            candidate_id=candidate_id, run_id=f"run-{symbol}", as_of_date=SIGNAL_DATE, symbol=symbol, daily_rank=1,
            model_score=0.5, signal_close=100.0, atr14=2.0, max_entry_price=101.0, shadow_top10=True, actionable=True,
            exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
        )
    )


def _seed_order(conn: sqlite3.Connection, candidate_id: str, symbol: str) -> None:
    SandboxRepository(conn).create_entry_order(
        EntryOrder(
            order_id=f"{candidate_id}:order", candidate_id=candidate_id, symbol=symbol, signal_date=SIGNAL_DATE,
            created_date=SIGNAL_DATE, valid_until=date(2026, 6, 18), max_entry_price=101.0, status=PENDING,
        )
    )


def test_snapshot_with_no_activity_equals_starting_capital(conn: sqlite3.Connection):
    ledger = PortfolioLedger(PortfolioRepository(conn), SandboxRepository(conn), REPLAY_ID, STARTING_CAPITAL_UNITS)
    snapshot = ledger.compute_snapshot(AS_OF, NOW)

    assert snapshot.cash_units == STARTING_CAPITAL_UNITS
    assert snapshot.reserved_capital_units == 0
    assert snapshot.open_position_market_value_units == 0
    assert snapshot.total_equity_units == STARTING_CAPITAL_UNITS
    assert snapshot.open_position_count == 0
    assert snapshot.reserved_order_count == 0
    assert snapshot.cumulative_commissions_units == 0
    assert snapshot.cumulative_slippage_cost_units == 0


def test_snapshot_with_one_active_reservation(conn: sqlite3.Connection):
    portfolio_repo = PortfolioRepository(conn)
    _seed_candidate(conn, "c1", "AAA")
    portfolio_repo.insert_admission(
        PortfolioAdmission("c1", REPLAY_ID, "c1", "AAA", SIGNAL_DATE, ACCEPTED, 1, to_money_units(10_000.0), None, NOW)
    )
    portfolio_repo.insert_reservation(
        SlotReservation(SlotReservation.make_id("c1"), REPLAY_ID, "c1", "c1", "AAA", to_money_units(10_000.0), RESERVED, NOW)
    )
    conn.commit()

    ledger = PortfolioLedger(portfolio_repo, SandboxRepository(conn), REPLAY_ID, STARTING_CAPITAL_UNITS)
    snapshot = ledger.compute_snapshot(AS_OF, NOW)

    assert snapshot.reserved_capital_units == to_money_units(10_000.0)
    assert snapshot.reserved_order_count == 1
    assert snapshot.cash_units == STARTING_CAPITAL_UNITS - to_money_units(10_000.0)
    # reconciliation invariant: cash + reserved + open-position-value == total equity
    assert snapshot.cash_units + snapshot.reserved_capital_units + snapshot.open_position_market_value_units == snapshot.total_equity_units
    assert snapshot.total_equity_units == STARTING_CAPITAL_UNITS  # reservation alone doesn't change total equity


def test_snapshot_with_open_position_marks_to_current_close(conn: sqlite3.Connection):
    portfolio_repo = PortfolioRepository(conn)
    sandbox_repo = SandboxRepository(conn)
    _seed_candidate(conn, "c1", "AAA")
    _seed_order(conn, "c1", "AAA")

    position = VirtualPosition(
        position_id="p1", symbol="AAA", candidate_id="c1", order_id="c1:order", signal_date=SIGNAL_DATE,
        entry_date=date(2026, 6, 16), entry_price=100.0, quantity=99.99, initial_rank=1, initial_model_score=0.5,
        signal_close=100.0, max_entry_price=101.0, initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal",
        target_price=120.0, planned_time_exit_date=date(2026, 7, 20), current_close=105.0,
    )
    sandbox_repo._insert_position_row(position)
    conn.commit()

    buy_execution = Execution(
        execution_id="e1", replay_id=REPLAY_ID, variant_id="B", control_seed=None, order_id="c1:order",
        candidate_id="c1", position_id="p1", symbol="AAA", side=BUY, decision_date=SIGNAL_DATE,
        execution_date=date(2026, 6, 16), raw_market_fill_price_units=to_price_units(100.0),
        effective_fill_price_units=to_price_units(100.05), quantity_units=to_quantity_units(99.99),
        gross_notional_units=to_money_units(99.99 * 100.05), commission_units=to_money_units(1.0),
        slippage_rate_units=to_rate_units(0.0005), slippage_cost_units=to_money_units(99.99 * 0.05),
        net_cash_flow_units=-to_money_units(99.99 * 100.05 + 1.0), fill_reason="FILLED_AT_OPEN",
        market_data_snapshot_id="snap-1", created_at=NOW,
    )
    portfolio_repo.append_execution(buy_execution)

    ledger = PortfolioLedger(portfolio_repo, sandbox_repo, REPLAY_ID, STARTING_CAPITAL_UNITS)
    snapshot = ledger.compute_snapshot(AS_OF, NOW)

    assert snapshot.open_position_count == 1
    assert snapshot.open_position_market_value_units == to_money_units(99.99 * 105.0)  # marks to current_close
    assert snapshot.cumulative_commissions_units == to_money_units(1.0)
    assert snapshot.cumulative_slippage_cost_units == buy_execution.slippage_cost_units
    assert snapshot.cash_units == STARTING_CAPITAL_UNITS + buy_execution.net_cash_flow_units
    assert snapshot.cash_units + snapshot.reserved_capital_units + snapshot.open_position_market_value_units == snapshot.total_equity_units


def test_snapshot_falls_back_to_entry_price_when_not_yet_monitored(conn: sqlite3.Connection):
    """A position filled TODAY has not been monitored yet (monitoring runs before
    candidate admission in the day-loop, Section 8.5) -- current_close is still
    None. The snapshot must use entry_price as the last known valid mark, not
    crash or silently treat it as zero."""

    portfolio_repo = PortfolioRepository(conn)
    sandbox_repo = SandboxRepository(conn)
    _seed_candidate(conn, "c1", "AAA")
    _seed_order(conn, "c1", "AAA")
    position = VirtualPosition(
        position_id="p1", symbol="AAA", candidate_id="c1", order_id="c1:order", signal_date=SIGNAL_DATE,
        entry_date=date(2026, 6, 16), entry_price=100.0, quantity=99.99, initial_rank=1, initial_model_score=0.5,
        signal_close=100.0, max_entry_price=101.0, initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal",
        target_price=120.0, planned_time_exit_date=date(2026, 7, 20), current_close=None,
    )
    sandbox_repo._insert_position_row(position)
    conn.commit()

    ledger = PortfolioLedger(portfolio_repo, sandbox_repo, REPLAY_ID, STARTING_CAPITAL_UNITS)
    snapshot = ledger.compute_snapshot(AS_OF, NOW)

    assert snapshot.open_position_market_value_units == to_money_units(99.99 * 100.0)


def test_snapshot_after_sell_removes_position_and_credits_cash(conn: sqlite3.Connection):
    portfolio_repo = PortfolioRepository(conn)
    sandbox_repo = SandboxRepository(conn)
    _seed_candidate(conn, "c1", "AAA")
    _seed_order(conn, "c1", "AAA")
    sandbox_repo.create_position(
        VirtualPosition(
            position_id="p1", symbol="AAA", candidate_id="c1", order_id="c1:order", signal_date=SIGNAL_DATE,
            entry_date=date(2026, 6, 16), entry_price=100.0, quantity=99.99, initial_rank=1, initial_model_score=0.5,
            signal_close=100.0, max_entry_price=101.0, initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal",
            target_price=120.0, planned_time_exit_date=date(2026, 7, 20),
        )
    )
    sandbox_repo.close_position(
        "p1", exit_date=AS_OF, exit_price=120.0, exit_reason="SELL_TARGET", realized_return=0.20,
        final_holding_day_count=2, final_mfe=0.20, final_mae=0.0,
    )

    buy = Execution(
        execution_id="e1", replay_id=REPLAY_ID, variant_id="B", control_seed=None, order_id="c1:order",
        candidate_id="c1", position_id="p1", symbol="AAA", side=BUY, decision_date=SIGNAL_DATE,
        execution_date=date(2026, 6, 16), raw_market_fill_price_units=to_price_units(100.0),
        effective_fill_price_units=to_price_units(100.05), quantity_units=to_quantity_units(99.99),
        gross_notional_units=to_money_units(99.99 * 100.05), commission_units=to_money_units(1.0),
        slippage_rate_units=to_rate_units(0.0005), slippage_cost_units=to_money_units(4.9995),
        net_cash_flow_units=-to_money_units(99.99 * 100.05 + 1.0), fill_reason="FILLED_AT_OPEN",
        market_data_snapshot_id="snap-1", created_at=NOW,
    )
    portfolio_repo.append_execution(buy)
    sell = Execution(
        execution_id="e2", replay_id=REPLAY_ID, variant_id="B", control_seed=None, order_id=None,
        candidate_id="c1", position_id="p1", symbol="AAA", side=SELL, decision_date=AS_OF, execution_date=AS_OF,
        raw_market_fill_price_units=to_price_units(120.0), effective_fill_price_units=to_price_units(119.94),
        quantity_units=to_quantity_units(99.99), gross_notional_units=to_money_units(99.99 * 119.94),
        commission_units=to_money_units(1.0), slippage_rate_units=to_rate_units(0.0005),
        slippage_cost_units=to_money_units(5.9994), net_cash_flow_units=to_money_units(99.99 * 119.94 - 1.0),
        fill_reason="SELL_TARGET", market_data_snapshot_id="snap-1", created_at=NOW,
    )
    portfolio_repo.append_execution(sell)
    # No open position row (already closed) -- get_open_positions() returns [].

    ledger = PortfolioLedger(portfolio_repo, sandbox_repo, REPLAY_ID, STARTING_CAPITAL_UNITS)
    snapshot = ledger.compute_snapshot(AS_OF, NOW)

    assert snapshot.open_position_count == 0
    assert snapshot.open_position_market_value_units == 0
    assert snapshot.cash_units == STARTING_CAPITAL_UNITS + buy.net_cash_flow_units + sell.net_cash_flow_units
    assert snapshot.cumulative_commissions_units == to_money_units(2.0)
    assert snapshot.total_equity_units == snapshot.cash_units  # nothing reserved, nothing open


def test_available_unreserved_cash_units_matches_compute_snapshot_cash(conn: sqlite3.Connection):
    portfolio_repo = PortfolioRepository(conn)
    _seed_candidate(conn, "c1", "AAA")
    portfolio_repo.insert_admission(
        PortfolioAdmission("c1", REPLAY_ID, "c1", "AAA", SIGNAL_DATE, ACCEPTED, 1, to_money_units(10_000.0), None, NOW)
    )
    portfolio_repo.insert_reservation(
        SlotReservation(SlotReservation.make_id("c1"), REPLAY_ID, "c1", "c1", "AAA", to_money_units(10_000.0), RESERVED, NOW)
    )
    conn.commit()

    ledger = PortfolioLedger(portfolio_repo, SandboxRepository(conn), REPLAY_ID, STARTING_CAPITAL_UNITS)
    snapshot = ledger.compute_snapshot(AS_OF, NOW)

    assert ledger.available_unreserved_cash_units() == snapshot.cash_units
