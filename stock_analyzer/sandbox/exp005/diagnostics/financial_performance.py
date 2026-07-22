"""Financial-feasibility report -- Revision 5, Section 10 (missing entirely from
Stages 11-15 until the Stage 11-15 closure cycle, finding 1).

This is the module that actually answers the project's primary research
question -- does frozen Model 2's ranking carry economically usable value? --
which none of `report_generator.py`'s decision-quality sections do; those cover
*how* BUY/HOLD/SELL/capacity decisions were executed, never whether the
resulting portfolio made money or how that compares to Variant D / the frozen
Section 10 thresholds.

**Provenance note on the exact Section 10 criteria wording:** the current frozen
document's Section 10 reads "Unchanged from Revision 2 except: [two arithmetic
clarifications]" -- Revision 2's own full criteria prose was never separately
committed to this repository (this repo's git history contains exactly one
commit that ever added this file, `16d2b45`, already at Revision 5; there is no
earlier draft to diff against). The authoritative, already-frozen, already-
implemented numeric source is `exp005.config.FeasibilityCriteria` (built in
Stage 1 from that same Revision 2 text, and already used for exact-equality
manifest verification throughout Stages 9-10): `max_drawdown_threshold=0.20`,
`largest_win_pct_of_net_profit_threshold=0.50`, `control_percentile_threshold=80.0`,
`min_profit_factor=1.0`. This module's five criteria are the direct, literal
application of those four frozen thresholds plus Section 10's own explicit text
(the positivity/concentration clarification for the "largest winner removed"
diagnostic) -- no threshold is invented or reinterpreted here.

**Data sources, exactly as Section 10/Section 8.5 specify -- nothing else:**
- `portfolio_equity_snapshots` for daily equity, drawdown, and quarterly returns
  (Section 8.5: "drawdown and quarterly returns are computed exclusively by
  reading this table").
- Paired BUY/SELL `executions` for exact realized per-trade P&L (their signed
  `net_cash_flow_units` already include commission and slippage).
- The final equity snapshot and unresolved (still-open) positions for
  mark-to-market ending results.
- The frozen `feasibility_criteria` recorded on the Experiment Manifest.

Every money/price/quantity value is read and combined in exact integer
fixed-point units (`domain/units.py`) until the final dataclass conversion to
float for presentation -- consistent with the rest of EXP-005's accounting.

Two-tier design, mirroring `report_generator.py`: `compute_financial_performance`
computes everything for ONE completed replay database (a `FinancialPerformanceReport`,
Section 10's per-run figures); `compute_feasibility_verdict` composes a Variant B
report with a list of Variant D seed reports (each necessarily its own isolated
database) into the final pass/fail verdict, including the required Variant-B-vs-D
percentile comparison. A criterion whose value cannot be determined (e.g. no
closed winning trade exists to measure concentration against) is reported with
`value=None`/`passed=None`, and the overall verdict is `None` (undetermined) if
ANY criterion is undetermined -- never silently treated as a pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stock_analyzer.sandbox.domain.position import CLOSED
from stock_analyzer.sandbox.exp005.config import DEFAULT_CONTROL_SEEDS, VARIANT_B, VARIANT_D
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import DiagnosticsContext
from stock_analyzer.sandbox.exp005.diagnostics.report_generator import percentile_rank
from stock_analyzer.sandbox.exp005.domain.execution import BUY
from stock_analyzer.sandbox.exp005.domain.units import money_units_to_float, to_money_units

CRITERION_POSITIVE_NET_PNL = "positive_net_pnl"
CRITERION_BEATS_CONTROL_PERCENTILE = "beats_control_percentile"
CRITERION_MAX_DRAWDOWN_WITHIN_THRESHOLD = "max_drawdown_within_threshold"
CRITERION_PROFIT_FACTOR_WITHIN_THRESHOLD = "profit_factor_within_threshold"
CRITERION_LARGEST_WINNER_CONCENTRATION_WITHIN_THRESHOLD = "largest_winner_concentration_within_threshold"


class FinancialPerformanceComputationError(RuntimeError):
    """Raised when the persisted data is insufficient to compute a financial
    performance report at all (no equity snapshots for this replay)."""


class ControlGroupValidationError(RuntimeError):
    """Raised when the Variant D reports handed to `compute_feasibility_verdict`
    are structurally invalid -- a duplicate seed, a seed outside the frozen
    `DEFAULT_CONTROL_SEEDS` list, a "control" report that isn't actually Variant
    D with a seed, or a Variant B report that isn't actually Variant B without
    one. This is never silently tolerated; a caller assembling the wrong set of
    reports is a genuine integrity problem, not a "not enough data yet" case
    (see the module docstring's distinction from an merely INCOMPLETE, but
    otherwise valid, control group)."""


class ExperimentIdentityMismatchError(RuntimeError):
    """Raised when a Variant B report and the Variant D reports it is being
    compared against do not provably originate from the same frozen experiment
    (Stage 11-15 third closure) -- same manifest artifact, same model, same
    feature/OHLC lineage, same signal/outcome period, and the same feasibility
    criteria. The ONLY fields allowed to differ between reports being compared
    are `variant_id` and `control_seed`; everything else must be identical, or
    the comparison is scientifically meaningless even when every individual
    number in it was computed correctly."""


@dataclass(frozen=True)
class ClosedTradeResult:
    position_id: str
    candidate_id: str
    symbol: str
    entry_date: date
    exit_date: date
    net_pnl_units: int
    net_pnl: float
    is_win: bool


@dataclass(frozen=True)
class DrawdownResult:
    max_drawdown_pct: float
    peak_date: date | None
    peak_equity: float | None
    trough_date: date | None
    trough_equity: float | None


@dataclass(frozen=True)
class QuarterlyReturn:
    year: int
    quarter: int
    start_date: date
    end_date: date
    start_equity: float
    end_equity: float
    return_pct: float | None


@dataclass(frozen=True)
class OpenPositionMarkResult:
    position_id: str
    candidate_id: str
    symbol: str
    entry_date: date
    unrealized_gain_units: int
    unrealized_gain: float


@dataclass(frozen=True)
class FinancialPerformanceReport:
    replay_id: str
    variant_id: str
    control_seed: int | None
    # Provenance identity (Stage 11-15 third closure) -- all taken from the
    # already-verified DiagnosticsContext, never a caller argument, so two
    # reports can be checked for genuine comparability before ever being
    # compared (see `_validate_comparable_provenance`). Only `variant_id`/
    # `control_seed` above are expected to differ between a Variant B report
    # and its Variant D control group; everything below must be identical.
    manifest_artifact_hash: str
    configuration_hash: str
    model_version: str
    feature_snapshot_id: str
    market_data_snapshot_id: str
    signal_start_date: date
    signal_end_date: date
    outcome_data_end_date: date
    feasibility_criteria: dict
    starting_equity: float
    ending_equity: float
    net_pnl: float
    net_return_pct: float
    drawdown: DrawdownResult
    quarterly_returns: tuple[QuarterlyReturn, ...]
    closed_trade_count: int
    win_count: int
    loss_count: int
    profit_factor: float | None
    closed_trades: tuple[ClosedTradeResult, ...]
    largest_closed_winning_trade: ClosedTradeResult | None
    largest_closed_winning_trade_pct_of_net_pnl: float | None
    net_pnl_minus_largest_winning_trade: float | None
    remains_positive_after_removing_largest_winner: bool | None
    largest_open_position: OpenPositionMarkResult | None
    largest_open_position_pct_of_net_pnl: float | None
    open_position_market_value_pct_of_ending_equity: float | None


def _quarter_of(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def _compute_drawdown(snapshots) -> DrawdownResult:
    peak_units = snapshots[0].total_equity_units
    peak_date = snapshots[0].as_of_date
    max_drawdown_pct = 0.0
    max_peak_units, max_peak_date = peak_units, peak_date
    max_trough_units, max_trough_date = peak_units, peak_date

    for snap in snapshots:
        if snap.total_equity_units > peak_units:
            peak_units, peak_date = snap.total_equity_units, snap.as_of_date
        if peak_units > 0:
            drawdown_pct = (peak_units - snap.total_equity_units) / peak_units
            if drawdown_pct > max_drawdown_pct:
                max_drawdown_pct = drawdown_pct
                max_peak_units, max_peak_date = peak_units, peak_date
                max_trough_units, max_trough_date = snap.total_equity_units, snap.as_of_date

    if max_drawdown_pct == 0.0:
        return DrawdownResult(max_drawdown_pct=0.0, peak_date=None, peak_equity=None, trough_date=None, trough_equity=None)
    return DrawdownResult(
        max_drawdown_pct=max_drawdown_pct,
        peak_date=max_peak_date, peak_equity=money_units_to_float(max_peak_units),
        trough_date=max_trough_date, trough_equity=money_units_to_float(max_trough_units),
    )


def _compute_quarterly_returns(snapshots) -> tuple[QuarterlyReturn, ...]:
    if not snapshots:
        return ()

    def _build(key, start_date, end_date, start_units, end_units) -> QuarterlyReturn:
        return_pct = (end_units - start_units) / start_units if start_units != 0 else None
        return QuarterlyReturn(
            year=key[0], quarter=key[1], start_date=start_date, end_date=end_date,
            start_equity=money_units_to_float(start_units), end_equity=money_units_to_float(end_units),
            return_pct=return_pct,
        )

    results = []
    quarter_start_units = snapshots[0].total_equity_units
    current_key = _quarter_of(snapshots[0].as_of_date)
    current_start_date = snapshots[0].as_of_date
    current_end_date = snapshots[0].as_of_date
    current_end_units = snapshots[0].total_equity_units

    for snap in snapshots[1:]:
        key = _quarter_of(snap.as_of_date)
        if key != current_key:
            results.append(_build(current_key, current_start_date, current_end_date, quarter_start_units, current_end_units))
            quarter_start_units = current_end_units
            current_key = key
            current_start_date = snap.as_of_date
        current_end_date = snap.as_of_date
        current_end_units = snap.total_equity_units

    results.append(_build(current_key, current_start_date, current_end_date, quarter_start_units, current_end_units))
    return tuple(results)


def _closed_trades(context: DiagnosticsContext) -> tuple[ClosedTradeResult, ...]:
    closed_positions = [p for p in context.sandbox_repo.list_all_positions() if p.status == CLOSED]
    trades = []
    for position in closed_positions:
        executions = context.portfolio_repo.list_executions_for_position(position.position_id)
        buy_execution = next((e for e in executions if e.side == BUY), None)
        sell_execution = next((e for e in executions if e.side != BUY), None)
        if buy_execution is None or sell_execution is None:
            continue
        net_pnl_units = buy_execution.net_cash_flow_units + sell_execution.net_cash_flow_units
        trades.append(
            ClosedTradeResult(
                position_id=position.position_id, candidate_id=position.candidate_id, symbol=position.symbol,
                entry_date=position.entry_date, exit_date=position.exit_date,
                net_pnl_units=net_pnl_units, net_pnl=money_units_to_float(net_pnl_units), is_win=net_pnl_units > 0,
            )
        )
    return tuple(sorted(trades, key=lambda t: (t.exit_date, t.position_id)))


def _open_positions_marked(context: DiagnosticsContext) -> tuple[OpenPositionMarkResult, ...]:
    open_positions = [p for p in context.sandbox_repo.list_all_positions() if p.status != CLOSED]
    results = []
    for position in open_positions:
        executions = context.portfolio_repo.list_executions_for_position(position.position_id)
        buy_execution = next((e for e in executions if e.side == BUY), None)
        if buy_execution is None:
            continue
        mark_price = position.current_close if position.current_close is not None else position.entry_price
        market_value_units = to_money_units(position.quantity * mark_price)
        # The exact total cash that left the portfolio for this BUY, including
        # slippage -- `gross_notional + commission` alone omits it (Stage
        # 11-15 second closure, finding 3). `net_cash_flow_units` is already
        # negative for a BUY (cash leaving), so negate it for a cost basis.
        cost_basis_units = -buy_execution.net_cash_flow_units
        unrealized_gain_units = market_value_units - cost_basis_units
        results.append(
            OpenPositionMarkResult(
                position_id=position.position_id, candidate_id=position.candidate_id, symbol=position.symbol,
                entry_date=position.entry_date, unrealized_gain_units=unrealized_gain_units,
                unrealized_gain=money_units_to_float(unrealized_gain_units),
            )
        )
    return tuple(results)


def compute_financial_performance(context: DiagnosticsContext) -> FinancialPerformanceReport:
    """`replay_id`/`variant_id`/`control_seed` are never caller-supplied (Stage
    11-15 third closure) -- all identity comes from the already hash-verified
    `context`, so a Variant D run's report can never be mislabeled Variant B
    (or assigned a different seed) after the fact."""

    replay_id = context.replay_id
    snapshots = context.portfolio_repo.list_equity_snapshots(replay_id)
    if not snapshots:
        raise FinancialPerformanceComputationError(
            f"no portfolio_equity_snapshots rows exist for replay_id={replay_id!r} -- a financial "
            "performance report requires at least one snapshot."
        )

    starting_equity_units = snapshots[0].total_equity_units
    ending_equity_units = snapshots[-1].total_equity_units
    net_pnl_units = ending_equity_units - starting_equity_units
    net_pnl = money_units_to_float(net_pnl_units)
    net_return_pct = net_pnl_units / starting_equity_units if starting_equity_units != 0 else 0.0

    drawdown = _compute_drawdown(snapshots)
    quarterly_returns = _compute_quarterly_returns(snapshots)

    trades = _closed_trades(context)
    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if t.net_pnl_units < 0]
    # Integer-first (Stage 11-15 second closure, finding 4): gross wins/losses
    # are summed in exact integer money units; the division into a float ratio
    # is the ONE, final presentation-layer conversion -- never a running sum of
    # already-rounded floats.
    gross_wins_units = sum(t.net_pnl_units for t in wins)
    gross_losses_units = sum(-t.net_pnl_units for t in losses)
    if not trades:
        profit_factor = None
    elif gross_losses_units == 0:
        profit_factor = float("inf") if gross_wins_units > 0 else None
    else:
        profit_factor = gross_wins_units / gross_losses_units

    largest_winner = max(wins, key=lambda t: t.net_pnl_units) if wins else None
    if largest_winner is not None and net_pnl > 0:
        largest_winner_pct = largest_winner.net_pnl / net_pnl
        net_pnl_minus_largest_winner = net_pnl - largest_winner.net_pnl
        remains_positive = net_pnl_minus_largest_winner > 0
    elif largest_winner is None:
        largest_winner_pct = None
        net_pnl_minus_largest_winner = net_pnl
        remains_positive = net_pnl > 0
    else:
        largest_winner_pct = None
        net_pnl_minus_largest_winner = None
        remains_positive = None

    open_marks = _open_positions_marked(context)
    # Only a genuinely UNREALIZED-GAIN position can be "the largest winner" --
    # Stage 11-15 second closure, finding 3: if every open position is
    # currently underwater, there is no unresolved winner to report, and the
    # field must be undetermined (None), never "the least-bad loss."
    positive_open_marks = [p for p in open_marks if p.unrealized_gain_units > 0]
    largest_open = max(positive_open_marks, key=lambda p: p.unrealized_gain_units) if positive_open_marks else None
    largest_open_pct = (largest_open.unrealized_gain / net_pnl) if (largest_open is not None and net_pnl > 0) else None
    open_value_pct_of_ending_equity = (
        snapshots[-1].open_position_market_value_units / ending_equity_units if ending_equity_units != 0 else None
    )

    return FinancialPerformanceReport(
        replay_id=replay_id, variant_id=context.variant_id, control_seed=context.control_seed,
        manifest_artifact_hash=context.manifest_artifact_hash, configuration_hash=context.configuration_hash,
        model_version=context.manifest.model_version, feature_snapshot_id=context.manifest.feature_snapshot_id,
        market_data_snapshot_id=context.manifest.ohlc_hash, signal_start_date=context.manifest.signal_start_date,
        signal_end_date=context.manifest.signal_end_date, outcome_data_end_date=context.manifest.outcome_data_end_date,
        feasibility_criteria=context.feasibility_criteria,
        starting_equity=money_units_to_float(starting_equity_units), ending_equity=money_units_to_float(ending_equity_units),
        net_pnl=net_pnl, net_return_pct=net_return_pct, drawdown=drawdown, quarterly_returns=quarterly_returns,
        closed_trade_count=len(trades), win_count=len(wins), loss_count=len(losses), profit_factor=profit_factor,
        closed_trades=trades, largest_closed_winning_trade=largest_winner,
        largest_closed_winning_trade_pct_of_net_pnl=largest_winner_pct,
        net_pnl_minus_largest_winning_trade=net_pnl_minus_largest_winner,
        remains_positive_after_removing_largest_winner=remains_positive,
        largest_open_position=largest_open, largest_open_position_pct_of_net_pnl=largest_open_pct,
        open_position_market_value_pct_of_ending_equity=open_value_pct_of_ending_equity,
    )


@dataclass(frozen=True)
class FeasibilityCriterionResult:
    name: str
    value: float | None
    threshold: float
    comparison: str
    passed: bool | None


@dataclass(frozen=True)
class FeasibilityVerdict:
    variant_b_replay_id: str
    variant_d_seed_count: int
    criteria: tuple[FeasibilityCriterionResult, ...]
    verdict: bool | None


def _criterion(name: str, value: float | None, threshold: float, comparison: str) -> FeasibilityCriterionResult:
    if value is None:
        passed = None
    elif comparison == ">":
        passed = value > threshold
    elif comparison == ">=":
        passed = value >= threshold
    elif comparison == "<=":
        passed = value <= threshold
    else:
        raise ValueError(f"unrecognized comparison {comparison!r}")
    return FeasibilityCriterionResult(name=name, value=value, threshold=threshold, comparison=comparison, passed=passed)


def _validate_control_group(variant_b: FinancialPerformanceReport, variant_d_reports: list[FinancialPerformanceReport]) -> None:
    """Structural validity only -- never tolerated, regardless of count (Stage
    11-15 second closure, finding 1). A merely INCOMPLETE but otherwise valid
    control group (fewer than 50 seeds, no duplicates, no foreign seeds) is NOT
    an error here -- see `_is_complete_control_group`, checked separately -- it
    is the ordinary state before all 50 seeds have run."""

    if variant_b.variant_id != VARIANT_B:
        raise ControlGroupValidationError(
            f"the Variant B report's own variant_id is {variant_b.variant_id!r}, not {VARIANT_B!r}."
        )
    if variant_b.control_seed is not None:
        raise ControlGroupValidationError(
            f"the Variant B report carries a control_seed ({variant_b.control_seed!r}) -- Variant B must not."
        )

    seen_seeds: set[int] = set()
    for report in variant_d_reports:
        if report.variant_id != VARIANT_D:
            raise ControlGroupValidationError(
                f"control report {report.replay_id!r} has variant_id {report.variant_id!r}, not {VARIANT_D!r}."
            )
        if report.control_seed is None:
            raise ControlGroupValidationError(f"control report {report.replay_id!r} has no control_seed.")
        if report.control_seed in seen_seeds:
            raise ControlGroupValidationError(f"duplicate control_seed {report.control_seed!r} in the control group.")
        seen_seeds.add(report.control_seed)
        if report.control_seed not in DEFAULT_CONTROL_SEEDS:
            raise ControlGroupValidationError(
                f"control_seed {report.control_seed!r} (report {report.replay_id!r}) is not in the frozen "
                f"{len(DEFAULT_CONTROL_SEEDS)}-seed DEFAULT_CONTROL_SEEDS list."
            )


def _is_complete_control_group(variant_d_reports: list[FinancialPerformanceReport]) -> bool:
    """True only if `variant_d_reports` is EXACTLY the frozen 50-seed set --
    never an arbitrary count (Stage 11-15 second closure, finding 1). Callers
    must run `_validate_control_group` first so "exactly 50" also implies
    unique and all-frozen-seeds by construction."""

    return len(variant_d_reports) == len(DEFAULT_CONTROL_SEEDS)


# The provenance fields that must be IDENTICAL between a Variant B report and
# every Variant D report it is compared against (Stage 11-15 third closure) --
# `variant_id`/`control_seed` are deliberately excluded, since those are
# exactly the two fields that are SUPPOSED to differ.
_COMPARABLE_PROVENANCE_FIELDS = (
    "manifest_artifact_hash",
    "model_version",
    "feature_snapshot_id",
    "market_data_snapshot_id",
    "signal_start_date",
    "signal_end_date",
    "outcome_data_end_date",
    "feasibility_criteria",
)


def _validate_comparable_provenance(
    variant_b: FinancialPerformanceReport, variant_d_reports: list[FinancialPerformanceReport]
) -> None:
    """Every report being compared must provably come from the same frozen
    manifest artifact, model, feature/OHLC lineage, and period -- checked field
    by field rather than relying on `manifest_artifact_hash` equality alone to
    imply the rest, so a mismatch in any one of them fails closed with a
    specific, identifiable reason (Stage 11-15 third closure, finding 2's
    sibling: a scientifically meaningless comparison must never look like a
    computed one)."""

    for field_name in _COMPARABLE_PROVENANCE_FIELDS:
        expected = getattr(variant_b, field_name)
        for report in variant_d_reports:
            actual = getattr(report, field_name)
            if actual != expected:
                raise ExperimentIdentityMismatchError(
                    f"control report {report.replay_id!r}'s {field_name} ({actual!r}) does not match "
                    f"Variant B report {variant_b.replay_id!r}'s {field_name} ({expected!r}) -- reports "
                    "being compared must come from the exact same frozen manifest, period, model, and "
                    "artifacts; only variant_id and control_seed may differ."
                )


def compute_feasibility_verdict(
    variant_b: FinancialPerformanceReport,
    variant_d_reports: list[FinancialPerformanceReport],
) -> FeasibilityVerdict:
    """Composes an already-computed Variant B `FinancialPerformanceReport` with a
    list of Variant D seed reports (each necessarily its own isolated replay
    database) into the final pass/fail verdict against the frozen feasibility
    criteria BAKED INTO each report at `compute_financial_performance` time
    (Stage 11-15 third closure, finding 3) -- never a dict a caller could swap
    out after seeing the results. `_validate_comparable_provenance` requires
    Variant B's own `feasibility_criteria` to already equal every Variant D
    report's, so using `variant_b.feasibility_criteria` here is not an
    arbitrary choice of one report over another.

    `variant_d_reports` is validated structurally (raising `ControlGroupValidationError`
    for a duplicate seed, a seed outside the frozen 50, or a report that isn't
    genuinely Variant B/D) and then checked for EXACT completeness against the
    frozen 50-seed `DEFAULT_CONTROL_SEEDS` list -- an incomplete-but-otherwise-
    valid control group never produces a percentile result (Stage 11-15 second
    closure, finding 1): `beats_control_percentile` is reported as
    undetermined, not computed from whatever subset happens to be available.

    Verdict logic is three-tier (Stage 11-15 second closure, finding 2): ANY
    confirmed failure makes the whole verdict `False`, even if another
    criterion is merely undetermined -- a known failure is never masked by an
    unrelated unknown. Only once no criterion is `False` does an undetermined
    criterion make the verdict `None`. `True` requires every criterion `True`."""

    _validate_control_group(variant_b, variant_d_reports)
    _validate_comparable_provenance(variant_b, variant_d_reports)

    feasibility_criteria = variant_b.feasibility_criteria
    max_drawdown_threshold = float(feasibility_criteria["max_drawdown_threshold"])
    largest_win_pct_threshold = float(feasibility_criteria["largest_win_pct_of_net_profit_threshold"])
    control_percentile_threshold = float(feasibility_criteria["control_percentile_threshold"])
    min_profit_factor = float(feasibility_criteria["min_profit_factor"])

    if _is_complete_control_group(variant_d_reports):
        d_returns = [r.net_return_pct for r in variant_d_reports]
        b_percentile = percentile_rank(variant_b.net_return_pct, d_returns)
    else:
        b_percentile = None

    criteria = (
        _criterion(CRITERION_POSITIVE_NET_PNL, variant_b.net_pnl, 0.0, ">"),
        _criterion(CRITERION_BEATS_CONTROL_PERCENTILE, b_percentile, control_percentile_threshold, ">="),
        _criterion(CRITERION_MAX_DRAWDOWN_WITHIN_THRESHOLD, variant_b.drawdown.max_drawdown_pct, max_drawdown_threshold, "<="),
        # `>=` handles profit_factor == float("inf") correctly on its own (inf is
        # >= any finite threshold) -- no special-casing needed.
        _criterion(CRITERION_PROFIT_FACTOR_WITHIN_THRESHOLD, variant_b.profit_factor, min_profit_factor, ">="),
        _criterion(
            CRITERION_LARGEST_WINNER_CONCENTRATION_WITHIN_THRESHOLD,
            variant_b.largest_closed_winning_trade_pct_of_net_pnl, largest_win_pct_threshold, "<=",
        ),
    )

    # Three-tier: a confirmed failure always wins over an unrelated unknown.
    if any(c.passed is False for c in criteria):
        verdict = False
    elif any(c.passed is None for c in criteria):
        verdict = None
    else:
        verdict = True

    return FeasibilityVerdict(
        variant_b_replay_id=variant_b.replay_id, variant_d_seed_count=len(variant_d_reports),
        criteria=criteria, verdict=verdict,
    )
