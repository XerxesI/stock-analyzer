"""Tests for EXP-005's decision-quality report generation -- Revision 5,
Section 25, Stage 14.

`percentile_rank` and `compute_selection_quality` are pure and get exact
hand-computed unit tests. `compute_run_summary` and its four sub-summaries are
exercised end-to-end against a small, real, FK-enforced SQLite fixture (rather
than duck-typed fakes) since the whole point of this module is wiring together
many already-tested repository queries and Stage 12-13 diagnostic functions --
the risk here is join/filter bugs, not new arithmetic, so the fixture checks
counts/rates/means rather than re-deriving every horizon by hand (that
exhaustive numeric coverage already exists in the Stage 12-13 test files).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder, EntryOrderAttempt
from stock_analyzer.sandbox.domain.position import PositionSnapshot, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import HOLD, SELL_TARGET
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.exp005.diagnostics._shared import full_market_calendar
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import DiagnosticsContext
from stock_analyzer.sandbox.exp005.diagnostics.report_generator import (
    RunQualitySummary,
    compute_run_summary,
    compute_selection_quality,
    percentile_rank,
)
from stock_analyzer.sandbox.exp005.domain.accounting import compute_buy_accounting, compute_sell_accounting
from stock_analyzer.sandbox.exp005.domain.admission import ACCEPTED, CONVERTED, NO_CAPACITY, PortfolioAdmission, SlotReservation
from stock_analyzer.sandbox.exp005.domain.equity_snapshot import PortfolioEquitySnapshot
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution
from stock_analyzer.sandbox.exp005.domain.units import price_units_to_float, quantity_units_to_float, to_price_units
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

REPLAY_ID = "replay-1"
NOW = datetime.now(timezone.utc)


class _FakeManifest:
    def __init__(self, outcome_data_end_date: date) -> None:
        self.outcome_data_end_date = outcome_data_end_date


def _bar(symbol: str, d: date, o: float, h: float, low: float, c: float) -> dict:
    return {"symbol": symbol, "date": pd.Timestamp(d), "Open": o, "High": h, "Low": low, "Close": c}


def _build_fixture():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    sandbox_repo = SandboxRepository(conn)
    portfolio_repo = PortfolioRepository(conn)

    sandbox_repo.create_run(
        SandboxRun(
            run_id="run-1", as_of_date=date(2026, 1, 5), command="generate-candidates",
            started_at=NOW, configuration_hash="test",
        )
    )

    # --- AAA: fills, then closes SELL_TARGET via an ambiguous intraday touch.
    aaa_candidate = RankedCandidate(
        candidate_id="2026-01-05:AAA", run_id="run-1", as_of_date=date(2026, 1, 5), symbol="AAA", daily_rank=1,
        model_score=0.8, signal_close=10.0, atr14=0.5, max_entry_price=10.1, shadow_top10=True, actionable=True,
        exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
    )
    sandbox_repo.insert_ranked_candidate(aaa_candidate)
    aaa_order = EntryOrder(
        order_id=EntryOrder.make_id(aaa_candidate.candidate_id), candidate_id=aaa_candidate.candidate_id,
        symbol="AAA", signal_date=date(2026, 1, 5), created_date=date(2026, 1, 5), valid_until=date(2026, 1, 7),
        max_entry_price=10.1, status="FILLED", fill_date=date(2026, 1, 6), fill_price=10.05, fill_reason="FILLED_AT_OPEN",
    )
    sandbox_repo.create_entry_order(aaa_order)
    sandbox_repo.insert_entry_order_attempt(
        EntryOrderAttempt(
            attempt_id=EntryOrderAttempt.make_id(aaa_order.order_id, date(2026, 1, 6)), order_id=aaa_order.order_id,
            symbol="AAA", attempt_date=date(2026, 1, 6), session_open=10.05, session_high=10.6, session_low=9.9,
            session_close=10.3, max_entry_price=10.1, outcome="FILLED_AT_OPEN", fill_price=10.05,
            reason="next_day_open<=max_entry_price",
        )
    )
    aaa_position = VirtualPosition(
        position_id=VirtualPosition.make_id("AAA", date(2026, 1, 6)), symbol="AAA", candidate_id=aaa_candidate.candidate_id,
        order_id=aaa_order.order_id, signal_date=date(2026, 1, 5), entry_date=date(2026, 1, 6), entry_price=10.05,
        quantity=10.0, initial_rank=1, initial_model_score=0.8, signal_close=10.0, max_entry_price=10.1,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=11.0,
        planned_time_exit_date=date(2026, 2, 2),
    )
    sandbox_repo.create_position(aaa_position)
    sandbox_repo.insert_position_snapshot(
        PositionSnapshot(
            snapshot_id=PositionSnapshot.make_id(aaa_position.position_id, date(2026, 1, 7)),
            position_id=aaa_position.position_id, symbol="AAA", as_of_date=date(2026, 1, 7), close_price=10.6,
            daily_return=None, cumulative_unrealized_return=0.0547, holding_day_count=2, mfe=0.0547, mae=-0.0149,
            distance_to_target=0.0377, current_rank=1, current_model_score=0.8, rank_change_from_entry=0,
            current_adv_quintile="adv_q1", current_market_regime="Bull_Normal", data_quality_status="OK",
            recommendation=HOLD,
        )
    )
    sandbox_repo.close_position(
        aaa_position.position_id, exit_date=date(2026, 1, 8), exit_price=11.0, exit_reason=SELL_TARGET,
        realized_return=0.0945, final_holding_day_count=3, final_mfe=0.1144, final_mae=-0.0149,
    )
    aaa_admission = PortfolioAdmission(
        admission_id=aaa_candidate.candidate_id, replay_id=REPLAY_ID, candidate_id=aaa_candidate.candidate_id,
        symbol="AAA", as_of_date=date(2026, 1, 5), decision=ACCEPTED, rank_at_admission=1,
        slot_budget_units=1_000_000, reason=None, created_at=NOW,
    )
    portfolio_repo.insert_admission(aaa_admission)
    # A real ACCEPTED admission always gets a matching reservation created
    # atomically alongside it (Section 8.2) -- needed here since Stage 11-15's
    # closure cycle made opportunity_cost.py's capacity-occupancy reconstruction
    # look this up directly, not just the admission/order.
    portfolio_repo.insert_reservation(
        SlotReservation(
            reservation_id=f"{aaa_admission.admission_id}:reservation", replay_id=REPLAY_ID,
            admission_id=aaa_admission.admission_id, candidate_id=aaa_candidate.candidate_id, symbol="AAA",
            reserved_amount_units=1_000_000, status=CONVERTED, created_at=NOW, resolved_at=NOW,
        )
    )
    buy_accounting = compute_buy_accounting(raw_fill_price=10.05, slot_budget=110.0, commission=1.0, slippage_rate=0.001)
    portfolio_repo.append_execution(
        Execution(
            execution_id=f"{aaa_position.position_id}:BUY", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
            order_id=aaa_order.order_id, candidate_id=aaa_candidate.candidate_id, position_id=aaa_position.position_id,
            symbol="AAA", side=BUY, decision_date=date(2026, 1, 6), execution_date=date(2026, 1, 6),
            raw_market_fill_price_units=to_price_units(10.05),
            effective_fill_price_units=buy_accounting.effective_fill_price_units,
            quantity_units=buy_accounting.quantity_units, gross_notional_units=buy_accounting.gross_notional_units,
            commission_units=100, slippage_rate_units=10, slippage_cost_units=buy_accounting.slippage_cost_units,
            net_cash_flow_units=buy_accounting.net_cash_flow_units, fill_reason="FILLED_AT_OPEN",
            market_data_snapshot_id="snap-1", created_at=NOW,
        )
    )
    effective_entry_price = price_units_to_float(buy_accounting.effective_fill_price_units)
    quantity = quantity_units_to_float(buy_accounting.quantity_units)
    sell_accounting = compute_sell_accounting(raw_fill_price=11.0, quantity=quantity, commission=1.0, slippage_rate=0.001)
    portfolio_repo.append_execution(
        Execution(
            execution_id=f"{aaa_position.position_id}:SELL", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
            order_id=None, candidate_id=aaa_candidate.candidate_id, position_id=aaa_position.position_id,
            symbol="AAA", side=SELL, decision_date=date(2026, 1, 8), execution_date=date(2026, 1, 8),
            raw_market_fill_price_units=to_price_units(11.0),
            effective_fill_price_units=sell_accounting.effective_fill_price_units,
            quantity_units=sell_accounting.quantity_units, gross_notional_units=sell_accounting.gross_notional_units,
            commission_units=100, slippage_rate_units=10, slippage_cost_units=sell_accounting.slippage_cost_units,
            net_cash_flow_units=sell_accounting.net_cash_flow_units, fill_reason=SELL_TARGET,
            market_data_snapshot_id="snap-1", created_at=NOW,
        )
    )
    effective_exit_price = price_units_to_float(sell_accounting.effective_fill_price_units)

    # --- CCC: an EXPIRED order (never fills) -- contributes to buy.fill_rate's denominator.
    ccc_candidate = RankedCandidate(
        candidate_id="2026-01-05:CCC", run_id="run-1", as_of_date=date(2026, 1, 5), symbol="CCC", daily_rank=2,
        model_score=0.5, signal_close=30.0, atr14=0.5, max_entry_price=30.1, shadow_top10=True, actionable=True,
        exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
    )
    sandbox_repo.insert_ranked_candidate(ccc_candidate)
    ccc_order = EntryOrder(
        order_id=EntryOrder.make_id(ccc_candidate.candidate_id), candidate_id=ccc_candidate.candidate_id,
        symbol="CCC", signal_date=date(2026, 1, 5), created_date=date(2026, 1, 5), valid_until=date(2026, 1, 7),
        max_entry_price=30.1, status="EXPIRED", no_fill_reason="entire_session_above_ceiling",
    )
    sandbox_repo.create_entry_order(ccc_order)

    # --- BBB: NO_CAPACITY admission.
    bbb_candidate = RankedCandidate(
        candidate_id="2026-01-05:BBB", run_id="run-1", as_of_date=date(2026, 1, 5), symbol="BBB", daily_rank=11,
        model_score=0.3, signal_close=20.0, atr14=0.5, max_entry_price=20.1, shadow_top10=False, actionable=True,
        exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
    )
    sandbox_repo.insert_ranked_candidate(bbb_candidate)
    portfolio_repo.insert_admission(
        PortfolioAdmission(
            admission_id=bbb_candidate.candidate_id, replay_id=REPLAY_ID, candidate_id=bbb_candidate.candidate_id,
            symbol="BBB", as_of_date=date(2026, 1, 5), decision=NO_CAPACITY, rank_at_admission=11,
            slot_budget_units=None, reason="10/10 slots reserved", created_at=NOW,
        )
    )

    for d, open_count, reserved_count in [
        (date(2026, 1, 5), 0, 1), (date(2026, 1, 6), 1, 0), (date(2026, 1, 8), 0, 0),
    ]:
        portfolio_repo.append_equity_snapshot(
            PortfolioEquitySnapshot(
                snapshot_id=f"{REPLAY_ID}:{d.isoformat()}", replay_id=REPLAY_ID, as_of_date=d,
                cash_units=1_000_000, reserved_capital_units=0, open_position_market_value_units=0,
                total_equity_units=1_000_000, open_position_count=open_count, reserved_order_count=reserved_count,
                cumulative_commissions_units=200, cumulative_slippage_cost_units=20, created_at=NOW,
            )
        )

    prices = pd.DataFrame(
        [
            _bar("AAA", date(2026, 1, 5), 10.0, 10.2, 9.8, 10.0),
            _bar("AAA", date(2026, 1, 6), 10.05, 10.6, 9.9, 10.3),
            _bar("AAA", date(2026, 1, 7), 10.3, 10.8, 10.1, 10.6),
            _bar("AAA", date(2026, 1, 8), 10.6, 11.2, 10.5, 11.0),
            _bar("AAA", date(2026, 1, 9), 11.0, 11.3, 10.8, 11.1),
            _bar("AAA", date(2026, 1, 10), 11.1, 11.4, 11.0, 11.3),
            _bar("BBB", date(2026, 1, 5), 20.0, 20.3, 19.8, 20.0),
            _bar("BBB", date(2026, 1, 6), 20.05, 20.6, 19.9, 20.3),
            _bar("BBB", date(2026, 1, 7), 20.3, 20.8, 20.1, 20.6),
            _bar("BBB", date(2026, 1, 8), 20.6, 21.2, 20.5, 21.0),
            _bar("BBB", date(2026, 1, 9), 21.0, 21.1, 20.9, 21.0),
            _bar("BBB", date(2026, 1, 10), 21.0, 21.2, 20.9, 21.1),
            _bar("CCC", date(2026, 1, 5), 30.0, 30.2, 29.8, 30.0),
            _bar("CCC", date(2026, 1, 6), 30.5, 30.8, 30.3, 30.6),
            _bar("CCC", date(2026, 1, 7), 30.6, 30.9, 30.4, 30.7),
        ]
    )
    calendar = full_market_calendar(prices)
    manifest = _FakeManifest(date(2026, 1, 10))
    context = DiagnosticsContext(
        manifest=manifest, replay_id=REPLAY_ID, prices_df=prices,
        portfolio_repo=portfolio_repo, sandbox_repo=sandbox_repo,
    )
    return context, calendar, effective_entry_price, effective_exit_price


def test_compute_run_summary_end_to_end_counts_and_key_means():
    context, calendar, effective_entry_price, effective_exit_price = _build_fixture()
    expected_realized_return_pct = (effective_exit_price - effective_entry_price) / effective_entry_price

    summary = compute_run_summary(context, REPLAY_ID, "B", None, calendar)

    assert summary.buy.filled_count == 1
    assert summary.buy.expired_count == 1
    assert summary.buy.fill_rate == pytest.approx(0.5)
    assert summary.buy.entry_session_ambiguity_count == 0  # FILLED_AT_OPEN, not CEILING

    assert summary.hold.hold_decision_count == 1
    assert summary.hold.unresolved_rate == pytest.approx(0.0)  # AAA's only HOLD position closed

    assert summary.sell.closed_position_count == 1
    assert summary.sell.target_exit_count == 1
    assert summary.sell.time_exit_count == 0
    # realized return uses EXP-005's own effective prices, never core's raw ones.
    assert summary.sell.mean_realized_return_pct == pytest.approx(expected_realized_return_pct)

    assert summary.capacity.no_capacity_count == 1
    assert summary.capacity.total_equity_snapshot_days == 3
    assert summary.capacity.idle_cash_day_count == 1  # Jan 8: open=0, reserved=0
    assert summary.capacity.mean_open_position_count == pytest.approx((0 + 1 + 0) / 3)
    # BBB's hypothetical ADR-007 fill: Jan 6 open=20.05 <= ceiling 20.1 -> FILLED_AT_OPEN.
    assert summary.capacity.hypothetical_fill_rate == pytest.approx(1.0)
    assert summary.capacity.accepted_mean_realized_return_pct == pytest.approx(expected_realized_return_pct)


def test_ambiguous_target_exit_excludes_exit_session_but_not_the_known_exit_price():
    """AAA's exit was an intraday-high touch (Jan 8 open=10.6 < target=11.0, but
    high=11.2 >= 11.0) -- Section 20's ambiguity rule excludes Jan 8's own
    High/Low from the MFE/MAE window (Jan 7's High of 10.8 would otherwise be the
    max), but the known, certain realized exit price is always still a valid
    boundary candidate (Stage 11-15 closure, finding 4) -- and since the exit
    price (~10.99) exceeds Jan 7's High (10.8), MFE is reported as the exit
    price, never understated below what the position demonstrably reached."""

    context, calendar, effective_entry_price, effective_exit_price = _build_fixture()
    summary = compute_run_summary(context, REPLAY_ID, "B", None, calendar)

    expected_mfe_pct = (effective_exit_price - effective_entry_price) / effective_entry_price
    assert summary.sell.mean_mfe_captured_pct == pytest.approx(expected_mfe_pct)
    assert summary.sell.mean_mfe_captured_pct >= summary.sell.mean_realized_return_pct


def _closed_position_fixture(sandbox_repo, portfolio_repo, symbol, entry_date, exit_date, price):
    run_id = f"run-{symbol}"
    sandbox_repo.create_run(
        SandboxRun(run_id=run_id, as_of_date=entry_date, command="generate-candidates", started_at=NOW, configuration_hash="t")
    )
    candidate_id = f"{entry_date.isoformat()}:{symbol}"
    sandbox_repo.insert_ranked_candidate(
        RankedCandidate(
            candidate_id=candidate_id, run_id=run_id, as_of_date=entry_date, symbol=symbol, daily_rank=1,
            model_score=0.5, signal_close=price, atr14=1.0, max_entry_price=price * 1.01, shadow_top10=True,
            actionable=True, exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
        )
    )
    order = EntryOrder(
        order_id=EntryOrder.make_id(candidate_id), candidate_id=candidate_id, symbol=symbol, signal_date=entry_date,
        created_date=entry_date, valid_until=exit_date, max_entry_price=price * 1.01, status="FILLED",
        fill_date=entry_date, fill_price=price, fill_reason="FILLED_AT_OPEN",
    )
    sandbox_repo.create_entry_order(order)
    position = VirtualPosition(
        position_id=VirtualPosition.make_id(symbol, entry_date), symbol=symbol, candidate_id=candidate_id,
        order_id=order.order_id, signal_date=entry_date, entry_date=entry_date, entry_price=price, quantity=10.0,
        initial_rank=1, initial_model_score=0.5, signal_close=price, max_entry_price=price * 1.01,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=price * 10,
        planned_time_exit_date=exit_date, status="CLOSED", exit_date=exit_date, exit_price=price,
        exit_reason="SELL_TIME",
    )
    sandbox_repo.create_position(position)

    buy_accounting = compute_buy_accounting(raw_fill_price=price, slot_budget=price * 11, commission=1.0, slippage_rate=0.0)
    portfolio_repo.append_execution(
        Execution(
            execution_id=f"{position.position_id}:BUY", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
            order_id=order.order_id, candidate_id=candidate_id, position_id=position.position_id, symbol=symbol,
            side=BUY, decision_date=entry_date, execution_date=entry_date,
            raw_market_fill_price_units=to_price_units(price), effective_fill_price_units=buy_accounting.effective_fill_price_units,
            quantity_units=buy_accounting.quantity_units, gross_notional_units=buy_accounting.gross_notional_units,
            commission_units=100, slippage_rate_units=0, slippage_cost_units=buy_accounting.slippage_cost_units,
            net_cash_flow_units=buy_accounting.net_cash_flow_units, fill_reason="FILLED_AT_OPEN",
            market_data_snapshot_id="snap-1", created_at=NOW,
        )
    )
    quantity = quantity_units_to_float(buy_accounting.quantity_units)
    sell_accounting = compute_sell_accounting(raw_fill_price=price, quantity=quantity, commission=1.0, slippage_rate=0.0)
    portfolio_repo.append_execution(
        Execution(
            execution_id=f"{position.position_id}:SELL", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
            order_id=None, candidate_id=candidate_id, position_id=position.position_id, symbol=symbol,
            side=SELL, decision_date=exit_date, execution_date=exit_date,
            raw_market_fill_price_units=to_price_units(price), effective_fill_price_units=sell_accounting.effective_fill_price_units,
            quantity_units=sell_accounting.quantity_units, gross_notional_units=sell_accounting.gross_notional_units,
            commission_units=100, slippage_rate_units=0, slippage_cost_units=sell_accounting.slippage_cost_units,
            net_cash_flow_units=sell_accounting.net_cash_flow_units, fill_reason="SELL_TIME",
            market_data_snapshot_id="snap-1", created_at=NOW,
        )
    )
    return position


def test_censored_horizon_observation_never_distorts_the_complete_horizon_mean():
    """AAA exits early (Jan 6) and has a full 5-session post-exit window, all
    flat -- a complete, unremarkable 0% forward return. BBB exits late (Jan 12)
    with only ONE session of data left before outcome_data_end_date (Jan 13) --
    censored (END_OF_EXPERIMENT) -- and that one session has an EXTREME 900%
    spike. If the censored observation were blended into the headline mean
    (Stage 11-15 closure, finding 2), it would swing the mean from 0.0 to 4.5;
    correctly excluded, the mean must still be exactly AAA's own 0.0."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    sandbox_repo = SandboxRepository(conn)
    portfolio_repo = PortfolioRepository(conn)

    _closed_position_fixture(sandbox_repo, portfolio_repo, "AAA", date(2026, 1, 5), date(2026, 1, 6), price=100.0)
    _closed_position_fixture(sandbox_repo, portfolio_repo, "BBB", date(2026, 1, 5), date(2026, 1, 12), price=200.0)

    rows = []
    for d in [date(2026, 1, 5), date(2026, 1, 6)] + [date(2026, 1, 6 + i) for i in range(1, 8)]:
        rows.append(_bar("AAA", d, 105.0, 106.0, 104.0, 105.0))
    rows[0] = _bar("AAA", date(2026, 1, 5), 100.0, 101.0, 99.0, 100.0)
    rows[1] = _bar("AAA", date(2026, 1, 6), 100.0, 106.0, 99.0, 105.0)
    for d in [date(2026, 1, 5 + i) for i in range(8)]:
        rows.append(_bar("BBB", d, 200.0, 201.0, 199.0, 200.0))
    rows.append(_bar("BBB", date(2026, 1, 13), 200.0, 2000.0, 200.0, 2000.0))

    prices = pd.DataFrame(rows)
    calendar = full_market_calendar(prices)
    context = DiagnosticsContext(
        manifest=_FakeManifest(date(2026, 1, 13)), replay_id=REPLAY_ID, prices_df=prices,
        portfolio_repo=portfolio_repo, sandbox_repo=sandbox_repo,
    )

    summary = compute_run_summary(context, REPLAY_ID, "B", None, calendar)

    h5 = 5
    assert summary.sell.horizon_complete_count[h5] == 1
    assert summary.sell.horizon_censored_end_of_experiment_count[h5] == 1
    assert summary.sell.horizon_censored_missing_market_data_count[h5] == 0
    # AAA's own complete-window return: effective_exit=100 (the fixture's raw
    # fill price, unaffected by the flat-105 post-exit bars), last observed
    # close in the 5-session window = 105 -> (105-100)/100 = 0.05. A blended
    # mean including BBB's censored 900% spike would be nowhere close to this
    # (roughly (0.05 + 9.0) / 2 = 4.525) -- the assertion below fails loudly if
    # the censored observation leaks into the headline mean.
    assert summary.sell.horizon_mean_forward_return_pct[h5] == pytest.approx(0.05)


