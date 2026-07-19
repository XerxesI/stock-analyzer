"""`NO_CAPACITY` opportunity-cost evaluation -- Revision 5, Section 24, Stage 13
(corrected in the Stage 11-15 closure cycle, finding 3).

For every `portfolio_admissions` row with `decision='NO_CAPACITY'`: the candidate's
rank and score at admission; signal close and max entry price; the portfolio's
capacity state at rejection (open/reserved counts, from that day's
`portfolio_equity_snapshots` row); which specific reservations AND open positions
occupied the slots that day; subsequent 1/5/10/20-session returns and MFE/MAE from
signal close; whether the existing ADR-007 entry rule would have filled;
`is_censored` (Section 27).

**Occupancy reconstruction uses only LOGICAL replay event dates, never wall-clock
timestamps** (Stage 11-15 closure, finding 3: the previous version compared
`slot_reservations.created_at`/`resolved_at` -- real wall-clock write times -- to
the historical `admission.as_of_date`, which are simply different clocks; a 2024
historical replay date has no defined relationship to a 2026 wall-clock write
time, so that comparison never found anything). The rules, applied uniformly:

- A reservation begins occupying on its OWNING ADMISSION's `as_of_date`.
- A still-pending reservation stops occupying on its logical fill session (a
  position takes over from there) or its logical expiry session (derived from
  `entry_order_attempts`, the same way `entry_timing.py` derives it -- `EntryOrder`
  itself has no `expiry_date` field).
- A position occupies a slot for every `as_of_date` with
  `entry_date <= as_of_date < exit_date`; an unresolved (still-open) position has
  no `exit_date` and so remains occupying through any later `as_of_date`.
- Within one admission day, candidates are evaluated in rank order (Section 8.4):
  an earlier-ranked candidate ACCEPTED earlier that SAME day occupies a slot for
  a later-ranked one being evaluated that day; a later-ranked one does not (it
  has not been decided yet at the point the earlier one is evaluated). A
  position that closed EARLIER that same day frees its slot before that day's
  own admission phase runs (Section 8.5's day-loop order: entries -> monitoring
  -> candidates/admissions) -- captured for free by the strict `< exit_date`
  occupancy test above, since `exit_date == as_of_date` already excludes it.

The reconstructed end-of-day (not tie-broken by rank) occupant count is reconciled
against that day's own persisted `portfolio_equity_snapshots` row -- both are
independent views of the same underlying state (the real-time `PortfolioLedger`'s
own bookkeeping vs. this post-hoc reconstruction from logical event dates), so
disagreement is a genuine integrity problem, surfaced as
`CapacityOccupancyReconciliationError`, never silently accepted.

**This is strictly observational.** `compute_opportunity_cost` never calls any
write method on any repository -- it only ever reads `PortfolioAdmission`/
`RankedCandidate`/`PortfolioEquitySnapshot`/`SlotReservation`/`VirtualPosition`/
`EntryOrder` rows already persisted by the real replay, plus the frozen OHLCV. It
must never create a `virtual_positions` row, a `slot_reservations` row, or any
cash-ledger entry.

**The hypothetical-fill check below intentionally DUPLICATES, rather than imports,
ADR-007's three-way execution rule** (`EntryService._evaluate_execution`,
`stock_analyzer/sandbox/application/entry_service.py`). Importing it would violate
Section 26's import-isolation invariant (diagnostics must never import a
decision-time module, direct or transitive -- see
`tests/test_exp005_diagnostics_import_boundary.py`). The rule is small, frozen, and
reproduced verbatim here for exactly that reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stock_analyzer.sandbox.domain.entry_order import EXPIRED, FILLED, FILLED_AT_CEILING, FILLED_AT_OPEN, NO_FILL
from stock_analyzer.sandbox.domain.position import CLOSED
from stock_analyzer.sandbox.exp005.diagnostics._shared import compute_forward_horizon, symbol_sessions
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import DiagnosticsContext
from stock_analyzer.sandbox.exp005.domain.admission import ACCEPTED, NO_CAPACITY, PortfolioAdmission
from stock_analyzer.sandbox.exp005.domain.units import money_units_to_float

HORIZONS_SESSIONS = (1, 5, 10, 20)

# Mirrors SandboxConfig.entry_validity_sessions's frozen default (Section 24: "the
# candidate's own 2-session validity window") -- not imported, to avoid pulling in
# stock_analyzer.sandbox.config from decision-time policy for a diagnostic constant.
ENTRY_VALIDITY_SESSIONS = 2


class OpportunityCostComputationError(RuntimeError):
    """Raised for an admission whose decision is not NO_CAPACITY, or for a
    candidate_id with no ranked_candidates row."""


class CapacityOccupancyReconciliationError(RuntimeError):
    """Raised when the reconstructed end-of-day capacity occupancy (derived
    purely from logical replay event dates) disagrees with that same day's own
    persisted portfolio_equity_snapshots row -- a genuine reconstruction or
    data-integrity problem, never silently accepted."""


def _evaluate_hypothetical_fill(open_price: float, low_price: float, max_entry_price: float) -> tuple[str, float | None]:
    """Verbatim mirror of EntryService._evaluate_execution's ADR-007 rule -- see
    the module docstring on why this is duplicated, not imported."""

    if open_price <= max_entry_price:
        return FILLED_AT_OPEN, open_price
    if low_price <= max_entry_price < open_price:
        return FILLED_AT_CEILING, max_entry_price
    return NO_FILL, None


@dataclass(frozen=True)
class OpportunityCostHorizonResult:
    horizon_sessions: int
    sessions_observed: int
    is_censored: bool
    censoring_reason: str | None
    forward_return_pct: float | None
    mfe_pct: float | None
    mfe_price: float | None
    mfe_date: date | None
    mae_pct: float | None
    mae_price: float | None
    mae_date: date | None


@dataclass(frozen=True)
class OccupyingReservation:
    reservation_id: str
    admission_id: str
    candidate_id: str
    symbol: str
    reserved_amount: float


@dataclass(frozen=True)
class OccupyingPosition:
    position_id: str
    candidate_id: str
    symbol: str
    entry_date: date


@dataclass(frozen=True)
class OpportunityCostResult:
    admission_id: str
    candidate_id: str
    symbol: str
    as_of_date: date
    rank_at_admission: int
    signal_close: float
    max_entry_price: float | None
    open_position_count: int | None
    reserved_order_count: int | None
    occupying_reservations: tuple[OccupyingReservation, ...]
    occupying_open_positions: tuple[OccupyingPosition, ...]
    hypothetical_would_have_filled: bool
    hypothetical_fill_date: date | None
    hypothetical_fill_reason: str | None
    hypothetical_raw_fill_price: float | None
    horizons: tuple[OpportunityCostHorizonResult, ...]


def _horizon_result(horizon: int, fh, reference_price: float) -> OpportunityCostHorizonResult:
    if fh.sessions_observed == 0:
        return OpportunityCostHorizonResult(
            horizon_sessions=horizon, sessions_observed=0, is_censored=fh.is_censored,
            censoring_reason=fh.censoring_reason, forward_return_pct=None,
            mfe_pct=None, mfe_price=None, mfe_date=None, mae_pct=None, mae_price=None, mae_date=None,
        )

    window = fh.window
    last_close = float(window.iloc[-1]["Close"])
    forward_return_pct = (last_close - reference_price) / reference_price

    mfe_date = window["High"].idxmax()
    mfe_price = float(window.loc[mfe_date, "High"])
    mfe_pct = (mfe_price - reference_price) / reference_price

    mae_date = window["Low"].idxmin()
    mae_price = float(window.loc[mae_date, "Low"])
    mae_pct = (mae_price - reference_price) / reference_price

    return OpportunityCostHorizonResult(
        horizon_sessions=horizon, sessions_observed=fh.sessions_observed, is_censored=fh.is_censored,
        censoring_reason=fh.censoring_reason, forward_return_pct=forward_return_pct,
        mfe_pct=mfe_pct, mfe_price=mfe_price, mfe_date=mfe_date,
        mae_pct=mae_pct, mae_price=mae_price, mae_date=mae_date,
    )


def _order_resolution_date(context: DiagnosticsContext, order) -> date | None:
    """The session an order's RESERVATION stops occupying a slot on: the fill
    date (a position takes over from there) or the logical expiry date (derived
    from entry_order_attempts -- EntryOrder itself has no expiry_date field, the
    same derivation entry_timing.py uses). None if the order is still PENDING
    (should not occur in a completed replay's own history, but then the
    reservation is treated as occupying indefinitely, never as already freed)."""

    if order.status == FILLED:
        return order.fill_date
    if order.status == EXPIRED:
        attempts = context.sandbox_repo.get_attempts_for_order(order.order_id)
        return attempts[-1].attempt_date if attempts else None
    return None


def _reconstruct_occupants(
    context: DiagnosticsContext, replay_id: str, as_of_date: date, same_day_rank_cutoff: int | None,
) -> tuple[tuple[OccupyingReservation, ...], tuple[OccupyingPosition, ...]]:
    """Reservations/positions occupying a capacity slot on as_of_date, from
    logical replay event dates only -- see the module docstring.

    `same_day_rank_cutoff`, if given, restricts SAME-DAY admissions to those
    with `rank_at_admission` STRICTLY LESS than this value (admissions are
    processed in rank order within a day) -- the view a specific candidate's own
    admission decision was actually made against. None reconstructs the FULL
    day (every admission on as_of_date, regardless of rank) -- the view the
    day's own END-OF-DAY equity snapshot reflects, used for reconciliation."""

    positions_by_candidate_id = {p.candidate_id: p for p in context.sandbox_repo.list_all_positions()}
    accepted = [a for a in context.portfolio_repo.list_admissions_for_experiment(replay_id) if a.decision == ACCEPTED]

    occupying_reservations: list[OccupyingReservation] = []
    occupying_positions: list[OccupyingPosition] = []
    for other in accepted:
        if other.as_of_date > as_of_date:
            continue
        if (
            other.as_of_date == as_of_date
            and same_day_rank_cutoff is not None
            and other.rank_at_admission >= same_day_rank_cutoff
        ):
            continue

        order = context.sandbox_repo.get_entry_order_by_candidate(other.candidate_id)
        if order is None:
            continue

        resolution_date = _order_resolution_date(context, order)
        if resolution_date is None or as_of_date < resolution_date:
            reservation = context.portfolio_repo.get_reservation_for_admission(other.admission_id)
            if reservation is not None:
                occupying_reservations.append(
                    OccupyingReservation(
                        reservation_id=reservation.reservation_id, admission_id=reservation.admission_id,
                        candidate_id=reservation.candidate_id, symbol=reservation.symbol,
                        reserved_amount=money_units_to_float(reservation.reserved_amount_units),
                    )
                )
            continue

        position = positions_by_candidate_id.get(other.candidate_id)
        if position is None:
            continue
        occupies = position.entry_date <= as_of_date and (position.status != CLOSED or as_of_date < position.exit_date)
        if occupies:
            occupying_positions.append(
                OccupyingPosition(
                    position_id=position.position_id, candidate_id=position.candidate_id,
                    symbol=position.symbol, entry_date=position.entry_date,
                )
            )

    return tuple(occupying_reservations), tuple(occupying_positions)


