"""Tests for EXP-005's financial-feasibility report -- Revision 5, Section 10
(Stage 11-15 closure cycle, finding 1: this module was entirely missing).

`_compute_drawdown`/`_compute_quarterly_returns` are pure and get exact
hand-computed unit tests against a known equity path. `compute_financial_performance`
is exercised against a real, FK-enforced SQLite fixture with two closed trades
(one win, one loss) and one dominant unresolved open winner. A separate small
fixture isolates the "positive headline return that flips negative after
removing the dominant winner" arithmetic. `compute_feasibility_verdict` is pure
and tested directly against constructed `FinancialPerformanceReport` objects,
covering Variant B below/at/above the control percentile and the
never-silently-a-pass rule for undetermined criteria.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.position import CLOSED, OPEN, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import SELL_TARGET
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.exp005.config import DEFAULT_CONTROL_SEEDS, VARIANT_B, VARIANT_D
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import DiagnosticsContext
from stock_analyzer.sandbox.exp005.diagnostics.financial_performance import (
    CRITERION_BEATS_CONTROL_PERCENTILE,
    CRITERION_LARGEST_WINNER_CONCENTRATION_WITHIN_THRESHOLD,
    CRITERION_MAX_DRAWDOWN_WITHIN_THRESHOLD,
    CRITERION_POSITIVE_NET_PNL,
    CRITERION_PROFIT_FACTOR_WITHIN_THRESHOLD,
    ControlGroupValidationError,
    DrawdownResult,
    FinancialPerformanceReport,
    _compute_drawdown,
    _compute_quarterly_returns,
    compute_feasibility_verdict,
    compute_financial_performance,
)
from stock_analyzer.sandbox.exp005.domain.accounting import compute_buy_accounting, compute_sell_accounting
from stock_analyzer.sandbox.exp005.domain.equity_snapshot import PortfolioEquitySnapshot
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL, Execution
from stock_analyzer.sandbox.exp005.domain.units import (
    money_units_to_float,
    quantity_units_to_float,
    to_money_units,
    to_price_units,
    to_rate_units,
)
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

REPLAY_ID = "replay-1"
NOW = datetime.now(timezone.utc)


class _FakeManifest:
    def __init__(self) -> None:
        self.outcome_data_end_date = date(2026, 12, 31)


class _FakeSnapshot:
    def __init__(self, as_of_date: date, total_equity_units: int, open_position_market_value_units: int = 0) -> None:
        self.as_of_date = as_of_date
        self.total_equity_units = total_equity_units
        self.open_position_market_value_units = open_position_market_value_units


# ------------------------------------------------------------------ drawdown


def test_drawdown_hand_computed_known_equity_path():
    snapshots = [
        _FakeSnapshot(date(2026, 1, 1), 100_00),
        _FakeSnapshot(date(2026, 1, 2), 110_00),
        _FakeSnapshot(date(2026, 1, 3), 90_00),
        _FakeSnapshot(date(2026, 1, 4), 95_00),
        _FakeSnapshot(date(2026, 1, 5), 80_00),
        _FakeSnapshot(date(2026, 1, 6), 120_00),
    ]

    result = _compute_drawdown(snapshots)

    assert result.max_drawdown_pct == pytest.approx((110 - 80) / 110)
    assert result.peak_date == date(2026, 1, 2)
    assert result.peak_equity == pytest.approx(110.0)
    assert result.trough_date == date(2026, 1, 5)
    assert result.trough_equity == pytest.approx(80.0)


def test_drawdown_zero_when_equity_never_declines():
    snapshots = [_FakeSnapshot(date(2026, 1, 1), 100_00), _FakeSnapshot(date(2026, 1, 2), 110_00)]

    result = _compute_drawdown(snapshots)

    assert result.max_drawdown_pct == pytest.approx(0.0)
    assert result.peak_date is None
    assert result.trough_date is None


# --------------------------------------------------------------- quarterly returns


def test_quarterly_returns_hand_computed_boundary_arithmetic():
    snapshots = [
        _FakeSnapshot(date(2026, 1, 15), 100_00),
        _FakeSnapshot(date(2026, 2, 15), 110_00),
        _FakeSnapshot(date(2026, 3, 15), 105_00),  # last snapshot in Q1
        _FakeSnapshot(date(2026, 4, 15), 120_00),  # first snapshot in Q2
        _FakeSnapshot(date(2026, 5, 15), 130_00),
    ]

    quarters = _compute_quarterly_returns(snapshots)

    assert len(quarters) == 2
    q1, q2 = quarters
    assert (q1.year, q1.quarter) == (2026, 1)
    assert q1.start_date == date(2026, 1, 15)
    assert q1.end_date == date(2026, 3, 15)
    assert q1.start_equity == pytest.approx(100.0)
    assert q1.end_equity == pytest.approx(105.0)
    assert q1.return_pct == pytest.approx(0.05)

    assert (q2.year, q2.quarter) == (2026, 2)
    assert q2.start_date == date(2026, 4, 15)
    assert q2.end_date == date(2026, 5, 15)
    # Q2's start carries over from Q1's own ending equity (105), not a
    # separately-reset baseline.
    assert q2.start_equity == pytest.approx(105.0)
    assert q2.end_equity == pytest.approx(130.0)
    assert q2.return_pct == pytest.approx((130.0 - 105.0) / 105.0)


# ------------------------------------------------------- financial performance


def _repos():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    return SandboxRepository(conn), PortfolioRepository(conn)


def _insert_closed_trade(sandbox_repo, portfolio_repo, symbol, entry_date, exit_date, sell_price, buy_price=100.0, budget=1000.0):
    """BUY commission/slippage are zero so quantity=budget/buy_price exactly and
    net_pnl_units = quantity * (sell_price - buy_price), in cents -- easy to
    hand-verify. Returns net_pnl_units (exact int) so callers can build precise
    expectations instead of re-deriving accounting arithmetic in the test body."""

    run_id = f"run-{symbol}"
    sandbox_repo.create_run(SandboxRun(run_id=run_id, as_of_date=entry_date, command="generate-candidates", started_at=NOW, configuration_hash="t"))
    candidate_id = f"{entry_date.isoformat()}:{symbol}"
    sandbox_repo.insert_ranked_candidate(
        RankedCandidate(
            candidate_id=candidate_id, run_id=run_id, as_of_date=entry_date, symbol=symbol, daily_rank=1,
            model_score=0.5, signal_close=buy_price, atr14=1.0, max_entry_price=buy_price * 1.01, shadow_top10=True,
            actionable=True, exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
        )
    )
    order = EntryOrder(
        order_id=EntryOrder.make_id(candidate_id), candidate_id=candidate_id, symbol=symbol, signal_date=entry_date,
        created_date=entry_date, valid_until=exit_date, max_entry_price=buy_price * 1.01, status="FILLED",
        fill_date=entry_date, fill_price=buy_price, fill_reason="FILLED_AT_OPEN",
    )
    sandbox_repo.create_entry_order(order)
    position = VirtualPosition(
        position_id=VirtualPosition.make_id(symbol, entry_date), symbol=symbol, candidate_id=candidate_id,
        order_id=order.order_id, signal_date=entry_date, entry_date=entry_date, entry_price=buy_price, quantity=10.0,
        initial_rank=1, initial_model_score=0.5, signal_close=buy_price, max_entry_price=buy_price * 1.01,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=buy_price * 2,
        planned_time_exit_date=exit_date, status=CLOSED, exit_date=exit_date, exit_price=sell_price, exit_reason=SELL_TARGET,
    )
    sandbox_repo.create_position(position)

    buy_accounting = compute_buy_accounting(raw_fill_price=buy_price, slot_budget=budget, commission=0.0, slippage_rate=0.0)
    portfolio_repo.append_execution(
        Execution(
            execution_id=f"{position.position_id}:BUY", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
            order_id=order.order_id, candidate_id=candidate_id, position_id=position.position_id, symbol=symbol,
            side=BUY, decision_date=entry_date, execution_date=entry_date,
            raw_market_fill_price_units=to_price_units(buy_price), effective_fill_price_units=buy_accounting.effective_fill_price_units,
            quantity_units=buy_accounting.quantity_units, gross_notional_units=buy_accounting.gross_notional_units,
            commission_units=0, slippage_rate_units=0, slippage_cost_units=buy_accounting.slippage_cost_units,
            net_cash_flow_units=buy_accounting.net_cash_flow_units, fill_reason="FILLED_AT_OPEN",
            market_data_snapshot_id="snap-1", created_at=NOW,
        )
    )
    quantity = quantity_units_to_float(buy_accounting.quantity_units)
    sell_accounting = compute_sell_accounting(raw_fill_price=sell_price, quantity=quantity, commission=0.0, slippage_rate=0.0)
    portfolio_repo.append_execution(
        Execution(
            execution_id=f"{position.position_id}:SELL", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
            order_id=None, candidate_id=candidate_id, position_id=position.position_id, symbol=symbol,
            side=SELL, decision_date=exit_date, execution_date=exit_date,
            raw_market_fill_price_units=to_price_units(sell_price), effective_fill_price_units=sell_accounting.effective_fill_price_units,
            quantity_units=sell_accounting.quantity_units, gross_notional_units=sell_accounting.gross_notional_units,
            commission_units=0, slippage_rate_units=0, slippage_cost_units=sell_accounting.slippage_cost_units,
            net_cash_flow_units=sell_accounting.net_cash_flow_units, fill_reason=SELL_TARGET,
            market_data_snapshot_id="snap-1", created_at=NOW,
        )
    )
    net_pnl_units = buy_accounting.net_cash_flow_units + sell_accounting.net_cash_flow_units
    return position, net_pnl_units


def _insert_open_position(
    sandbox_repo, portfolio_repo, symbol, entry_date, current_close, buy_price=100.0, budget=1000.0,
    commission=0.0, slippage_rate=0.0,
):
    run_id = f"run-{symbol}-open"
    sandbox_repo.create_run(SandboxRun(run_id=run_id, as_of_date=entry_date, command="generate-candidates", started_at=NOW, configuration_hash="t"))
    candidate_id = f"{entry_date.isoformat()}:{symbol}"
    sandbox_repo.insert_ranked_candidate(
        RankedCandidate(
            candidate_id=candidate_id, run_id=run_id, as_of_date=entry_date, symbol=symbol, daily_rank=1,
            model_score=0.5, signal_close=buy_price, atr14=1.0, max_entry_price=buy_price * 1.01, shadow_top10=True,
            actionable=True, exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
        )
    )
    order = EntryOrder(
        order_id=EntryOrder.make_id(candidate_id), candidate_id=candidate_id, symbol=symbol, signal_date=entry_date,
        created_date=entry_date, valid_until=entry_date, max_entry_price=buy_price * 1.01, status="FILLED",
        fill_date=entry_date, fill_price=buy_price, fill_reason="FILLED_AT_OPEN",
    )
    sandbox_repo.create_entry_order(order)
    # The dual-accounting invariant (Stage 6 errata): the ONE quantity EXP-005
    # sizes is used everywhere, including VirtualPosition.quantity -- never a
    # separately-hardcoded value that could diverge from the execution's own
    # quantity_units once commission/slippage make sizing non-trivial.
    buy_accounting = compute_buy_accounting(raw_fill_price=buy_price, slot_budget=budget, commission=commission, slippage_rate=slippage_rate)
    quantity = quantity_units_to_float(buy_accounting.quantity_units)
    position = VirtualPosition(
        position_id=VirtualPosition.make_id(symbol, entry_date), symbol=symbol, candidate_id=candidate_id,
        order_id=order.order_id, signal_date=entry_date, entry_date=entry_date, entry_price=buy_price, quantity=quantity,
        initial_rank=1, initial_model_score=0.5, signal_close=buy_price, max_entry_price=buy_price * 1.01,
        initial_adv_quintile="adv_q1", initial_market_regime="Bull_Normal", target_price=999999.0,
        planned_time_exit_date=date(2026, 12, 31), status=OPEN, current_close=current_close,
    )
    sandbox_repo.create_position(position)
    portfolio_repo.append_execution(
        Execution(
            execution_id=f"{position.position_id}:BUY", replay_id=REPLAY_ID, variant_id="B", control_seed=None,
            order_id=order.order_id, candidate_id=candidate_id, position_id=position.position_id, symbol=symbol,
            side=BUY, decision_date=entry_date, execution_date=entry_date,
            raw_market_fill_price_units=to_price_units(buy_price), effective_fill_price_units=buy_accounting.effective_fill_price_units,
            quantity_units=buy_accounting.quantity_units, gross_notional_units=buy_accounting.gross_notional_units,
            commission_units=to_money_units(commission), slippage_rate_units=to_rate_units(slippage_rate),
            slippage_cost_units=buy_accounting.slippage_cost_units,
            net_cash_flow_units=buy_accounting.net_cash_flow_units, fill_reason="FILLED_AT_OPEN",
            market_data_snapshot_id="snap-1", created_at=NOW,
        )
    )
    # Mirrors compute_financial_performance's OWN computation path exactly
    # (round the mark-to-market value to whole money units first, THEN
    # subtract the exact-integer cost basis, THEN convert once to float) so
    # this hand-check isn't thrown off by a different float-rounding path than
    # production uses. Cost basis is the EXACT total cash outflow (gross
    # notional + commission + slippage) -- Stage 11-15 second closure,
    # finding 3 -- never gross_notional alone, which silently omits slippage.
    market_value_units = to_money_units(quantity * current_close)
    cost_basis_units = -buy_accounting.net_cash_flow_units
    unrealized_gain = money_units_to_float(market_value_units - cost_basis_units)
    return position, unrealized_gain


def test_compute_financial_performance_hand_computed_trades_and_open_winner():
    sandbox_repo, portfolio_repo = _repos()

    # Trade A: win of $200 (buy 10 @ $100 = -$1000, sell 10 @ $120 = +$1200).
    _, a_pnl_units = _insert_closed_trade(sandbox_repo, portfolio_repo, "AAA", date(2026, 1, 5), date(2026, 1, 8), sell_price=120.0)
    # Trade B: loss of $100 (sell 10 @ $90 = +$900).
    _, b_pnl_units = _insert_closed_trade(sandbox_repo, portfolio_repo, "BBB", date(2026, 1, 5), date(2026, 1, 9), sell_price=90.0)
    # Trade C: dominant win of $4000 (sell 10 @ $500 = +$5000).
    _, c_pnl_units = _insert_closed_trade(sandbox_repo, portfolio_repo, "CCC", date(2026, 1, 5), date(2026, 1, 10), sell_price=500.0)
    # Open position D: cost basis $1000 (quantity 10 @ entry $100), marked at
    # current_close=$700 -> market_value=$7000, unrealized_gain=$6000 -- the
    # dominant unresolved winner.
    _, d_unrealized_gain = _insert_open_position(sandbox_repo, portfolio_repo, "DDD", date(2026, 1, 5), current_close=700.0)

    assert a_pnl_units == pytest.approx(20_000)  # $200 in cents
    assert b_pnl_units == pytest.approx(-10_000)  # -$100
    assert c_pnl_units == pytest.approx(400_000)  # $4000
    assert d_unrealized_gain == pytest.approx(6000.0)

    realized_units = a_pnl_units + b_pnl_units + c_pnl_units
    unrealized_units = round(d_unrealized_gain * 100)
    starting_units = 1_000_000
    ending_units = starting_units + realized_units + unrealized_units

    portfolio_repo.append_equity_snapshot(
        PortfolioEquitySnapshot(
            snapshot_id=f"{REPLAY_ID}:2026-01-05", replay_id=REPLAY_ID, as_of_date=date(2026, 1, 5),
            cash_units=starting_units, reserved_capital_units=0, open_position_market_value_units=0,
            total_equity_units=starting_units, open_position_count=0, reserved_order_count=0,
            cumulative_commissions_units=0, cumulative_slippage_cost_units=0, created_at=NOW,
        )
    )
    portfolio_repo.append_equity_snapshot(
        PortfolioEquitySnapshot(
            snapshot_id=f"{REPLAY_ID}:2026-01-10", replay_id=REPLAY_ID, as_of_date=date(2026, 1, 10),
            cash_units=ending_units - 700_000, reserved_capital_units=0, open_position_market_value_units=700_000,
            total_equity_units=ending_units, open_position_count=1, reserved_order_count=0,
            cumulative_commissions_units=0, cumulative_slippage_cost_units=0, created_at=NOW,
        )
    )

    context = DiagnosticsContext(
        manifest=_FakeManifest(), replay_id=REPLAY_ID, prices_df=None,
        portfolio_repo=portfolio_repo, sandbox_repo=sandbox_repo,
    )

    report = compute_financial_performance(context, REPLAY_ID, "B", None)

    expected_net_pnl = money_units_to_float(realized_units + unrealized_units)
    assert report.starting_equity == pytest.approx(10_000.0)
    assert report.ending_equity == pytest.approx(10_000.0 + expected_net_pnl)
    assert report.net_pnl == pytest.approx(expected_net_pnl)
    assert report.net_return_pct == pytest.approx(expected_net_pnl / 10_000.0)

    assert report.closed_trade_count == 3
    assert report.win_count == 2
    assert report.loss_count == 1
    # gross_wins = 200 + 4000 = 4200; gross_losses = 100 -> profit_factor = 42.0
    assert report.profit_factor == pytest.approx(42.0)

    assert report.largest_closed_winning_trade is not None
    assert report.largest_closed_winning_trade.symbol == "CCC"
    assert report.largest_closed_winning_trade.net_pnl == pytest.approx(4000.0)
    assert report.largest_closed_winning_trade_pct_of_net_pnl == pytest.approx(4000.0 / expected_net_pnl)
    assert report.net_pnl_minus_largest_winning_trade == pytest.approx(expected_net_pnl - 4000.0)
    assert report.remains_positive_after_removing_largest_winner is True

    assert report.largest_open_position is not None
    assert report.largest_open_position.symbol == "DDD"
    assert report.largest_open_position.unrealized_gain == pytest.approx(6000.0)
    assert report.largest_open_position_pct_of_net_pnl == pytest.approx(6000.0 / expected_net_pnl)
    assert report.open_position_market_value_pct_of_ending_equity == pytest.approx(700_000 / ending_units)


def test_positive_net_pnl_flips_negative_after_removing_dominant_winner():
    sandbox_repo, portfolio_repo = _repos()

    # One dominant win of $500 (sell 10 @ $150), one loss of $400 (sell 10 @
    # $60) -> net_pnl=$100 (positive), but net_pnl - largest_winner =
    # 100 - 500 = -400 (negative).
    _, win_units = _insert_closed_trade(sandbox_repo, portfolio_repo, "AAA", date(2026, 1, 5), date(2026, 1, 6), sell_price=150.0)
    _, loss_units = _insert_closed_trade(sandbox_repo, portfolio_repo, "BBB", date(2026, 1, 5), date(2026, 1, 6), sell_price=60.0)
    assert win_units == pytest.approx(50_000)  # $500
    assert loss_units == pytest.approx(-40_000)  # -$400

    starting_units = 1_000_000
    ending_units = starting_units + win_units + loss_units
    portfolio_repo.append_equity_snapshot(
        PortfolioEquitySnapshot(
            snapshot_id=f"{REPLAY_ID}:2026-01-05", replay_id=REPLAY_ID, as_of_date=date(2026, 1, 5),
            cash_units=starting_units, reserved_capital_units=0, open_position_market_value_units=0,
            total_equity_units=starting_units, open_position_count=0, reserved_order_count=0,
            cumulative_commissions_units=0, cumulative_slippage_cost_units=0, created_at=NOW,
        )
    )
    portfolio_repo.append_equity_snapshot(
        PortfolioEquitySnapshot(
            snapshot_id=f"{REPLAY_ID}:2026-01-06", replay_id=REPLAY_ID, as_of_date=date(2026, 1, 6),
            cash_units=ending_units, reserved_capital_units=0, open_position_market_value_units=0,
            total_equity_units=ending_units, open_position_count=0, reserved_order_count=0,
            cumulative_commissions_units=0, cumulative_slippage_cost_units=0, created_at=NOW,
        )
    )

    context = DiagnosticsContext(
        manifest=_FakeManifest(), replay_id=REPLAY_ID, prices_df=None,
        portfolio_repo=portfolio_repo, sandbox_repo=sandbox_repo,
    )

    report = compute_financial_performance(context, REPLAY_ID, "B", None)

    assert report.net_pnl == pytest.approx(100.0)
    assert report.largest_closed_winning_trade.net_pnl == pytest.approx(500.0)
    assert report.net_pnl_minus_largest_winning_trade == pytest.approx(-400.0)
    assert report.remains_positive_after_removing_largest_winner is False


def test_open_position_cost_basis_includes_slippage_not_just_gross_notional_and_commission():
    """Stage 11-15 second closure, finding 3: the previous cost basis
    (gross_notional + commission) silently omitted slippage. With a non-zero
    commission AND slippage rate, the correct cost basis is the exact BUY cash
    outflow (-net_cash_flow_units), which is strictly larger than
    gross_notional + commission alone -- so unrealized_gain must be smaller
    than the (wrong) old formula would report."""

    sandbox_repo, portfolio_repo = _repos()
    position, expected_unrealized_gain = _insert_open_position(
        sandbox_repo, portfolio_repo, "AAA", date(2026, 1, 5), current_close=120.0,
        buy_price=100.0, budget=1000.0, commission=2.0, slippage_rate=0.01,
    )
    portfolio_repo.append_equity_snapshot(
        PortfolioEquitySnapshot(
            snapshot_id=f"{REPLAY_ID}:2026-01-05", replay_id=REPLAY_ID, as_of_date=date(2026, 1, 5),
            cash_units=1_000_000, reserved_capital_units=0, open_position_market_value_units=0,
            total_equity_units=1_000_000, open_position_count=1, reserved_order_count=0,
            cumulative_commissions_units=200, cumulative_slippage_cost_units=1000, created_at=NOW,
        )
    )

    context = DiagnosticsContext(
        manifest=_FakeManifest(), replay_id=REPLAY_ID, prices_df=None,
        portfolio_repo=portfolio_repo, sandbox_repo=sandbox_repo,
    )
    report = compute_financial_performance(context, REPLAY_ID, "B", None)

    assert report.largest_open_position is not None
    assert report.largest_open_position.unrealized_gain == pytest.approx(expected_unrealized_gain)
    # The old (buggy) gross_notional+commission-only cost basis would have
    # reported a strictly LARGER (wrong) unrealized gain, since it omits the
    # slippage cost that genuinely reduced the cash the position cost to buy.
    reference_accounting = compute_buy_accounting(100.0, 1000.0, 2.0, 0.01)
    quantity = quantity_units_to_float(reference_accounting.quantity_units)
    old_wrong_cost_basis_units = reference_accounting.gross_notional_units + to_money_units(2.0)
    old_wrong_unrealized_gain = quantity * 120.0 - money_units_to_float(old_wrong_cost_basis_units)
    assert report.largest_open_position.unrealized_gain < old_wrong_unrealized_gain


def test_all_open_positions_underwater_gives_no_largest_open_winner():
    """Stage 11-15 second closure, finding 3: if every open position currently
    shows a loss, there is no unresolved WINNER to report -- the field must be
    None (undetermined), never "the least-bad loss.\""""

    sandbox_repo, portfolio_repo = _repos()
    _insert_open_position(sandbox_repo, portfolio_repo, "AAA", date(2026, 1, 5), current_close=80.0)  # underwater
    _insert_open_position(sandbox_repo, portfolio_repo, "BBB", date(2026, 1, 5), current_close=90.0)  # also underwater
    portfolio_repo.append_equity_snapshot(
        PortfolioEquitySnapshot(
            snapshot_id=f"{REPLAY_ID}:2026-01-05", replay_id=REPLAY_ID, as_of_date=date(2026, 1, 5),
            cash_units=1_000_000, reserved_capital_units=0, open_position_market_value_units=0,
            total_equity_units=1_000_000, open_position_count=2, reserved_order_count=0,
            cumulative_commissions_units=0, cumulative_slippage_cost_units=0, created_at=NOW,
        )
    )

    context = DiagnosticsContext(
        manifest=_FakeManifest(), replay_id=REPLAY_ID, prices_df=None,
        portfolio_repo=portfolio_repo, sandbox_repo=sandbox_repo,
    )
    report = compute_financial_performance(context, REPLAY_ID, "B", None)

    assert report.largest_open_position is None
    assert report.largest_open_position_pct_of_net_pnl is None


