from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

import stock_analyzer.sandbox.application.monitoring_service as monitoring_service_module
from stock_analyzer.sandbox.application.monitoring_service import MonitoringService
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.position import VirtualPosition
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

CONFIG = SandboxConfig()
SIGNAL_DATE = date(2026, 6, 15)
ENTRY_DATE = date(2026, 6, 16)  # holding day 1


@pytest.fixture
def repo() -> SandboxRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return SandboxRepository(conn)


def _open_position(repo: SandboxRepository, symbol: str = "AAA", entry_price: float = 10.0) -> VirtualPosition:
    candidate = RankedCandidate(
        candidate_id=RankedCandidate.make_id(SIGNAL_DATE, symbol),
        run_id=SandboxRun.make_id(SIGNAL_DATE, "generate-candidates"),
        as_of_date=SIGNAL_DATE,
        symbol=symbol,
        daily_rank=1,
        model_score=0.5,
        signal_close=entry_price,
        atr14=0.5,
        max_entry_price=entry_price * 1.02,
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
        valid_until=date(2026, 6, 18),
        max_entry_price=entry_price * 1.02,
        status="FILLED",
        fill_date=ENTRY_DATE,
        fill_price=entry_price,
    )
    repo.create_entry_order(order)
    position = VirtualPosition(
        position_id=VirtualPosition.make_id(symbol, ENTRY_DATE),
        symbol=symbol,
        candidate_id=candidate.candidate_id,
        order_id=order.order_id,
        signal_date=SIGNAL_DATE,
        entry_date=ENTRY_DATE,
        entry_price=entry_price,
        quantity=CONFIG.virtual_notional / entry_price,
        initial_rank=1,
        initial_model_score=0.5,
        signal_close=entry_price,
        max_entry_price=entry_price * 1.02,
        initial_adv_quintile="adv_q1",
        initial_market_regime="Bull_Normal",
        target_price=round(entry_price * 1.20, 4),
        planned_time_exit_date=date(2026, 7, 14),
    )
    position, _ = repo.create_position(position)
    return position


def _bar(open_: float, high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1_000_000})


def _patch_bars(monkeypatch, bars_by_date: dict[date, pd.Series]) -> None:
    def fake_fetch_as_of(symbol: str, as_of: date, period: str = "2y") -> pd.DataFrame:
        if as_of not in bars_by_date:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return pd.DataFrame([bars_by_date[as_of]], index=[pd.Timestamp(as_of)])

    monkeypatch.setattr(monitoring_service_module, "fetch_as_of", fake_fetch_as_of)


def test_target_reached_at_open(repo: SandboxRepository, monkeypatch):
    position = _open_position(repo, entry_price=10.0)  # target = 12.0
    _patch_bars(monkeypatch, {ENTRY_DATE: _bar(open_=12.5, high=12.8, low=12.3, close=12.6)})

    outcomes = MonitoringService(repo, CONFIG).monitor(ENTRY_DATE)

    assert outcomes[0].recommendation == "SELL_TARGET"
    updated = repo.get_position(position.position_id)
    assert updated.status == "CLOSED"
    assert updated.exit_price == pytest.approx(12.5)  # filled at open, not target_price
    assert updated.exit_reason == "SELL_TARGET"


def test_target_reached_intraday_not_at_open(repo: SandboxRepository, monkeypatch):
    position = _open_position(repo, entry_price=10.0)  # target = 12.0
    _patch_bars(monkeypatch, {ENTRY_DATE: _bar(open_=10.5, high=12.4, low=10.4, close=11.8)})

    outcomes = MonitoringService(repo, CONFIG).monitor(ENTRY_DATE)

    assert outcomes[0].recommendation == "SELL_TARGET"
    updated = repo.get_position(position.position_id)
    assert updated.exit_price == pytest.approx(12.0)  # filled at target_price, not high


def test_no_target_hit_produces_hold(repo: SandboxRepository, monkeypatch):
    position = _open_position(repo, entry_price=10.0)  # target = 12.0
    _patch_bars(monkeypatch, {ENTRY_DATE: _bar(open_=10.1, high=10.5, low=9.9, close=10.3)})

    outcomes = MonitoringService(repo, CONFIG).monitor(ENTRY_DATE)

    assert outcomes[0].recommendation == "HOLD"
    updated = repo.get_position(position.position_id)
    assert updated.status == "OPEN"
    assert updated.current_holding_day_count == 1