def _reconcile_occupancy_with_equity_snapshot(
    context: DiagnosticsContext, admission: PortfolioAdmission, equity_snapshot,
) -> None:
    if equity_snapshot is None:
        return

    full_day_reservations, full_day_positions = _reconstruct_occupants(
        context, admission.replay_id, admission.as_of_date, same_day_rank_cutoff=None,
    )
    reconstructed_reserved_count = len(full_day_reservations)
    reconstructed_open_count = len(full_day_positions)
    if (
        reconstructed_reserved_count != equity_snapshot.reserved_order_count
        or reconstructed_open_count != equity_snapshot.open_position_count
    ):
        raise CapacityOccupancyReconciliationError(
            f"reconstructed end-of-day capacity occupancy for {admission.replay_id!r} on "
            f"{admission.as_of_date} (reserved={reconstructed_reserved_count}, "
            f"open={reconstructed_open_count}) does not match that day's own persisted "
            f"portfolio_equity_snapshots row (reserved_order_count="
            f"{equity_snapshot.reserved_order_count}, open_position_count="
            f"{equity_snapshot.open_position_count})."
        )


def _hypothetical_fill(sessions, signal_date: date, max_entry_price: float | None):
    if max_entry_price is None:
        return False, None, None, None

    candidate_dates = [d for d in sessions.index if d > signal_date][:ENTRY_VALIDITY_SESSIONS]
    for d in candidate_dates:
        bar = sessions.loc[d]
        outcome, price = _evaluate_hypothetical_fill(float(bar["Open"]), float(bar["Low"]), max_entry_price)
        if outcome != NO_FILL:
            return True, d, outcome, price
    return False, None, None, None


