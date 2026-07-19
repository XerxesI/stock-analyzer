"""MFE/MAE diagnostics -- Revision 5, Section 20, Stage 12 (corrected in the
Stage 11-15 closure cycle, finding 4).

For every position (open or closed), computed post-hoc from the frozen OHLCV over
its holding window:

    MFE = (max KNOWN price reached during the holding window - effective_entry_price)
          / effective_entry_price
    MAE = (min KNOWN price reached during the holding window - effective_entry_price)
          / effective_entry_price

reported together with the MFE/MAE price and date, sessions from entry to each,
the realized-or-mark-to-market return, peak-to-exit giveback (MFE% -
realized_return%), and exit efficiency (realized_return / MFE).

**Holding-window boundary rule (Section 20), frozen and applied identically to
every position regardless of variant/seed:** daily OHLC cannot establish whether
a session's own High/Low happened before or after an intraday-threshold
fill/exit within that SAME session, so a session's High/Low are included only
when the executable moment within it is unambiguous:

- Entry session: `FILLED_AT_OPEN` (executed at the session's open, the earliest
  possible point) -> that session's High/Low are included. `FILLED_AT_CEILING`
  (an intraday touch reached the ceiling, order within the session unknown) ->
  excluded; the window starts the NEXT session, using the known fill price as
  the reference.
- Exit session (closed positions only): `SELL_TIME` (the session's close,
  unambiguous) -> included. `SELL_TARGET` via the session's own open reaching
  the target (unambiguous, reconstructed here from the frozen OHLCV itself, the
  same open-first branch `MonitoringService._check_target` uses) -> included.
  `SELL_TARGET` via only an intraday high touch (order within the session
  unknown) -> excluded; the window ends the PREVIOUS session.
- Open (unresolved) positions: there is no exit session to exclude -- the window
  runs from the (already resolved) entry boundary through the frozen OHLCV's own
  `outcome_data_end_date`, since the position was never observed to exit within
  the frozen period.

**Known-boundary-point correction (Stage 11-15 closure, finding 4):** excluding
an exit session's own (unknown-ordering) High/Low must never make MFE/MAE
UNDERSTATE what the position is actually known to have reached. The realized
effective exit price is itself a certain, known point on the path -- it is
always included as a candidate for both MFE and MAE, alongside whatever the
(possibly-excluded) window's own High/Low contribute. For an ordinary profitable
`SELL_TARGET` exit this guarantees `mfe_pct >= realized_return_pct` (the position
cannot be reported as never having reached the very price it demonstrably sold
at), which in turn guarantees peak-to-exit giveback is never negative and exit
efficiency never exceeds 1 purely as an artifact of the exclusion rule above.
This is a general correctness property, not special-cased to the ambiguous exit
branch: for `SELL_TIME`/unambiguous `SELL_TARGET` exits the exit session's own
High/Low are already in the window and already dominate, so including the exit
price as an extra candidate there is a harmless no-op.

**Degenerate empty-window fallback (Stage 11-15 closure, finding 4):** if the
ambiguity exclusions above leave literally zero observed sessions in the window
(e.g. a `FILLED_AT_CEILING` entry immediately followed by an ambiguous
`SELL_TARGET` exit the very next session), the trade is NOT discarded as
undefined. Instead MFE/MAE fall back to the two (or, for an open position with
no data at all after entry, the one) points that ARE certainly known: the
effective entry price (a zero-excursion baseline -- no intermediate excursion
can be claimed or denied) and, for a closed position, the effective exit price.

Both `effective_entry_price` and `effective_exit_price` come from EXP-005's own
`executions` ledger (Stage 5/6) -- never core `virtual_positions.entry_price`
(the raw, uncosted reference core's own decisions use; see the Stage 6 dual-
accounting errata).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from stock_analyzer.sandbox.domain.entry_order import FILLED_AT_CEILING, FILLED_AT_OPEN
from stock_analyzer.sandbox.domain.position import CLOSED, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import SELL_TARGET, SELL_TIME
from stock_analyzer.sandbox.exp005.diagnostics._shared import next_session as _next_session
from stock_analyzer.sandbox.exp005.diagnostics._shared import previous_session as _previous_session
from stock_analyzer.sandbox.exp005.diagnostics._shared import symbol_sessions as _symbol_sessions
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import DiagnosticsContext
from stock_analyzer.sandbox.exp005.domain.execution import BUY
from stock_analyzer.sandbox.exp005.domain.units import price_units_to_float


class MfeMaeComputationError(RuntimeError):
    """Raised when a position's data is inconsistent with this computation's
    preconditions: missing BUY/SELL execution, or an unrecognized fill/exit
    reason. No longer raised for an empty post-ambiguity-exclusion window -- see
    the degenerate-fallback branch below."""


@dataclass(frozen=True)
class MfeMaeResult:
    position_id: str
    candidate_id: str
    symbol: str
    is_closed: bool
    effective_entry_price: float
    window_start_date: date
    window_end_date: date
    mfe_pct: float
    mfe_price: float
    mfe_date: date
    sessions_to_mfe: int
    mae_pct: float
    mae_price: float
    mae_date: date
    sessions_to_mae: int
    realized_or_mtm_return_pct: float
    peak_to_exit_giveback_pct: float
    exit_efficiency: float | None  # None when mfe_pct == 0 (undefined, not infinite)


def _is_target_exit_unambiguous(sessions: pd.DataFrame, exit_date: date, target_price: float) -> bool:
    """SELL_TARGET via the session's own open >= target is unambiguous (the same
    open-first branch MonitoringService._check_target uses); via only an
    intraday high touch, it is not."""

    if exit_date not in sessions.index:
        return False
    return bool(sessions.loc[exit_date, "Open"] >= target_price)


def compute_mfe_mae(context: DiagnosticsContext, position: VirtualPosition) -> MfeMaeResult:
    executions = context.portfolio_repo.list_executions_for_position(position.position_id)
    buy_execution = next((e for e in executions if e.side == BUY), None)
    if buy_execution is None:
        raise MfeMaeComputationError(f"position {position.position_id} has no BUY execution.")

    effective_entry_price = price_units_to_float(buy_execution.effective_fill_price_units)
    sessions = _symbol_sessions(context.prices_df, position.symbol)

    is_closed = position.status == CLOSED
    effective_exit_price: float | None = None
    if is_closed:
        sell_execution = next((e for e in executions if e.side != BUY), None)
        if sell_execution is None:
            raise MfeMaeComputationError(f"closed position {position.position_id} has no SELL execution.")
        effective_exit_price = price_units_to_float(sell_execution.effective_fill_price_units)

    fill_reason = buy_execution.fill_reason
    if fill_reason == FILLED_AT_OPEN:
        window_start = position.entry_date
    elif fill_reason == FILLED_AT_CEILING:
        window_start = _next_session(sessions, position.entry_date)
    else:
        raise MfeMaeComputationError(
            f"unrecognized BUY fill_reason {fill_reason!r} for position {position.position_id}."
        )

    if is_closed:
        if position.exit_reason == SELL_TIME:
            window_end = position.exit_date
        elif position.exit_reason == SELL_TARGET:
            if _is_target_exit_unambiguous(sessions, position.exit_date, position.target_price):
                window_end = position.exit_date
            else:
                window_end = _previous_session(sessions, position.exit_date)
        else:
            raise MfeMaeComputationError(
                f"unrecognized exit_reason {position.exit_reason!r} for position {position.position_id}."
            )
    else:
        window_end = context.manifest.outcome_data_end_date

    if window_start is None or window_end is None or window_start > window_end:
        # Degenerate: the ambiguity exclusions above leave no observed session at
        # all. Fall back to the certainly-known boundary price(s) rather than
        # discarding an otherwise valid trade -- see the module docstring.
        known_points: list[tuple[date, float]] = [(position.entry_date, effective_entry_price)]
        if is_closed:
            known_points.append((position.exit_date, effective_exit_price))
        mfe_date, mfe_price = max(known_points, key=lambda p: p[1])
        mae_date, mae_price = min(known_points, key=lambda p: p[1])
        sessions_to_mfe = known_points.index((mfe_date, mfe_price)) + 1
        sessions_to_mae = known_points.index((mae_date, mae_price)) + 1
        reported_window_start = window_start if window_start is not None else position.entry_date
        reported_window_end = window_end if window_end is not None else reported_window_start
    else:
        window = sessions.loc[(sessions.index >= window_start) & (sessions.index <= window_end)]
        if window.empty:
            raise MfeMaeComputationError(
                f"position {position.position_id} has no observed sessions in its holding window "
                f"[{window_start}, {window_end}] despite a non-empty nominal range -- a genuine gap "
                "in the frozen source, not the ambiguity-exclusion case."
            )

        mfe_date = window["High"].idxmax()
        mfe_price = float(window.loc[mfe_date, "High"])
        mae_date = window["Low"].idxmin()
        mae_price = float(window.loc[mae_date, "Low"])

        ordered_dates = list(window.index)
        if is_closed and position.exit_date not in ordered_dates:
            # The exit session's own High/Low were excluded (ambiguous touch),
            # but the exit price ITSELF is a known point on the path -- always a
            # candidate for both MFE and MAE, never silently understated.
            ordered_dates = ordered_dates + [position.exit_date]
            if effective_exit_price > mfe_price:
                mfe_date, mfe_price = position.exit_date, effective_exit_price
            if effective_exit_price < mae_price:
                mae_date, mae_price = position.exit_date, effective_exit_price

        sessions_to_mfe = ordered_dates.index(mfe_date) + 1
        sessions_to_mae = ordered_dates.index(mae_date) + 1
        reported_window_start, reported_window_end = window_start, window_end

    mfe_pct = (mfe_price - effective_entry_price) / effective_entry_price
    mae_pct = (mae_price - effective_entry_price) / effective_entry_price

    if is_closed:
        realized_or_mtm_return_pct = (effective_exit_price - effective_entry_price) / effective_entry_price
    else:
        mark_price = position.current_close if position.current_close is not None else effective_entry_price
        realized_or_mtm_return_pct = (mark_price - effective_entry_price) / effective_entry_price

    peak_to_exit_giveback_pct = mfe_pct - realized_or_mtm_return_pct
    exit_efficiency = (realized_or_mtm_return_pct / mfe_pct) if mfe_pct != 0 else None

    return MfeMaeResult(
        position_id=position.position_id,
        candidate_id=position.candidate_id,
        symbol=position.symbol,
        is_closed=is_closed,
        effective_entry_price=effective_entry_price,
        window_start_date=reported_window_start,
        window_end_date=reported_window_end,
        mfe_pct=mfe_pct,
        mfe_price=mfe_price,
        mfe_date=mfe_date,
        sessions_to_mfe=sessions_to_mfe,
        mae_pct=mae_pct,
        mae_price=mae_price,
        mae_date=mae_date,
        sessions_to_mae=sessions_to_mae,
        realized_or_mtm_return_pct=realized_or_mtm_return_pct,
        peak_to_exit_giveback_pct=peak_to_exit_giveback_pct,
        exit_efficiency=exit_efficiency,
    )