def test_profit_factor_integer_first_summation_avoids_float_rounding():
    """The classic float pitfall: 0.1 + 0.2 != 0.3 in binary floating point
    ((0.1 + 0.2) / 0.3 == 1.0000000000000002, not exactly 1.0). Two winning
    trades of $0.10 and $0.20 plus one loss of exactly $0.30 must give a
    profit factor of EXACTLY 1.0 -- summing in integer cents first (30 + 30)
    rather than summing pre-converted floats (Stage 11-15 second closure,
    finding 4)."""

    sandbox_repo, portfolio_repo = _repos()
    _insert_closed_trade(sandbox_repo, portfolio_repo, "AAA", date(2026, 1, 5), date(2026, 1, 6), sell_price=100.01)  # +$0.10
    _insert_closed_trade(sandbox_repo, portfolio_repo, "BBB", date(2026, 1, 5), date(2026, 1, 6), sell_price=100.02)  # +$0.20
    _insert_closed_trade(sandbox_repo, portfolio_repo, "CCC", date(2026, 1, 5), date(2026, 1, 6), sell_price=99.97)   # -$0.30
    portfolio_repo.append_equity_snapshot(
        PortfolioEquitySnapshot(
            snapshot_id=f"{REPLAY_ID}:2026-01-05", replay_id=REPLAY_ID, as_of_date=date(2026, 1, 5),
            cash_units=1_000_000, reserved_capital_units=0, open_position_market_value_units=0,
            total_equity_units=1_000_000, open_position_count=0, reserved_order_count=0,
            cumulative_commissions_units=0, cumulative_slippage_cost_units=0, created_at=NOW,
        )
    )
    portfolio_repo.append_equity_snapshot(
        PortfolioEquitySnapshot(
            snapshot_id=f"{REPLAY_ID}:2026-01-06", replay_id=REPLAY_ID, as_of_date=date(2026, 1, 6),
            cash_units=1_000_000, reserved_capital_units=0, open_position_market_value_units=0,
            total_equity_units=1_000_000, open_position_count=0, reserved_order_count=0,
            cumulative_commissions_units=0, cumulative_slippage_cost_units=0, created_at=NOW,
        )
    )

    context = DiagnosticsContext(
        manifest=_FakeManifest(), replay_id=REPLAY_ID, prices_df=None,
        portfolio_repo=portfolio_repo, sandbox_repo=sandbox_repo,
    )
    report = compute_financial_performance(context, REPLAY_ID, "B", None)

    assert report.profit_factor == 1.0  # exact equality, not pytest.approx -- this is the whole point