def compute_opportunity_cost(
    context: DiagnosticsContext, admission: PortfolioAdmission, calendar: tuple[date, ...]
) -> OpportunityCostResult:
    if admission.decision != NO_CAPACITY:
        raise OpportunityCostComputationError(
            f"admission {admission.admission_id} has decision {admission.decision!r}, not NO_CAPACITY."
        )

    candidate = context.sandbox_repo.get_candidate(admission.candidate_id)
    if candidate is None:
        raise OpportunityCostComputationError(f"no ranked_candidates row for {admission.candidate_id}.")

    equity_snapshot = context.portfolio_repo.get_equity_snapshot(admission.replay_id, admission.as_of_date)
    open_position_count = equity_snapshot.open_position_count if equity_snapshot is not None else None
    reserved_order_count = equity_snapshot.reserved_order_count if equity_snapshot is not None else None
    _reconcile_occupancy_with_equity_snapshot(context, admission, equity_snapshot)

    occupying_reservations, occupying_positions = _reconstruct_occupants(
        context, admission.replay_id, admission.as_of_date, same_day_rank_cutoff=admission.rank_at_admission,
    )

    sessions = symbol_sessions(context.prices_df, candidate.symbol)
    horizons = tuple(
        _horizon_result(
            h,
            compute_forward_horizon(sessions, calendar, admission.as_of_date, h, context.manifest.outcome_data_end_date),
            candidate.signal_close,
        )
        for h in HORIZONS_SESSIONS
    )

    would_fill, fill_date, fill_reason, raw_price = _hypothetical_fill(sessions, admission.as_of_date, candidate.max_entry_price)

    return OpportunityCostResult(
        admission_id=admission.admission_id, candidate_id=admission.candidate_id, symbol=admission.symbol,
        as_of_date=admission.as_of_date, rank_at_admission=admission.rank_at_admission,
        signal_close=candidate.signal_close, max_entry_price=candidate.max_entry_price,
        open_position_count=open_position_count, reserved_order_count=reserved_order_count,
        occupying_reservations=occupying_reservations, occupying_open_positions=occupying_positions,
        hypothetical_would_have_filled=would_fill, hypothetical_fill_date=fill_date,
        hypothetical_fill_reason=fill_reason, hypothetical_raw_fill_price=raw_price,
        horizons=horizons,
    )
