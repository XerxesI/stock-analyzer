"""Tests for EXP-005's Stage 6 "aligned dual-accounting" design: core decides
WHETHER/WHEN/AT WHAT RAW PRICE a fill or exit happens (completely unaffected by
which accounting seam is injected); EXP-005's Exp005AccountingSeam decides ONLY
position sizing, and records its own cost-adjusted executions ledger -- using the
SAME quantity everywhere. See stock_analyzer/sandbox/application/accounting_seam.py
and stock_analyzer/sandbox/exp005/application/portfolio_accounting_seam.py.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

import stock_analyzer.sandbox.application.entry_service as entry_service_module
import stock_analyzer.sandbox.application.monitoring_service as monitoring_service_module
from stock_analyzer.sandbox.application.accounting_seam import DefaultAccountingSeam
from stock_analyzer.sandbox.application.entry_service import EntryService
from stock_analyzer.sandbox.application.monitoring_service import MonitoringService
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import PENDING, EntryOrder
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.exp005.application.admission_orchestrator import AdmissionTransactionService
from stock_analyzer.sandbox.exp005.application.portfolio_accounting_seam import (
    AccountingSeamIntegrityError,
    Exp005AccountingSeam,
)
from stock_analyzer.sandbox.exp005.application.portfolio_ledger import PortfolioLedger
from stock_analyzer.sandbox.exp005.config import PortfolioConfig
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL
from stock_analyzer.sandbox.exp005.domain.units import price_units_to_float, quantity_units_to_float, to_money_units
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

SIGNAL_DATE = date(2026, 6, 15)
SESSION_1 = date(2026, 6, 16)  # fill day
SESSION_2 = date(2026, 6, 17)  # close day
REPLAY_ID = "replay-1"
MAX_SLOTS = 10
PORTFOLIO_CONFIG = PortfolioConfig()  # starting_capital=100_000, slot_budget=10_000, commission=1.0, slippage=0.0005
MARKET_DATA_SNAPSHOT_ID = "snap-1"


def _bar(open_: float, high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1_000_000})


def _patch_bars(monkeypatch, bars_by_date: dict[date, pd.Series]) -> None:
    def fake_fetch_as_of(symbol: str, as_of: date, period: str = "2y") -> pd.DataFrame:
        if as_of not in bars_by_date:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return pd.DataFrame([bars_by_date[as_of]], index=[pd.Timestamp(as_of)])

    monkeypatch.setattr(entry_service_module, "fetch_as_of", fake_fetch_as_of)
    monkeypatch.setattr(monitoring_service_module, "fetch_as_of", fake_fetch_as_of)


def _make_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    return conn


def _seed_candidate(conn: sqlite3.Connection, symbol: str = "AAA", max_entry_price: float = 101.0) -> RankedCandidate:
    candidate = RankedCandidate(
        candidate_id=RankedCandidate.make_id(SIGNAL_DATE, symbol),
        run_id=SandboxRun.make_id(SIGNAL_DATE, "generate-candidates"),
        as_of_date=SIGNAL_DATE,
        symbol=symbol,
        daily_rank=1,
        model_score=0.5,
        signal_close=100.0,
        atr14=2.0,
        max_entry_price=max_entry_price,
        shadow_top10=True,
        actionable=True,
        exclusion_reason=None,
        adv_quintile="adv_q1",
        market_regime="Bull_Normal",
    )
    conn.execute(
        "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, status, "
        " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            candidate.run_id, SIGNAL_DATE.isoformat(), "generate-candidates",
            datetime.now(timezone.utc).isoformat(), None, "COMPLETED", "v1", None, None, "hash", None,
        ),
    )
    conn.commit()
    SandboxRepository(conn).insert_ranked_candidate(candidate)
    return candidate


def _order_for(candidate: RankedCandidate) -> EntryOrder:
    return EntryOrder(
        order_id=EntryOrder.make_id(candidate.candidate_id),
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        signal_date=candidate.as_of_date,
        created_date=candidate.as_of_date,
        valid_until=SESSION_2,
        max_entry_price=candidate.max_entry_price,
        status=PENDING,
    )


class _FixedCash:
    def __init__(self, units: int) -> None:
        self._units = units

    def available_unreserved_cash_units(self) -> int:
        return self._units


def _admit(conn: sqlite3.Connection, candidate: RankedCandidate, portfolio_repo: PortfolioRepository, sandbox_repo: SandboxRepository):
    service = AdmissionTransactionService(
        conn, portfolio_repo, sandbox_repo, REPLAY_ID, MAX_SLOTS,
        to_money_units(PORTFOLIO_CONFIG.slot_budget), _FixedCash(10**12),
    )
    return service.admit_candidate(candidate, candidate.as_of_date, _order_for(candidate))


def _wire_exp005(conn: sqlite3.Connection, variant_id: str = "B", control_seed: int | None = None):
    portfolio_repo = PortfolioRepository(conn)
    sandbox_repo = SandboxRepository(conn)
    seam = Exp005AccountingSeam(
        portfolio_repo, REPLAY_ID, variant_id, control_seed, PORTFOLIO_CONFIG, MARKET_DATA_SNAPSHOT_ID,
    )
    return portfolio_repo, sandbox_repo, seam


# --------------------------------------------------------------- fill reconciliation


def test_buy_fill_reconciles_across_position_transaction_and_execution(monkeypatch):
    conn = _make_connection()
    candidate = _seed_candidate(conn)
    portfolio_repo, sandbox_repo, seam = _wire_exp005(conn)
    _admit(conn, candidate, portfolio_repo, sandbox_repo)

    _patch_bars(monkeypatch, {SESSION_1: _bar(open_=100.0, high=105.0, low=99.0, close=101.0)})
    EntryService(sandbox_repo, SandboxConfig(), accounting_seam=seam).process_entries(SESSION_1)

    order = sandbox_repo.get_entry_order(EntryOrder.make_id(candidate.candidate_id))
    position = sandbox_repo.get_open_positions()[0]
    buy_txn = [t for t in sandbox_repo.get_transactions_for_position(position.position_id) if t.transaction_type == BUY][0]
    buy_execution = [e for e in portfolio_repo.list_executions_for_position(position.position_id) if e.side == BUY][0]

    # 1. same position_id and quantity across position, virtual transaction, execution
    assert buy_txn.position_id == position.position_id
    assert buy_execution.position_id == position.position_id
    assert position.quantity == buy_txn.quantity
    assert position.quantity == quantity_units_to_float(buy_execution.quantity_units)

    # 2. raw fill price agrees between core and execution
    assert order.fill_price == 100.0
    assert position.entry_price == 100.0
    assert buy_txn.price == 100.0
    assert price_units_to_float(buy_execution.raw_market_fill_price_units) == 100.0

    # 5. effective price/costs affect only the EXP-005 ledger
    assert price_units_to_float(buy_execution.effective_fill_price_units) != 100.0  # slippage-adjusted
    assert buy_execution.commission_units == to_money_units(PORTFOLIO_CONFIG.entry_commission)

    # reservation converted
    reservation = portfolio_repo.get_reservation_for_admission(candidate.candidate_id)
    assert reservation.status == "CONVERTED"


def test_buy_cash_debit_equals_exactly_one_slot_budget(monkeypatch):
    conn = _make_connection()
    candidate = _seed_candidate(conn)
    portfolio_repo, sandbox_repo, seam = _wire_exp005(conn)
    starting_capital_units = to_money_units(PORTFOLIO_CONFIG.starting_capital)
    ledger = PortfolioLedger(portfolio_repo, sandbox_repo, REPLAY_ID, starting_capital_units)

    assert ledger.available_unreserved_cash_units() == starting_capital_units

    _admit(conn, candidate, portfolio_repo, sandbox_repo)

    assert ledger.available_unreserved_cash_units() == starting_capital_units - to_money_units(PORTFOLIO_CONFIG.slot_budget)


def test_sell_uses_the_original_buy_quantity(monkeypatch):
    conn = _make_connection()
    candidate = _seed_candidate(conn)
    portfolio_repo, sandbox_repo, seam = _wire_exp005(conn)
    _admit(conn, candidate, portfolio_repo, sandbox_repo)

    _patch_bars(
        monkeypatch,
        {
            SESSION_1: _bar(open_=100.0, high=105.0, low=99.0, close=101.0),
            SESSION_2: _bar(open_=125.0, high=126.0, low=124.0, close=125.5),
        },
    )
    EntryService(sandbox_repo, SandboxConfig(), accounting_seam=seam).process_entries(SESSION_1)
    MonitoringService(sandbox_repo, SandboxConfig(), accounting_seam=seam).monitor(SESSION_2)

    position = sandbox_repo.get_position(f"AAA:{SESSION_1.isoformat()}")
    assert position.status == "CLOSED"

    buy_execution = [e for e in portfolio_repo.list_executions_for_position(position.position_id) if e.side == BUY][0]
    sell_execution = [e for e in portfolio_repo.list_executions_for_position(position.position_id) if e.side == SELL][0]
    sell_txn = [t for t in sandbox_repo.get_transactions_for_position(position.position_id) if t.transaction_type == SELL][0]

    assert sell_execution.quantity_units == buy_execution.quantity_units
    assert sell_txn.quantity == position.quantity
    assert quantity_units_to_float(sell_execution.quantity_units) == position.quantity

    # raw exit price agreement
    assert position.exit_price == 125.0
    assert sell_txn.price == 125.0
    assert price_units_to_float(sell_execution.raw_market_fill_price_units) == 125.0
    # effective price isolated to the EXP-005 ledger
    assert price_units_to_float(sell_execution.effective_fill_price_units) != 125.0

    # core's realized_return stays a raw-price shadow metric
    expected_raw_return = (125.0 - 100.0) / 100.0
    assert position.realized_return == pytest.approx(expected_raw_return)


# ------------------------------------------------------------ decision invariance


def test_exp005_sizing_does_not_change_fill_or_exit_decisions(monkeypatch):
    """Same market data, two independent databases -- one with the default seam,
    one with the EXP-005 seam. fill_date/fill_reason/target_price/exit_date/
    exit_reason must be IDENTICAL; only quantity (and entry/exit notional, which is
    derived from quantity) may differ."""

    bars = {
        SESSION_1: _bar(open_=100.0, high=105.0, low=99.0, close=101.0),
        SESSION_2: _bar(open_=125.0, high=126.0, low=124.0, close=125.5),
    }

    # -- default seam run --
    conn_a = _make_connection()
    candidate_a = _seed_candidate(conn_a)
    repo_a = SandboxRepository(conn_a)
    order_a = _order_for(candidate_a)
    order_a, _ = repo_a.create_entry_order(order_a)
    _patch_bars(monkeypatch, bars)
    EntryService(repo_a, SandboxConfig()).process_entries(SESSION_1)
    MonitoringService(repo_a, SandboxConfig()).monitor(SESSION_2)
    position_a = repo_a.get_position(f"AAA:{SESSION_1.isoformat()}")
    order_a = repo_a.get_entry_order(order_a.order_id)

    # -- EXP-005 seam run, independent database --
    conn_b = _make_connection()
    candidate_b = _seed_candidate(conn_b)
    portfolio_repo_b, sandbox_repo_b, seam_b = _wire_exp005(conn_b)
    _admit(conn_b, candidate_b, portfolio_repo_b, sandbox_repo_b)
    EntryService(sandbox_repo_b, SandboxConfig(), accounting_seam=seam_b).process_entries(SESSION_1)
    MonitoringService(sandbox_repo_b, SandboxConfig(), accounting_seam=seam_b).monitor(SESSION_2)
    position_b = sandbox_repo_b.get_position(f"AAA:{SESSION_1.isoformat()}")
    order_b = sandbox_repo_b.get_entry_order(EntryOrder.make_id(candidate_b.candidate_id))

    assert order_a.fill_date == order_b.fill_date
    assert order_a.fill_price == order_b.fill_price
    assert order_a.fill_reason == order_b.fill_reason
    assert position_a.target_price == position_b.target_price
    assert position_a.exit_date == position_b.exit_date
    assert position_a.exit_reason == position_b.exit_reason
    assert position_a.entry_price == position_b.entry_price  # both raw, unaffected by sizing
    # sizing genuinely differs ($1,000 default notional vs $10,000 EXP-005 slot budget)
    assert position_a.quantity != position_b.quantity


def test_default_seam_preserves_virtual_notional_sizing_exactly():
    config = SandboxConfig()
    seam = DefaultAccountingSeam(config)
    quantity = seam.size_buy(order=None, raw_fill_price=50.0, fill_date=SESSION_1)  # type: ignore[arg-type]
    assert quantity == config.virtual_notional / 50.0


# ---------------------------------------------------------------- atomic rollback


class _ExplodingOnFilled(Exp005AccountingSeam):
    def on_filled(self, order, position, transaction, raw_fill_price) -> None:
        raise RuntimeError("simulated failure inside on_filled")


class _ExplodingOnClosed(Exp005AccountingSeam):
    def on_closed(self, position, transaction, exit_date, exit_price, exit_reason) -> None:
        raise RuntimeError("simulated failure inside on_closed")


def test_fill_event_rolls_back_completely_on_injected_failure(monkeypatch):
    conn = _make_connection()
    candidate = _seed_candidate(conn)
    portfolio_repo = PortfolioRepository(conn)
    sandbox_repo = SandboxRepository(conn)
    _admit(conn, candidate, portfolio_repo, sandbox_repo)
    seam = _ExplodingOnFilled(portfolio_repo, REPLAY_ID, "B", None, PORTFOLIO_CONFIG, MARKET_DATA_SNAPSHOT_ID)

    _patch_bars(monkeypatch, {SESSION_1: _bar(open_=100.0, high=105.0, low=99.0, close=101.0)})
    with pytest.raises(RuntimeError, match="simulated failure"):
        EntryService(sandbox_repo, SandboxConfig(), accounting_seam=seam).process_entries(SESSION_1)

    order = sandbox_repo.get_entry_order(EntryOrder.make_id(candidate.candidate_id))
    assert order.status == "PENDING"  # NOT filled
    assert sandbox_repo.get_open_positions() == []
    reservation = portfolio_repo.get_reservation_for_admission(candidate.candidate_id)
    assert reservation.status == "RESERVED"  # NOT converted
    assert portfolio_repo.list_executions_for_experiment(REPLAY_ID) == []


def test_close_event_rolls_back_completely_on_injected_failure(monkeypatch):
    conn = _make_connection()
    candidate = _seed_candidate(conn)
    portfolio_repo = PortfolioRepository(conn)
    sandbox_repo = SandboxRepository(conn)
    _admit(conn, candidate, portfolio_repo, sandbox_repo)
    good_seam = Exp005AccountingSeam(portfolio_repo, REPLAY_ID, "B", None, PORTFOLIO_CONFIG, MARKET_DATA_SNAPSHOT_ID)

    _patch_bars(
        monkeypatch,
        {
            SESSION_1: _bar(open_=100.0, high=105.0, low=99.0, close=101.0),
            SESSION_2: _bar(open_=125.0, high=126.0, low=124.0, close=125.5),
        },
    )
    EntryService(sandbox_repo, SandboxConfig(), accounting_seam=good_seam).process_entries(SESSION_1)
    position_before = sandbox_repo.get_open_positions()[0]
    executions_before = len(portfolio_repo.list_executions_for_experiment(REPLAY_ID))

    exploding_seam = _ExplodingOnClosed(portfolio_repo, REPLAY_ID, "B", None, PORTFOLIO_CONFIG, MARKET_DATA_SNAPSHOT_ID)
    with pytest.raises(RuntimeError, match="simulated failure"):
        MonitoringService(sandbox_repo, SandboxConfig(), accounting_seam=exploding_seam).monitor(SESSION_2)

    position_after = sandbox_repo.get_position(position_before.position_id)
    assert position_after.status == "OPEN"  # NOT closed
    sell_txns = [t for t in sandbox_repo.get_transactions_for_position(position_before.position_id) if t.transaction_type == SELL]
    assert sell_txns == []
    assert len(portfolio_repo.list_executions_for_experiment(REPLAY_ID)) == executions_before  # no new SELL execution


# --------------------------------------------------------------------- expiry path


def test_expiry_releases_the_reservation(monkeypatch):
    conn = _make_connection()
    candidate = _seed_candidate(conn, max_entry_price=90.0)  # ceiling never reachable below
    portfolio_repo, sandbox_repo, seam = _wire_exp005(conn)
    _admit(conn, candidate, portfolio_repo, sandbox_repo)

    _patch_bars(
        monkeypatch,
        {
            SESSION_1: _bar(open_=100.0, high=105.0, low=99.0, close=101.0),
            SESSION_2: _bar(open_=100.0, high=105.0, low=99.0, close=101.0),
        },
    )
    EntryService(sandbox_repo, SandboxConfig(), accounting_seam=seam).process_entries(SESSION_1)
    EntryService(sandbox_repo, SandboxConfig(), accounting_seam=seam).process_entries(SESSION_2)

    order = sandbox_repo.get_entry_order(EntryOrder.make_id(candidate.candidate_id))
    assert order.status == "EXPIRED"
    reservation = portfolio_repo.get_reservation_for_admission(candidate.candidate_id)
    assert reservation.status == "RELEASED"
    assert sandbox_repo.get_open_positions() == []


def test_missing_reservation_is_an_integrity_error_not_a_silent_skip(monkeypatch):
    """Structurally, every PENDING order created via AdmissionTransactionService has
    exactly one RESERVED reservation -- if a caller somehow reaches size_buy without
    one (a data-integrity failure, not a normal path), it must fail loudly."""

    conn = _make_connection()
    candidate = _seed_candidate(conn)
    portfolio_repo, sandbox_repo, seam = _wire_exp005(conn)
    # Deliberately create the order WITHOUT going through AdmissionTransactionService
    # (no reservation exists for this candidate).
    order = _order_for(candidate)
    sandbox_repo.create_entry_order(order)

    _patch_bars(monkeypatch, {SESSION_1: _bar(open_=100.0, high=105.0, low=99.0, close=101.0)})
    with pytest.raises(AccountingSeamIntegrityError):
        EntryService(sandbox_repo, SandboxConfig(), accounting_seam=seam).process_entries(SESSION_1)