# ---------------------------------------------------------------- feasibility verdict


def _report(net_pnl: float, net_return_pct: float, max_drawdown_pct: float = 0.05, profit_factor: float | None = 2.0,
            largest_win_pct: float | None = 0.1, replay_id: str = "r",
            variant_id: str = VARIANT_B, control_seed: int | None = None) -> FinancialPerformanceReport:
    return FinancialPerformanceReport(
        replay_id=replay_id, variant_id=variant_id, control_seed=control_seed,
        starting_equity=10_000.0, ending_equity=10_000.0 + net_pnl, net_pnl=net_pnl, net_return_pct=net_return_pct,
        drawdown=DrawdownResult(max_drawdown_pct=max_drawdown_pct, peak_date=None, peak_equity=None, trough_date=None, trough_equity=None),
        quarterly_returns=(), closed_trade_count=1, win_count=1, loss_count=0, profit_factor=profit_factor,
        closed_trades=(), largest_closed_winning_trade=None, largest_closed_winning_trade_pct_of_net_pnl=largest_win_pct,
        net_pnl_minus_largest_winning_trade=None, remains_positive_after_removing_largest_winner=None,
        largest_open_position=None, largest_open_position_pct_of_net_pnl=None,
        open_position_market_value_pct_of_ending_equity=None,
    )


