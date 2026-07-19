"""`NO_CAPACITY` opportunity-cost evaluation -- Revision 5, Section 24, Stage 13.

For every `portfolio_admissions` row with `decision='NO_CAPACITY'`: the candidate's
rank and score at admission; signal close and max entry price; the portfolio's
capacity state at rejection (open/reserved counts, from that day's
`portfolio_equity_snapshots` row); which specific reservations occupied the 10 slots
that day (reconstructed from `slot_reservations`' own `created_at`/`resolved_at`
timestamps -- its `status` column alone is a mutable, current-only field and cannot
answer a question about a PAST date, see infrastructure/repository.py's
`list_reservations_for_experiment`); subsequent 1/5/10/20-session returns and
MFE/MAE from signal close; whether the existing ADR-007 entry rule would have
filled; `is_censored` (Section 27).

**This is strictly observational.** `compute_opportunity_cost` never calls any
write method on any repository -- it only ever reads `PortfolioAdmission`/
`RankedCandidate`/`PortfolioEquitySnapshot`/`SlotReservation` rows already persisted
by the real replay, plus the frozen OHLCV. It must never create a
`virtual_positions` row, a `slot_reservations` row, or any cash-ledger entry.

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

from stock_analyzer.sandbox.domain.entry_order import FILLED_AT_CEILING, FILLED_AT_OPEN, NO_FILL
from stock_analyzer.sandbox.exp005.diagnostics._shared import compute_forward_horizon, symbol_sessions
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import DiagnosticsContext
from stock_analyzer.sandbox.exp005.domain.admission import NO_CAPACITY, PortfolioAdmission
from stock_analyzer.sandbox.exp005.domain.units import money_units_to_float

HORIZONS_SESSIONS = (1, 5, 10, 20)

# Mirrors SandboxConfig.entry_validity_sessions's frozen default (Section 24: "the
# candidate's own 2-session validity window") -- not imported, to avoid pulling in
# stock_analyzer.sandbox.config from decision-time policy for a diagnostic constant.
ENTRY_VALIDITY_SESSIONS = 2


class OpportunityCostComputationError(RuntimeError):
    """Raised for an admission whose decision is not NO_CAPACITY, or for a
    candidate_id with no ranked_candidates row."""


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


def _occupying_reservations(context: DiagnosticsContext, admission: PortfolioAdmission) -> tuple[OccupyingReservation, ...]:
    all_reservations = context.portfolio_repo.list_reservations_for_experiment(admission.replay_id)
    as_of = admission.as_of_date
    occupying = [
        r for r in all_reservations
        if r.created_at.date() <= as_of and (r.resolved_at is None or r.resolved_at.date() > as_of)
    ]
    return tuple(
        OccupyingReservation(
            reservation_id=r.reservation_id, admission_id=r.admission_id, candidate_id=r.candidate_id,
            symbol=r.symbol, reserved_amount=money_units_to_float(r.reserved_amount_units),
        )
        for r in occupying
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
        occupying_reservations=_occupying_reservations(context, admission),
        hypothetical_would_have_filled=would_fill, hypothetical_fill_date=fill_date,
        hypothetical_fill_reason=fill_reason, hypothetical_raw_fill_price=raw_price,
        horizons=horizons,
    )
