"""HOLD-decision diagnostics -- Revision 5, Section 22, Stage 13.

For every daily HOLD snapshot (a `position_snapshots` row with
`recommendation='HOLD'`), at forward horizons of 1, 5, and 10 trading sessions,
computed from the frozen OHLCV starting the session after the snapshot's own
`as_of_date`: forward close return (from that snapshot's own `close_price`);
maximum future High / minimum future Low; whether the position's `target_price` was
subsequently reached within that specific horizon (and sessions-until-target if so);
`is_censored` (Section 27).

Additionally, per HOLD snapshot (not horizon-specific): whether the position, as
actually replayed, EVENTUALLY exited profitably -- `PROFITABLE`/`ADVERSE` when the
position later closed (using EXP-005's own effective entry/exit prices from
`executions`, never core's raw `entry_price`/`exit_price`), or `UNRESOLVED` when the
position never closed within the frozen replay (it never "eventually exited" at
all -- a distinct case from a genuinely adverse outcome, matching Section 22's
"unresolved/censored" aggregate bucket).

HOLD correctness is explicitly not treated as binary -- these are post-hoc
diagnostics reported alongside the subsequent path, never training labels, never new
policy rules, and never fed back into a replay decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stock_analyzer.sandbox.domain.position import CLOSED, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import HOLD
from stock_analyzer.sandbox.exp005.diagnostics._shared import compute_forward_horizon, symbol_sessions
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import DiagnosticsContext
from stock_analyzer.sandbox.exp005.domain.execution import BUY
from stock_analyzer.sandbox.exp005.domain.units import price_units_to_float

HORIZONS_SESSIONS = (1, 5, 10)

PROFITABLE = "PROFITABLE"
ADVERSE = "ADVERSE"
UNRESOLVED = "UNRESOLVED"


class HoldQualityComputationError(RuntimeError):
    """Raised for a snapshot whose recommendation is not HOLD, or a position with
    no BUY execution -- HOLD diagnostics are only defined relative to a HOLD
    decision on an actually-entered position."""


@dataclass(frozen=True)
class HoldQualityHorizonResult:
    horizon_sessions: int
    sessions_observed: int
    is_censored: bool
    censoring_reason: str | None
    forward_close_return_pct: float | None
    max_high_price: float | None
    max_high_pct: float | None
    min_low_price: float | None
    min_low_pct: float | None
    target_reached: bool
    sessions_to_target: int | None


@dataclass(frozen=True)
class HoldQualityResult:
    position_id: str
    candidate_id: str
    symbol: str
    as_of_date: date
    holding_day_count: int
    snapshot_close_price: float
    horizons: tuple[HoldQualityHorizonResult, ...]
    eventual_outcome: str  # PROFITABLE, ADVERSE, or UNRESOLVED


def _horizon_result(horizon: int, fh, reference_price: float, target_price: float) -> HoldQualityHorizonResult:
    if fh.sessions_observed == 0:
        return HoldQualityHorizonResult(
            horizon_sessions=horizon, sessions_observed=0, is_censored=fh.is_censored,
            censoring_reason=fh.censoring_reason, forward_close_return_pct=None,
            max_high_price=None, max_high_pct=None, min_low_price=None, min_low_pct=None,
            target_reached=False, sessions_to_target=None,
        )

    window = fh.window
    last_close = float(window.iloc[-1]["Close"])
    forward_close_return_pct = (last_close - reference_price) / reference_price

    max_high_price = float(window["High"].max())
    max_high_pct = (max_high_price - reference_price) / reference_price
    min_low_price = float(window["Low"].min())
    min_low_pct = (min_low_price - reference_price) / reference_price

    target_hits = window.index[window["High"] >= target_price]
    target_reached = len(target_hits) > 0
    sessions_to_target = (list(window.index).index(target_hits[0]) + 1) if target_reached else None

    return HoldQualityHorizonResult(
        horizon_sessions=horizon, sessions_observed=fh.sessions_observed, is_censored=fh.is_censored,
        censoring_reason=fh.censoring_reason, forward_close_return_pct=forward_close_return_pct,
        max_high_price=max_high_price, max_high_pct=max_high_pct,
        min_low_price=min_low_price, min_low_pct=min_low_pct,
        target_reached=target_reached, sessions_to_target=sessions_to_target,
    )


def _eventual_outcome(context: DiagnosticsContext, position: VirtualPosition) -> str:
    if position.status != CLOSED:
        return UNRESOLVED

    executions = context.portfolio_repo.list_executions_for_position(position.position_id)
    buy_execution = next((e for e in executions if e.side == BUY), None)
    sell_execution = next((e for e in executions if e.side != BUY), None)
    if buy_execution is None or sell_execution is None:
        raise HoldQualityComputationError(
            f"closed position {position.position_id} is missing a BUY or SELL execution."
        )

    effective_entry_price = price_units_to_float(buy_execution.effective_fill_price_units)
    effective_exit_price = price_units_to_float(sell_execution.effective_fill_price_units)
    return PROFITABLE if effective_exit_price > effective_entry_price else ADVERSE


def compute_hold_quality(
    context: DiagnosticsContext, snapshot, position: VirtualPosition, calendar: tuple[date, ...]
) -> HoldQualityResult:
    if snapshot.recommendation != HOLD:
        raise HoldQualityComputationError(
            f"snapshot {snapshot.snapshot_id} has recommendation {snapshot.recommendation!r}, not HOLD."
        )

    sessions = symbol_sessions(context.prices_df, position.symbol)
    horizons = tuple(
        _horizon_result(
            h,
            compute_forward_horizon(sessions, calendar, snapshot.as_of_date, h, context.manifest.outcome_data_end_date),
            snapshot.close_price,
            position.target_price,
        )
        for h in HORIZONS_SESSIONS
    )

    return HoldQualityResult(
        position_id=position.position_id,
        candidate_id=position.candidate_id,
        symbol=position.symbol,
        as_of_date=snapshot.as_of_date,
        holding_day_count=snapshot.holding_day_count,
        snapshot_close_price=snapshot.close_price,
        horizons=horizons,
        eventual_outcome=_eventual_outcome(context, position),
    )


def compute_hold_quality_for_position(
    context: DiagnosticsContext, position: VirtualPosition, calendar: tuple[date, ...]
) -> list[HoldQualityResult]:
    """Convenience batch entry point: every HOLD snapshot for one position, in
    as_of_date order (matching get_snapshots_for_position's own ordering)."""

    snapshots = context.sandbox_repo.get_snapshots_for_position(position.position_id)
    return [compute_hold_quality(context, s, position, calendar) for s in snapshots if s.recommendation == HOLD]