FEASIBILITY_CRITERIA = {
    "max_drawdown_threshold": "0.20",
    "largest_win_pct_of_net_profit_threshold": "0.50",
    "control_percentile_threshold": "80.0",
    "min_profit_factor": "1.0",
}


def _full_control_group(returns_by_rank=None) -> list[FinancialPerformanceReport]:
    """Exactly the frozen 50-seed control group -- one valid Variant D report
    per seed in DEFAULT_CONTROL_SEEDS. `returns_by_rank`, if given, maps a
    1-based rank (in DEFAULT_CONTROL_SEEDS' own order) to a net_return_pct;
    seeds not present default to 0.01."""

    returns_by_rank = returns_by_rank or {}
    reports = []
    for rank, seed in enumerate(DEFAULT_CONTROL_SEEDS, start=1):
        reports.append(
            _report(
                net_pnl=100.0, net_return_pct=returns_by_rank.get(rank, 0.01), replay_id=f"d{seed}",
                variant_id=VARIANT_D, control_seed=seed,
            )
        )
    return reports


def test_feasibility_verdict_variant_b_above_control_percentile_passes():
    # 50 D returns spread 0.01, 0.02, ..., 0.50 -- B at 0.99 is above all of them.
    variant_d = _full_control_group({rank: 0.01 * rank for rank in range(1, 51)})
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.99)

    verdict = compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)

    percentile_criterion = next(c for c in verdict.criteria if c.name == CRITERION_BEATS_CONTROL_PERCENTILE)
    assert percentile_criterion.value == pytest.approx(100.0)
    assert percentile_criterion.passed is True
    assert verdict.verdict is True


