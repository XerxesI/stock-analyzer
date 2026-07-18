from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

import stock_analyzer.sandbox.application.entry_service as entry_service_module
from stock_analyzer.sandbox.application.entry_service import EntryService
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

CONFIG = SandboxConfig()
SIGNAL_DATE = date(2026, 6, 15)
SESSION_1 = date(2026, 6, 16)
SESSION_2 = date(2026, 6, 17)


@pytest.fixture
def repo() -> SandboxRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return SandboxRepository(conn)


def _make_pending_order(repo: SandboxRepository, symbol: str = "AAA", max_entry_price: float = 10.20) -> EntryOrder:
    candidate = RankedCandidate(
        candidate_id=RankedCandidate.make_id(SIGNAL_DATE, symbol),
        run_id=SandboxRun.make_id(SIGNAL_DATE, "generate-candidates"),
        as_of_date=SIGNAL_DATE,
        symbol=symbol,
        daily_rank=1,
        model_score=0.5,
        signal_close=10.0,
        atr14=0.5,
        max_entry_price=max_entry_price,
        shadow_top10=True,
        actionable=True,
        exclusion_reason=None,
        adv_quintile="adv_q1",
        market_regime="Bull_Normal",
    )
    repo.create_run(
        SandboxRun(
            run_id=candidate.run_id,
            as_of_date=SIGNAL_DATE,
            command="generate-candidates",
            started_at=datetime.now(timezone.utc),
            configuration_hash="test",
        )
    )
    repo.insert_ranked_candidate(candidate)
    order = EntryOrder(
        order_id=EntryOrder.make_id(candidate.candidate_id),
        candidate_id=candidate.candidate_id,
        symbol=symbol,
        signal_date=SIGNAL_DATE,
        created_date=SIGNAL_DATE,
        valid_until=SESSION_2,
        max_entry_price=max_entry_price,
        status="PENDING",
    )
    order, _ = repo.create_entry_order(order)
    return order


def _bar(open_: float, high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1_000_000})


def _patch_bars(monkeypatch, bars_by_date: dict[date, pd.Series]) -> None:
    def fake_fetch_as_of(symbol: str, as_of: date, period: str = "2y") -> pd.DataFrame:
        if as_of not in bars_by_date:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return pd.DataFrame([bars_by_date[as_of]], index=[pd.Timestamp(as_of)])

    monkeypatch.setattr(entry_service_module, "fetch_as_of", fake_fetch_as_of)


def test_fills_at_open_when_open_below_ceiling(repo: SandboxRepository, monkeypatch):
    order = _make_pending_order(repo, max_entry_price=10.20)
    _patch_bars(monkeypatch, {SESSION_1: _bar(open_=10.10, high=10.30, low=10.05, close=10.20)})

    outcomes = EntryService(repo, CONFIG).process_entries(SESSION_1)

    assert outcomes[0].outcome == "FILLED"
    assert outcomes[0].fill_price == pytest.approx(10.10)
    updated = repo.get_entry_order(order.order_id)
    assert updated.status == "FILLED"
    assert updated.fill_price == pytest.approx(10.10)
    open_positions = repo.get_open_positions()
    assert len(open_positions) == 1
    assert open_positions[0].entry_price == pytest.approx(10.10)
    assert open_positions[0].symbol == order.symbol


def test_fills_at_open_when_open_exactly_at_ceiling(repo: SandboxRepository, monkeypatch):
    _make_pending_order(repo, max_entry_price=10.20)
    _patch_bars(monkeypatch, {SESSION_1: _bar(open_=10.20, high=10.30, low=10.15, close=10.25)})

    outcomes = EntryService(repo, CONFIG).process_entries(SESSION_1)

    assert outcomes[0].outcome == "FILLED"
    assert outcomes[0].fill_price == pytest.approx(10.20)