# ------------------------------------------------------------------- pure helpers


def test_target_reached_rate_asymmetric_censoring_rule():
    """A censored observation that already reached the target is a resolved
    success (the fact is certain even from a partial window). A censored
    observation that has NOT reached the target is excluded entirely -- it is
    unresolved, not a completed failure, and must never drag the rate down."""

    from stock_analyzer.sandbox.exp005.diagnostics.report_generator import _target_reached_rate

    class _FakeHorizonResult:
        def __init__(self, target_reached: bool, is_censored: bool) -> None:
            self.target_reached = target_reached
            self.is_censored = is_censored

    # 2 complete (1 hit, 1 miss), 1 censored-but-already-hit (counts as a
    # resolved success), 1 censored-and-not-yet-hit (excluded entirely).
    results = [
        _FakeHorizonResult(target_reached=True, is_censored=False),
        _FakeHorizonResult(target_reached=False, is_censored=False),
        _FakeHorizonResult(target_reached=True, is_censored=True),
        _FakeHorizonResult(target_reached=False, is_censored=True),
    ]

    rate = _target_reached_rate(results)

    # Denominator is 3 (the censored-and-not-yet-hit observation excluded);
    # numerator is 2 (both True observations, including the censored one).
    assert rate == pytest.approx(2 / 3)