def test_feasibility_verdict_variant_b_below_control_percentile_fails():
    variant_d = _full_control_group({rank: 0.01 * rank for rank in range(1, 51)})
    variant_b = _report(net_pnl=10.0, net_return_pct=0.001)  # below every D value (min is 0.01)

    verdict = compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)

    percentile_criterion = next(c for c in verdict.criteria if c.name == CRITERION_BEATS_CONTROL_PERCENTILE)
    assert percentile_criterion.value == pytest.approx(0.0)
    assert percentile_criterion.passed is False
    assert verdict.verdict is False


def test_feasibility_verdict_variant_b_exactly_at_control_percentile_passes():
    # 50 D returns 0.01..0.50; exactly 40 of 50 (80%) are <= 0.40.
    variant_d = _full_control_group({rank: 0.01 * rank for rank in range(1, 51)})
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.40)

    verdict = compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)

    percentile_criterion = next(c for c in verdict.criteria if c.name == CRITERION_BEATS_CONTROL_PERCENTILE)
    assert percentile_criterion.value == pytest.approx(80.0)
    assert percentile_criterion.passed is True


def test_feasibility_verdict_all_criteria_pass():
    variant_d = _full_control_group()
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20, max_drawdown_pct=0.10, profit_factor=2.0, largest_win_pct=0.30)

    verdict = compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)

    assert {c.name: c.passed for c in verdict.criteria} == {
        CRITERION_POSITIVE_NET_PNL: True,
        CRITERION_BEATS_CONTROL_PERCENTILE: True,
        CRITERION_MAX_DRAWDOWN_WITHIN_THRESHOLD: True,
        CRITERION_PROFIT_FACTOR_WITHIN_THRESHOLD: True,
        CRITERION_LARGEST_WINNER_CONCENTRATION_WITHIN_THRESHOLD: True,
    }
    assert verdict.verdict is True


