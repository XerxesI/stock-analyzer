"""Decision-quality report generation -- Revision 5, Section 25, Stage 14.

Generated once, after a replay completes, from the persisted decision-time/
accounting facts plus the frozen OHLCV, using Sections 20-24's frozen definitions.
Two-tier design:

- `compute_run_summary` aggregates ONE completed replay database (one variant/seed)
  into BUY/HOLD/SELL/Capacity quality summaries (Section 25's first four report
  sections). It iterates persisted orders/positions/snapshots/admissions and reduces
  the Stage 12-13 per-item diagnostic results to means/rates/distributions -- it
  never re-derives a decision, only aggregates already-computed post-hoc facts.
- `compute_selection_quality` (Section 25's fifth section) composes already-computed
  `RunQualitySummary` objects -- one for Variant B, one per Variant D seed. Each
  seed necessarily lives in its OWN isolated replay database (per EXP-004's
  "isolated DB, sequential" convention: `virtual_positions` and friends have no
  `replay_id` column, so Variant B and every Variant D seed cannot share one
  database) -- and reports Variant B's point value against the PERCENTILE RANK of
  that value within the Variant D seed distribution, mirroring Section 10's own
  `control_percentile_threshold` discipline: a point-vs-point comparison is never
  reported alone.

Every "distribution" reported here is a plain tuple of already-computed per-item
values (never re-fetched or re-derived), so a caller can compute whatever
additional statistic it needs without re-querying the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stock_analyzer.sandbox.domain.entry_order import FILLED_AT_CEILING
from stock_analyzer.sandbox.domain.position import CLOSED
from stock_analyzer.sandbox.domain.recommendation import SELL_TARGET, SELL_TIME
from stock_analyzer.sandbox.exp005.diagnostics import entry_timing, hold_quality, mfe_mae, opportunity_cost, sell_quality
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import DiagnosticsContext
from stock_analyzer.sandbox.exp005.diagnostics.hold_quality import ADVERSE, PROFITABLE, UNRESOLVED

BUY_HORIZONS = entry_timing.HORIZONS_SESSIONS
HOLD_HORIZONS = hold_quality.HORIZONS_SESSIONS
SELL_HORIZONS = sell_quality.HORIZONS_SESSIONS
CAPACITY_HORIZONS = opportunity_cost.HORIZONS_SESSIONS


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def percentile_rank(value: float, distribution: list[float]) -> float | None:
    """The percentage of `distribution` that is <= `value` -- e.g. 80.0 means
    `value` is at or above 80% of the distribution. Mirrors Section 10's
    control_percentile_threshold discipline: never a bare point-vs-point compare."""

    if not distribution:
        return None
    at_or_below = sum(1 for d in distribution if d <= value)
    return 100.0 * at_or_below / len(distribution)


# ------------------------------------------------------------------------- BUY


@dataclass(frozen=True)
class BuyQualitySummary:
    filled_count: int
    expired_count: int
    fill_rate: float | None
    entry_session_ambiguity_count: int
    mean_entry_gap_pct: float | None
    mean_slippage_cost: float | None
    mean_slippage_rate_pct: float | None
    target_hit_rate: float | None
    entry_gap_pct_distribution: tuple[float, ...]
    slippage_cost_distribution: tuple[float, ...]
    horizon_mean_forward_return_pct: dict[int, float | None]
    horizon_mean_mfe_pct: dict[int, float | None]
    horizon_mean_mae_pct: dict[int, float | None]
    horizon_mean_sessions_to_mfe: dict[int, float | None]
    horizon_mean_sessions_to_mae: dict[int, float | None]
    horizon_censored_count: dict[int, int]


def compute_buy_quality_summary(context: DiagnosticsContext, calendar: tuple[date, ...]) -> BuyQualitySummary:
    filled_orders = context.sandbox_repo.list_filled_orders()
    expired_orders = context.sandbox_repo.list_expired_orders()
    positions_by_order_id = {p.order_id: p for p in context.sandbox_repo.list_all_positions()}

    results = []
    for order in filled_orders:
        position = positions_by_order_id.get(order.order_id)
        if position is None:
            continue
        results.append(entry_timing.compute_entry_timing_for_filled_order(context, order, position, calendar))

    entry_gap_pct_distribution = tuple(r.entry_gap_pct for r in results if r.entry_gap_pct is not None)
    slippage_cost_distribution = tuple(r.slippage_cost for r in results)

    horizon_forward_return: dict[int, float | None] = {}
    horizon_mfe: dict[int, float | None] = {}
    horizon_mae: dict[int, float | None] = {}
    horizon_sessions_to_mfe: dict[int, float | None] = {}
    horizon_sessions_to_mae: dict[int, float | None] = {}
    horizon_censored: dict[int, int] = {}
    for i, horizon in enumerate(BUY_HORIZONS):
        horizon_results = [r.horizons[i] for r in results]
        horizon_forward_return[horizon] = _mean([h.forward_return_pct for h in horizon_results])
        horizon_mfe[horizon] = _mean([h.mfe_pct for h in horizon_results])
        horizon_mae[horizon] = _mean([h.mae_pct for h in horizon_results])
        horizon_sessions_to_mfe[horizon] = _mean([h.sessions_to_mfe for h in horizon_results])
        horizon_sessions_to_mae[horizon] = _mean([h.sessions_to_mae for h in horizon_results])
        horizon_censored[horizon] = sum(1 for h in horizon_results if h.is_censored)

    return BuyQualitySummary(
        filled_count=len(filled_orders),
        expired_count=len(expired_orders),
        fill_rate=_rate(len(filled_orders), len(filled_orders) + len(expired_orders)),
        entry_session_ambiguity_count=sum(1 for r in results if r.fill_reason == FILLED_AT_CEILING),
        mean_entry_gap_pct=_mean(list(entry_gap_pct_distribution)),
        mean_slippage_cost=_mean(list(slippage_cost_distribution)),
        mean_slippage_rate_pct=_mean([r.slippage_rate_pct for r in results]),
        target_hit_rate=_rate(sum(1 for r in results if r.target_reached_within_actual_holding_horizon), len(results)),
        entry_gap_pct_distribution=entry_gap_pct_distribution,
        slippage_cost_distribution=slippage_cost_distribution,
        horizon_mean_forward_return_pct=horizon_forward_return,
        horizon_mean_mfe_pct=horizon_mfe,
        horizon_mean_mae_pct=horizon_mae,
        horizon_mean_sessions_to_mfe=horizon_sessions_to_mfe,
        horizon_mean_sessions_to_mae=horizon_sessions_to_mae,
        horizon_censored_count=horizon_censored,
    )


# ------------------------------------------------------------------------ HOLD


_AGE_BUCKETS = (("1-5", lambda d: d <= 5), ("6-10", lambda d: 6 <= d <= 10), ("11+", lambda d: d >= 11))
_RETURN_BUCKETS = (
    ("<0%", lambda r: r is not None and r < 0.0),
    ("0-10%", lambda r: r is not None and 0.0 <= r < 0.10),
    (">=10%", lambda r: r is not None and r >= 0.10),
)
_HOLD_BUCKET_REPRESENTATIVE_HORIZON = 10


@dataclass(frozen=True)
class HoldQualityBucketStats:
    label: str
    snapshot_count: int
    target_reached_rate: float | None
    adverse_rate: float | None


@dataclass(frozen=True)
class HoldQualitySummary:
    hold_decision_count: int
    profitable_continuation_rate: float | None
    adverse_continuation_rate: float | None
    target_eventually_reached_rate: float | None
    time_exit_eventually_reached_rate: float | None
    unresolved_rate: float | None
    horizon_mean_forward_return_pct: dict[int, float | None]
    horizon_mean_mfe_pct: dict[int, float | None]
    horizon_mean_mae_pct: dict[int, float | None]
    horizon_target_reached_rate: dict[int, float | None]
    horizon_censored_count: dict[int, int]
    by_holding_age_bucket: tuple[HoldQualityBucketStats, ...]
    by_unrealized_return_bucket: tuple[HoldQualityBucketStats, ...]


def _bucket_stats(label: str, snapshots_with_results) -> HoldQualityBucketStats:
    if not snapshots_with_results:
        return HoldQualityBucketStats(label=label, snapshot_count=0, target_reached_rate=None, adverse_rate=None)
    horizon_index = HOLD_HORIZONS.index(_HOLD_BUCKET_REPRESENTATIVE_HORIZON)
    reps = [result.horizons[horizon_index] for _, result in snapshots_with_results]
    target_reached_rate = _rate(sum(1 for r in reps if r.target_reached), len(reps))
    adverse_rate = _rate(sum(1 for r in reps if r.forward_close_return_pct is not None and r.forward_close_return_pct < 0), len(reps))
    return HoldQualityBucketStats(label=label, snapshot_count=len(reps), target_reached_rate=target_reached_rate, adverse_rate=adverse_rate)


def compute_hold_quality_summary(context: DiagnosticsContext, calendar: tuple[date, ...]) -> HoldQualitySummary:
    hold_snapshots = context.sandbox_repo.list_hold_snapshots()
    positions_by_id = {p.position_id: p for p in context.sandbox_repo.list_all_positions()}

    results = []
    for snapshot in hold_snapshots:
        position = positions_by_id.get(snapshot.position_id)
        if position is None:
            continue
        results.append((snapshot, hold_quality.compute_hold_quality(context, snapshot, position, calendar)))

    horizon_forward_return: dict[int, float | None] = {}
    horizon_mfe: dict[int, float | None] = {}
    horizon_mae: dict[int, float | None] = {}
    horizon_target_rate: dict[int, float | None] = {}
    horizon_censored: dict[int, int] = {}
    for i, horizon in enumerate(HOLD_HORIZONS):
        horizon_results = [r.horizons[i] for _, r in results]
        horizon_forward_return[horizon] = _mean([h.forward_close_return_pct for h in horizon_results])
        horizon_mfe[horizon] = _mean([h.max_high_pct for h in horizon_results])
        horizon_mae[horizon] = _mean([h.min_low_pct for h in horizon_results])
        horizon_target_rate[horizon] = _rate(sum(1 for h in horizon_results if h.target_reached), len(horizon_results))
        horizon_censored[horizon] = sum(1 for h in horizon_results if h.is_censored)

    # Per-POSITION eventual-outcome rates (not per-snapshot, so a long-held position
    # does not dominate the rate simply by having more HOLD days).
    distinct_position_ids = {snapshot.position_id for snapshot, _ in results}
    outcome_positions = [positions_by_id[pid] for pid in distinct_position_ids]
    n = len(outcome_positions)
    result_by_position = {r.position_id: r for _, r in results}
    eventual_outcomes = [result_by_position[pid].eventual_outcome for pid in distinct_position_ids]
    profitable_rate = _rate(sum(1 for o in eventual_outcomes if o == PROFITABLE), n)
    adverse_rate = _rate(sum(1 for o in eventual_outcomes if o == ADVERSE), n)
    unresolved_rate = _rate(sum(1 for o in eventual_outcomes if o == UNRESOLVED), n)
    target_rate = _rate(sum(1 for pid in distinct_position_ids if positions_by_id[pid].exit_reason == SELL_TARGET), n)
    time_rate = _rate(sum(1 for pid in distinct_position_ids if positions_by_id[pid].exit_reason == SELL_TIME), n)

    age_buckets = tuple(
        _bucket_stats(label, [(s, r) for s, r in results if predicate(s.holding_day_count)])
        for label, predicate in _AGE_BUCKETS
    )
    return_buckets = tuple(
        _bucket_stats(label, [(s, r) for s, r in results if predicate(s.cumulative_unrealized_return)])
        for label, predicate in _RETURN_BUCKETS
    )

    return HoldQualitySummary(
        hold_decision_count=len(results),
        profitable_continuation_rate=profitable_rate,
        adverse_continuation_rate=adverse_rate,
        target_eventually_reached_rate=target_rate,
        time_exit_eventually_reached_rate=time_rate,
        unresolved_rate=unresolved_rate,
        horizon_mean_forward_return_pct=horizon_forward_return,
        horizon_mean_mfe_pct=horizon_mfe,
        horizon_mean_mae_pct=horizon_mae,
        horizon_target_reached_rate=horizon_target_rate,
        horizon_censored_count=horizon_censored,
        by_holding_age_bucket=age_buckets,
        by_unrealized_return_bucket=return_buckets,
    )


# ------------------------------------------------------------------------ SELL


@dataclass(frozen=True)
class SellQualitySummary:
    closed_position_count: int
    mean_realized_return_pct: float | None
    mean_mfe_captured_pct: float | None
    mean_peak_to_exit_giveback_pct: float | None
    mean_exit_efficiency: float | None
    target_exit_count: int
    time_exit_count: int
    target_exit_mean_realized_return_pct: float | None
    time_exit_mean_realized_return_pct: float | None
    horizon_mean_forward_return_pct: dict[int, float | None]
    horizon_mean_max_high_pct: dict[int, float | None]
    horizon_mean_min_low_pct: dict[int, float | None]
    horizon_target_reached_rate: dict[int, float | None]
    horizon_censored_count: dict[int, int]
    total_censored_post_exit_observations: int


def compute_sell_quality_summary(context: DiagnosticsContext, calendar: tuple[date, ...]) -> SellQualitySummary:
    closed_positions = [p for p in context.sandbox_repo.list_all_positions() if p.status == CLOSED]

    mfe_results = [mfe_mae.compute_mfe_mae(context, p) for p in closed_positions]
    sell_results = [sell_quality.compute_sell_quality(context, p, calendar) for p in closed_positions]

    target_positions = [p for p in closed_positions if p.exit_reason == SELL_TARGET]
    time_positions = [p for p in closed_positions if p.exit_reason == SELL_TIME]
    realized_by_position_id = {m.position_id: m.realized_or_mtm_return_pct for m in mfe_results}

    horizon_forward_return: dict[int, float | None] = {}
    horizon_max_high: dict[int, float | None] = {}
    horizon_min_low: dict[int, float | None] = {}
    horizon_target_rate: dict[int, float | None] = {}
    horizon_censored: dict[int, int] = {}
    for i, horizon in enumerate(SELL_HORIZONS):
        horizon_results = [r.horizons[i] for r in sell_results]
        horizon_forward_return[horizon] = _mean([h.close_to_close_return_pct for h in horizon_results])
        horizon_max_high[horizon] = _mean([h.max_high_pct for h in horizon_results])
        horizon_min_low[horizon] = _mean([h.min_low_pct for h in horizon_results])
        horizon_target_rate[horizon] = _rate(sum(1 for h in horizon_results if h.target_reached), len(horizon_results))
        horizon_censored[horizon] = sum(1 for h in horizon_results if h.is_censored)

    return SellQualitySummary(
        closed_position_count=len(closed_positions),
        mean_realized_return_pct=_mean([m.realized_or_mtm_return_pct for m in mfe_results]),
        mean_mfe_captured_pct=_mean([m.mfe_pct for m in mfe_results]),
        mean_peak_to_exit_giveback_pct=_mean([m.peak_to_exit_giveback_pct for m in mfe_results]),
        mean_exit_efficiency=_mean([m.exit_efficiency for m in mfe_results]),
        target_exit_count=len(target_positions),
        time_exit_count=len(time_positions),
        target_exit_mean_realized_return_pct=_mean([realized_by_position_id[p.position_id] for p in target_positions]),
        time_exit_mean_realized_return_pct=_mean([realized_by_position_id[p.position_id] for p in time_positions]),
        horizon_mean_forward_return_pct=horizon_forward_return,
        horizon_mean_max_high_pct=horizon_max_high,
        horizon_mean_min_low_pct=horizon_min_low,
        horizon_target_reached_rate=horizon_target_rate,
        horizon_censored_count=horizon_censored,
        total_censored_post_exit_observations=sum(horizon_censored.values()),
    )


# -------------------------------------------------------------------- CAPACITY


@dataclass(frozen=True)
class CapacityQualitySummary:
    no_capacity_count: int
    hypothetical_fill_rate: float | None
    horizon_mean_missed_mfe_pct: dict[int, float | None]
    horizon_mean_missed_mae_pct: dict[int, float | None]
    horizon_censored_count: dict[int, int]
    mean_open_position_count: float | None
    mean_reserved_order_count: float | None
    idle_cash_day_count: int
    total_equity_snapshot_days: int
    accepted_mean_realized_return_pct: float | None
    rejected_mean_horizon20_return_pct: float | None


def compute_capacity_quality_summary(
    context: DiagnosticsContext, replay_id: str, calendar: tuple[date, ...]
) -> CapacityQualitySummary:
    admissions = context.portfolio_repo.list_admissions_for_experiment(replay_id)
    no_capacity_admissions = [a for a in admissions if a.decision == "NO_CAPACITY"]
    results = [opportunity_cost.compute_opportunity_cost(context, a, calendar) for a in no_capacity_admissions]

    horizon_mfe: dict[int, float | None] = {}
    horizon_mae: dict[int, float | None] = {}
    horizon_censored: dict[int, int] = {}
    horizon20_returns: list[float | None] = []
    for i, horizon in enumerate(CAPACITY_HORIZONS):
        horizon_results = [r.horizons[i] for r in results]
        horizon_mfe[horizon] = _mean([h.mfe_pct for h in horizon_results])
        horizon_mae[horizon] = _mean([h.mae_pct for h in horizon_results])
        horizon_censored[horizon] = sum(1 for h in horizon_results if h.is_censored)
        if horizon == 20:
            horizon20_returns = [h.forward_return_pct for h in horizon_results]

    snapshots = context.portfolio_repo.list_equity_snapshots(replay_id)
    closed_positions = [p for p in context.sandbox_repo.list_all_positions() if p.status == CLOSED]
    accepted_returns = [mfe_mae.compute_mfe_mae(context, p).realized_or_mtm_return_pct for p in closed_positions]

    return CapacityQualitySummary(
        no_capacity_count=len(no_capacity_admissions),
        hypothetical_fill_rate=_rate(sum(1 for r in results if r.hypothetical_would_have_filled), len(results)),
        horizon_mean_missed_mfe_pct=horizon_mfe,
        horizon_mean_missed_mae_pct=horizon_mae,
        horizon_censored_count=horizon_censored,
        mean_open_position_count=_mean([float(s.open_position_count) for s in snapshots]),
        mean_reserved_order_count=_mean([float(s.reserved_order_count) for s in snapshots]),
        idle_cash_day_count=sum(1 for s in snapshots if s.open_position_count == 0 and s.reserved_order_count == 0),
        total_equity_snapshot_days=len(snapshots),
        accepted_mean_realized_return_pct=_mean(accepted_returns),
        rejected_mean_horizon20_return_pct=_mean(horizon20_returns),
    )


# ---------------------------------------------------------------------- RUN SUMMARY


@dataclass(frozen=True)
class RunQualitySummary:
    replay_id: str
    variant_id: str
    control_seed: int | None
    buy: BuyQualitySummary
    hold: HoldQualitySummary
    sell: SellQualitySummary
    capacity: CapacityQualitySummary


def compute_run_summary(
    context: DiagnosticsContext, replay_id: str, variant_id: str, control_seed: int | None, calendar: tuple[date, ...]
) -> RunQualitySummary:
    return RunQualitySummary(
        replay_id=replay_id,
        variant_id=variant_id,
        control_seed=control_seed,
        buy=compute_buy_quality_summary(context, calendar),
        hold=compute_hold_quality_summary(context, calendar),
        sell=compute_sell_quality_summary(context, calendar),
        capacity=compute_capacity_quality_summary(context, replay_id, calendar),
    )


# ------------------------------------------------------------- SELECTION QUALITY


_SELECTION_METRICS: dict[str, callable] = {
    "entry_quality_mean_entry_gap_pct": lambda s: s.buy.mean_entry_gap_pct,
    "mfe_captured_pct": lambda s: s.sell.mean_mfe_captured_pct,
    "realized_return_pct": lambda s: s.sell.mean_realized_return_pct,
    "exit_efficiency": lambda s: s.sell.mean_exit_efficiency,
    "target_hit_rate": lambda s: s.buy.target_hit_rate,
    "no_capacity_hypothetical_fill_rate": lambda s: s.capacity.hypothetical_fill_rate,
}


@dataclass(frozen=True)
class SelectionQualityMetricComparison:
    metric_name: str
    variant_b_value: float | None
    variant_d_distribution: tuple[float, ...]
    variant_d_mean: float | None
    variant_b_percentile_rank_within_d: float | None


@dataclass(frozen=True)
class SelectionQualityReport:
    variant_b_replay_id: str
    variant_d_seed_count: int
    metrics: tuple[SelectionQualityMetricComparison, ...]


def compute_selection_quality(
    variant_b: RunQualitySummary, variant_d_seeds: list[RunQualitySummary]
) -> SelectionQualityReport:
    metrics = []
    for name, extractor in _SELECTION_METRICS.items():
        b_value = extractor(variant_b)
        d_distribution = tuple(v for v in (extractor(s) for s in variant_d_seeds) if v is not None)
        metrics.append(
            SelectionQualityMetricComparison(
                metric_name=name,
                variant_b_value=b_value,
                variant_d_distribution=d_distribution,
                variant_d_mean=_mean(list(d_distribution)),
                variant_b_percentile_rank_within_d=(
                    percentile_rank(b_value, list(d_distribution)) if b_value is not None else None
                ),
            )
        )
    return SelectionQualityReport(
        variant_b_replay_id=variant_b.replay_id,
        variant_d_seed_count=len(variant_d_seeds),
        metrics=tuple(metrics),
    )