def test_fills_at_ceiling_when_gap_above_but_low_touches_ceiling(repo: SandboxRepository, monkeypatch):
    _make_pending_order(repo, max_entry_price=10.20)
    _patch_bars(monkeypatch, {SESSION_1: _bar(open_=10.50, high=10.60, low=10.15, close=10.40)})

    outcomes = EntryService(repo, CONFIG).process_entries(SESSION_1)

    assert outcomes[0].outcome == "FILLED"
    assert outcomes[0].fill_price == pytest.approx(10.20)  # ceiling, not open or low


def test_no_fill_when_entire_session_above_ceiling(repo: SandboxRepository, monkeypatch):
    order = _make_pending_order(repo, max_entry_price=10.20)
    _patch_bars(monkeypatch, {SESSION_1: _bar(open_=10.50, high=10.80, low=10.30, close=10.60)})

    outcomes = EntryService(repo, CONFIG).process_entries(SESSION_1)

    assert outcomes[0].outcome == "SKIPPED_TODAY"
    updated = repo.get_entry_order(order.order_id)
    assert updated.status == "PENDING"  # still has one attempt left


def test_first_session_no_fill_then_second_session_fills(repo: SandboxRepository, monkeypatch):
    order = _make_pending_order(repo, max_entry_price=10.20)
    _patch_bars(
        monkeypatch,
        {
            SESSION_1: _bar(open_=10.50, high=10.80, low=10.30, close=10.60),
            SESSION_2: _bar(open_=10.10, high=10.25, low=10.05, close=10.15),
        },
    )
    service = EntryService(repo, CONFIG)

    day1 = service.process_entries(SESSION_1)
    day2 = service.process_entries(SESSION_2)

    assert day1[0].outcome == "SKIPPED_TODAY"
    assert day2[0].outcome == "FILLED"
    assert day2[0].fill_price == pytest.approx(10.10)
    updated = repo.get_entry_order(order.order_id)
    assert updated.status == "FILLED"


def test_two_sessions_without_fill_expires(repo: SandboxRepository, monkeypatch):
    order = _make_pending_order(repo, max_entry_price=10.20)
    _patch_bars(
        monkeypatch,
        {
            SESSION_1: _bar(open_=10.50, high=10.80, low=10.30, close=10.60),
            SESSION_2: _bar(open_=10.55, high=10.90, low=10.35, close=10.70),
        },
    )
    service = EntryService(repo, CONFIG)

    service.process_entries(SESSION_1)
    day2 = service.process_entries(SESSION_2)

    assert day2[0].outcome == "EXPIRED"
    updated = repo.get_entry_order(order.order_id)
    assert updated.status == "EXPIRED"
    assert len(repo.get_open_positions()) == 0


def test_no_fill_on_or_before_signal_date(repo: SandboxRepository, monkeypatch):
    order = _make_pending_order(repo, max_entry_price=10.20)
    # Even if SIGNAL_DATE itself had a bar that would technically satisfy the ceiling,
    # it must never be used as an execution session.
    _patch_bars(monkeypatch, {SIGNAL_DATE: _bar(open_=10.10, high=10.20, low=10.05, close=10.15)})

    outcomes = EntryService(repo, CONFIG).process_entries(SIGNAL_DATE)

    assert outcomes == []
    updated = repo.get_entry_order(order.order_id)
    assert updated.status == "PENDING"


def test_no_session_data_does_not_consume_an_attempt(repo: SandboxRepository, monkeypatch):
    order = _make_pending_order(repo, max_entry_price=10.20)
    # No bar at all for SESSION_1 (e.g. not yet fetched / genuinely no data that day).
    _patch_bars(monkeypatch, {SESSION_2: _bar(open_=10.10, high=10.25, low=10.05, close=10.15)})
    service = EntryService(repo, CONFIG)

    day1 = service.process_entries(SESSION_1)
    day2 = service.process_entries(SESSION_2)

    assert day1[0].outcome == "NO_SESSION_DATA"
    assert day2[0].outcome == "FILLED"  # still counted as attempt #1, not #2