def test_feasibility_verdict_drawdown_and_profit_factor_failures():
    variant_d = _full_control_group()
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20, max_drawdown_pct=0.35, profit_factor=0.5, largest_win_pct=0.80)

    verdict = compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)

    by_name = {c.name: c.passed for c in verdict.criteria}
    assert by_name[CRITERION_MAX_DRAWDOWN_WITHIN_THRESHOLD] is False
    assert by_name[CRITERION_PROFIT_FACTOR_WITHIN_THRESHOLD] is False
    assert by_name[CRITERION_LARGEST_WINNER_CONCENTRATION_WITHIN_THRESHOLD] is False
    assert verdict.verdict is False


def test_feasibility_verdict_undetermined_criterion_never_silently_passes():
    """No closed winning trade at all -- largest_win_pct is None (undeterminable,
    not zero). The overall verdict must be None (undetermined), never a silent
    True just because the other criteria happen to pass."""

    variant_d = _full_control_group()
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20, largest_win_pct=None)

    verdict = compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)

    by_name = {c.name: c.passed for c in verdict.criteria}
    assert by_name[CRITERION_LARGEST_WINNER_CONCENTRATION_WITHIN_THRESHOLD] is None
    assert verdict.verdict is None


def test_feasibility_verdict_infinite_profit_factor_passes():
    variant_d = _full_control_group()
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20, profit_factor=float("inf"))

    verdict = compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)

    by_name = {c.name: c.passed for c in verdict.criteria}
    assert by_name[CRITERION_PROFIT_FACTOR_WITHIN_THRESHOLD] is True