def test_target_reached_rate_none_when_nothing_resolved():
    from stock_analyzer.sandbox.exp005.diagnostics.report_generator import _target_reached_rate

    class _FakeHorizonResult:
        def __init__(self, target_reached: bool, is_censored: bool) -> None:
            self.target_reached = target_reached
            self.is_censored = is_censored

    results = [_FakeHorizonResult(target_reached=False, is_censored=True)]

    assert _target_reached_rate(results) is None


def test_percentile_rank_hand_computed():
    distribution = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile_rank(3.0, distribution) == pytest.approx(60.0)  # 3 of 5 values <= 3.0
    assert percentile_rank(5.0, distribution) == pytest.approx(100.0)
    assert percentile_rank(0.5, distribution) == pytest.approx(0.0)
    assert percentile_rank(3.0, []) is None


def _summary_with(realized_return_pct: float | None) -> RunQualitySummary:
    from stock_analyzer.sandbox.exp005.diagnostics.report_generator import (
        BuyQualitySummary,
        CapacityQualitySummary,
        HoldQualitySummary,
        SellQualitySummary,
    )

    buy = BuyQualitySummary(
        filled_count=0, expired_count=0, fill_rate=None, entry_session_ambiguity_count=0,
        mean_entry_gap_pct=None, mean_slippage_cost=None, mean_slippage_rate_pct=None, target_hit_rate=None,
        entry_gap_pct_distribution=(), slippage_cost_distribution=(), horizon_mean_forward_return_pct={},
        horizon_mean_mfe_pct={}, horizon_mean_mae_pct={}, horizon_mean_sessions_to_mfe={},
        horizon_mean_sessions_to_mae={}, horizon_complete_count={},
        horizon_censored_end_of_experiment_count={}, horizon_censored_missing_market_data_count={},
    )
    hold = HoldQualitySummary(
        hold_decision_count=0, profitable_continuation_rate=None, adverse_continuation_rate=None,
        target_eventually_reached_rate=None, time_exit_eventually_reached_rate=None, unresolved_rate=None,
        horizon_mean_forward_return_pct={}, horizon_mean_mfe_pct={}, horizon_mean_mae_pct={},
        horizon_target_reached_rate={}, horizon_complete_count={},
        horizon_censored_end_of_experiment_count={}, horizon_censored_missing_market_data_count={},
        by_holding_age_bucket=(), by_unrealized_return_bucket=(),
    )
    sell = SellQualitySummary(
        closed_position_count=1, mean_realized_return_pct=realized_return_pct, mean_mfe_captured_pct=None,
        mean_peak_to_exit_giveback_pct=None, mean_exit_efficiency=None, target_exit_count=0, time_exit_count=0,
        target_exit_mean_realized_return_pct=None, time_exit_mean_realized_return_pct=None,
        horizon_mean_forward_return_pct={}, horizon_mean_max_high_pct={}, horizon_mean_min_low_pct={},
        horizon_target_reached_rate={}, horizon_complete_count={},
        horizon_censored_end_of_experiment_count={}, horizon_censored_missing_market_data_count={},
        total_censored_post_exit_observations=0,
    )
    capacity = CapacityQualitySummary(
        no_capacity_count=0, hypothetical_fill_rate=None, horizon_mean_missed_mfe_pct={}, horizon_mean_missed_mae_pct={},
        horizon_complete_count={}, horizon_censored_end_of_experiment_count={}, horizon_censored_missing_market_data_count={},
        mean_open_position_count=None, mean_reserved_order_count=None,
        idle_cash_day_count=0, total_equity_snapshot_days=0, accepted_mean_realized_return_pct=None,
        rejected_mean_horizon20_return_pct=None,
    )
    return RunQualitySummary(replay_id="r", variant_id="D", control_seed=1, buy=buy, hold=hold, sell=sell, capacity=capacity)