def test_time_exit_fires_on_holding_day_20(repo: SandboxRepository, monkeypatch):
    position = _open_position(repo, entry_price=10.0)
    service = MonitoringService(repo, CONFIG)

    # 19 HOLD days (holding days 1-19), never touching target.
    day = ENTRY_DATE
    bars = {}
    for i in range(19):
        bars[day] = _bar(open_=10.1, high=10.5, low=9.9, close=10.2)
        day = date.fromordinal(day.toordinal() + 1)
    # Day 20: still below target, must time-exit at close.
    bars[day] = _bar(open_=10.3, high=10.6, low=10.1, close=10.4)
    _patch_bars(monkeypatch, bars)

    last_outcome = None
    current = ENTRY_DATE
    for _ in range(20):
        result = service.monitor(current)
        last_outcome = result[0] if result else None
        if last_outcome and last_outcome.recommendation in ("SELL_TARGET", "SELL_TIME"):
            break
        current = date.fromordinal(current.toordinal() + 1)

    assert last_outcome.recommendation == "SELL_TIME"
    assert last_outcome.holding_day_count == 20
    updated = repo.get_position(position.position_id)
    assert updated.status == "CLOSED"
    assert updated.exit_price == pytest.approx(10.4)  # closing price of holding day 20
    assert updated.exit_reason == "SELL_TIME"


def test_no_premature_time_exit_before_holding_day_20(repo: SandboxRepository, monkeypatch):
    _open_position(repo, entry_price=10.0)
    service = MonitoringService(repo, CONFIG)

    day = ENTRY_DATE
    bars = {}
    for i in range(10):
        bars[day] = _bar(open_=10.1, high=10.5, low=9.9, close=10.2)
        day = date.fromordinal(day.toordinal() + 1)
    _patch_bars(monkeypatch, bars)

    current = ENTRY_DATE
    for _ in range(10):
        result = service.monitor(current)
        assert result[0].recommendation == "HOLD"
        current = date.fromordinal(current.toordinal() + 1)


def test_no_duplicate_exit_when_monitored_twice_same_day(repo: SandboxRepository, monkeypatch):
    position = _open_position(repo, entry_price=10.0)
    _patch_bars(monkeypatch, {ENTRY_DATE: _bar(open_=12.5, high=12.8, low=12.3, close=12.6)})
    service = MonitoringService(repo, CONFIG)

    service.monitor(ENTRY_DATE)
    # Position is now CLOSED -- get_open_positions() must no longer return it, so a
    # second monitor() call for the same date is a no-op for this position.
    second_run_outcomes = service.monitor(ENTRY_DATE)

    assert second_run_outcomes == []
    snapshots = repo.get_snapshots_for_position(position.position_id)
    assert len(snapshots) == 1  # not duplicated
    transactions = repo.get_transactions_for_position(position.position_id)
    assert len(transactions) == 1  # single SELL, not two


def test_missing_data_blocks_monitoring_and_does_not_sell(repo: SandboxRepository, monkeypatch):
    position = _open_position(repo, entry_price=10.0)
    _patch_bars(monkeypatch, {})  # no bar at all for ENTRY_DATE

    outcomes = MonitoringService(repo, CONFIG).monitor(ENTRY_DATE)

    assert outcomes[0].recommendation == "MONITORING_BLOCKED"
    updated = repo.get_position(position.position_id)
    assert updated.status == "OPEN"
    events = repo.get_data_quality_events_for_date(ENTRY_DATE)
    assert len(events) == 1
    assert events[0].symbol == "AAA"
    # The gap is recorded as an auditable recommendation event too, not just silence.
    from stock_analyzer.sandbox.application.recommendation_service import RecommendationService

    history = RecommendationService(repo).position_history(position.position_id)
    assert [r.recommendation for r in history] == ["MONITORING_BLOCKED"]


def test_missing_data_never_triggers_a_sale_no_matter_how_long(repo: SandboxRepository, monkeypatch):
    # Per review: a calendar-days proxy must never mechanically liquidate a position.
    # This asserts the position survives many consecutive missing-data days untouched.
    position = _open_position(repo, entry_price=10.0)
    service = MonitoringService(repo, CONFIG)
    _patch_bars(monkeypatch, {})  # no data ever

    current = ENTRY_DATE
    outcomes_seen = []
    for _ in range(30):
        result = service.monitor(current)
        outcomes_seen.extend(o.recommendation for o in result)
        current = date.fromordinal(current.toordinal() + 1)

    assert outcomes_seen  # monitoring did run each day
    assert set(outcomes_seen) == {"MONITORING_BLOCKED"}
    assert "SELL_DATA_FAILURE" not in outcomes_seen
    updated = repo.get_position(position.position_id)
    assert updated.status == "OPEN"  # still unresolved, never auto-closed
    assert updated.exit_reason is None