def test_feasibility_verdict_empty_variant_d_gives_undetermined_percentile():
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20)

    verdict = compute_feasibility_verdict(variant_b, [], FEASIBILITY_CRITERIA)

    by_name = {c.name: c.passed for c in verdict.criteria}
    assert by_name[CRITERION_BEATS_CONTROL_PERCENTILE] is None
    assert verdict.verdict is None


# ------------------------------------------- control-group completeness (finding 1)


@pytest.mark.parametrize("seed_count", [1, 5, 49])
def test_incomplete_control_group_gives_undetermined_percentile_not_an_error(seed_count):
    """Fewer than 50 seeds is the ORDINARY state while an experiment's Variant D
    sweep is still running -- never an error, but never enough to produce a
    percentile result either."""

    variant_d = _full_control_group()[:seed_count]
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20)

    verdict = compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)

    by_name = {c.name: c.passed for c in verdict.criteria}
    assert by_name[CRITERION_BEATS_CONTROL_PERCENTILE] is None


def test_exactly_50_valid_seeds_gives_a_determined_percentile():
    variant_d = _full_control_group()
    assert len(variant_d) == 50
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.99)

    verdict = compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)

    by_name = {c.name: c.passed for c in verdict.criteria}
    assert by_name[CRITERION_BEATS_CONTROL_PERCENTILE] is not None