def test_compute_selection_quality_percentile_wiring():
    variant_b = _summary_with(0.10)
    variant_b = RunQualitySummary(
        replay_id="replay-b", variant_id="B", control_seed=None,
        buy=variant_b.buy, hold=variant_b.hold, sell=variant_b.sell, capacity=variant_b.capacity,
    )
    variant_d_seeds = [_summary_with(v) for v in [0.01, 0.02, 0.03, 0.04, 0.20]]

    report = compute_selection_quality(variant_b, variant_d_seeds)

    realized_return_metric = next(m for m in report.metrics if m.metric_name == "realized_return_pct")
    assert realized_return_metric.variant_b_value == pytest.approx(0.10)
    assert realized_return_metric.variant_d_distribution == (0.01, 0.02, 0.03, 0.04, 0.20)
    assert realized_return_metric.variant_d_mean == pytest.approx(0.06)
    assert realized_return_metric.variant_b_percentile_rank_within_d == pytest.approx(80.0)  # 4 of 5 <= 0.10
    assert report.variant_b_replay_id == "replay-b"
    assert report.variant_d_seed_count == 5


def test_compute_selection_quality_handles_none_values_gracefully():
    variant_b = _summary_with(None)
    variant_d_seeds = [_summary_with(None), _summary_with(0.05)]

    report = compute_selection_quality(variant_b, variant_d_seeds)

    realized_return_metric = next(m for m in report.metrics if m.metric_name == "realized_return_pct")
    assert realized_return_metric.variant_b_value is None
    assert realized_return_metric.variant_b_percentile_rank_within_d is None
    assert realized_return_metric.variant_d_distribution == (0.05,)  # None entries filtered out