def test_51_control_reports_is_structurally_invalid():
    """Only 50 valid seeds exist in DEFAULT_CONTROL_SEEDS -- 51 reports drawn
    from that universe must contain a duplicate, by construction (pigeonhole)."""

    variant_d = _full_control_group() + [_full_control_group()[0]]
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20)

    with pytest.raises(ControlGroupValidationError):
        compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)


def test_duplicated_seed_is_rejected():
    variant_d = _full_control_group()[:49] + [_full_control_group()[0]]  # seed 0 appears twice
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20)

    with pytest.raises(ControlGroupValidationError, match="duplicate control_seed"):
        compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)


def test_unknown_seed_is_rejected():
    variant_d = _full_control_group()[:49] + [
        _report(net_pnl=100.0, net_return_pct=0.01, replay_id="d-unknown", variant_id=VARIANT_D, control_seed=99_999)
    ]
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20)

    with pytest.raises(ControlGroupValidationError, match="not in the frozen"):
        compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)


def test_control_report_missing_variant_id_d_is_rejected():
    variant_d = [_report(net_pnl=100.0, net_return_pct=0.01, replay_id="bad", variant_id=VARIANT_B, control_seed=1)]
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20)

    with pytest.raises(ControlGroupValidationError):
        compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)


def test_control_report_missing_control_seed_is_rejected():
    variant_d = [_report(net_pnl=100.0, net_return_pct=0.01, replay_id="bad", variant_id=VARIANT_D, control_seed=None)]
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20)

    with pytest.raises(ControlGroupValidationError):
        compute_feasibility_verdict(variant_b, variant_d, FEASIBILITY_CRITERIA)


def test_variant_b_report_with_a_control_seed_is_rejected():
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20, variant_id=VARIANT_B, control_seed=1)

    with pytest.raises(ControlGroupValidationError):
        compute_feasibility_verdict(variant_b, [], FEASIBILITY_CRITERIA)


def test_variant_b_report_with_wrong_variant_id_is_rejected():
    variant_b = _report(net_pnl=1000.0, net_return_pct=0.20, variant_id=VARIANT_D, control_seed=None)

    with pytest.raises(ControlGroupValidationError):
        compute_feasibility_verdict(variant_b, [], FEASIBILITY_CRITERIA)


# --------------------------------------------------- three-tier verdict logic (finding 2)


def test_confirmed_failure_wins_over_unrelated_undetermined_criterion():
    """Net P&L is negative (a CONFIRMED failure) while winner-concentration is
    undeterminable (no closed winning trade). The overall verdict must be
    False, not None -- a known failure is never masked by an unrelated
    unknown."""

    variant_b = _report(net_pnl=-100.0, net_return_pct=-0.01, largest_win_pct=None)

    verdict = compute_feasibility_verdict(variant_b, [], FEASIBILITY_CRITERIA)

    by_name = {c.name: c.passed for c in verdict.criteria}
    assert by_name[CRITERION_POSITIVE_NET_PNL] is False
    assert by_name[CRITERION_LARGEST_WINNER_CONCENTRATION_WITHIN_THRESHOLD] is None
    assert by_name[CRITERION_BEATS_CONTROL_PERCENTILE] is None
    assert verdict.verdict is False
