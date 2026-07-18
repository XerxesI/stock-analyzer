"""Attribution funnel and counterfactual shadow-candidate metrics for a completed
Historical Sandbox Replay. Observational only -- see EXP-004: these metrics are never
used to tune the entry-price policy, candidate count, target, or holding horizon.

Funnel: eligible universe -> shadow top-10 -> actionable top-3 -> pending entry
orders -> filled positions -> closed positions. Reported with counts and conversion
rates at each stage, plus a counterfactual comparison (shadow top-10 vs. actionable
top-3 vs. unfilled/expired candidates vs. filled positions) so a later reader can
attribute performance differences to the ranking, the top-3 selection, the
already-open/pending suppression, the entry-price ceiling, entry expiry, or the exit
policy -- rather than lumping them all into one number.
"""

from __future__ import annotations

import statistics
from datetime import date

import pandas as pd

from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

TARGET_RETURN = 0.20


def _success_rate(hits: int, n: int) -> float | None:
    return (hits / n) if n else None


def load_target_label_lookup(features_path: str) -> dict[tuple[str, str], bool]:
    """(symbol, date_iso) -> target_20pct_20d, from the frozen SWING_20 feature
    dataset's own already-validated label column. Reused here rather than
    re-derived from fresh OHLC, so the shadow-ranking success rate reported by the
    replay is exactly the same label EXP-001/002/003 were built on -- not a
    redefinition relative to signal close vs. entry price."""

    df = pd.read_parquet(features_path, columns=["symbol", "date", "target_20pct_20d"])
    df["date"] = pd.to_datetime(df["date"]).dt.date.map(lambda d: d.isoformat())
    return {(row.symbol, row.date): bool(row.target_20pct_20d) for row in df.itertuples(index=False)}


def build_replay_metrics(
    repo: SandboxRepository,
    signal_start: date,
    signal_end: date,
    outcome_end: date,
    label_lookup: dict[tuple[str, str], bool] | None = None,
) -> dict[str, object]:
    conn = repo.connection  # read-only ad hoc queries; see SandboxRepository.connection docstring

    all_candidates = conn.execute(
        "SELECT * FROM ranked_candidates WHERE as_of_date BETWEEN ? AND ?",
        (signal_start.isoformat(), signal_end.isoformat()),
    ).fetchall()

    signal_dates = sorted({r["as_of_date"] for r in all_candidates})
    shadow_rows = [r for r in all_candidates if r["shadow_top10"]]
    actionable_rows = [r for r in all_candidates if r["actionable"]]
    excluded_rows = [r for r in all_candidates if not r["actionable"]]

    orders = conn.execute("SELECT * FROM entry_orders").fetchall()
    filled_orders = [o for o in orders if o["status"] == "FILLED"]
    expired_orders = [o for o in orders if o["status"] == "EXPIRED"]

    attempts = conn.execute("SELECT * FROM entry_order_attempts").fetchall()
    fills_at_open = [a for a in attempts if a["outcome"] == "FILLED_AT_OPEN"]
    fills_at_ceiling = [a for a in attempts if a["outcome"] == "FILLED_AT_CEILING"]
    no_fill_attempts = [a for a in attempts if a["outcome"] == "NO_FILL"]

    positions = conn.execute("SELECT * FROM virtual_positions").fetchall()
    open_positions = [p for p in positions if p["status"] == "OPEN"]
    closed_positions = [p for p in positions if p["status"] == "CLOSED"]
    target_exits = [p for p in closed_positions if p["exit_reason"] == "SELL_TARGET"]
    time_exits = [p for p in closed_positions if p["exit_reason"] == "SELL_TIME"]

    # Read holding_day_count/mfe/mae from each position's own FINAL position_snapshots
    # row (the append-only source of truth, ADR-006) rather than virtual_positions'
    # current-state columns -- those are kept in sync on close as of the fix that
    # accompanies this comment, but position_snapshots is authoritative regardless of
    # whether some future code path forgets to update virtual_positions again.
    final_snapshot_by_position = _final_snapshots(conn, [p["position_id"] for p in positions])
    holding_days = [
        final_snapshot_by_position[p["position_id"]]["holding_day_count"]
        for p in closed_positions
        if p["position_id"] in final_snapshot_by_position
    ]
    mfe_values = [
        final_snapshot_by_position[p["position_id"]]["mfe"]
        for p in positions
        if p["position_id"] in final_snapshot_by_position
    ]
    mae_values = [
        final_snapshot_by_position[p["position_id"]]["mae"]
        for p in positions
        if p["position_id"] in final_snapshot_by_position
    ]

    realized_returns = [p["realized_return"] for p in closed_positions if p["realized_return"] is not None]
    wins = [r for r in realized_returns if r > 0]

    exclusion_reasons: dict[str, int] = {}
    for r in excluded_rows:
        reason = r["exclusion_reason"] or "UNKNOWN"
        exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + 1

    by_rank: dict[int, int] = {}
    for r in shadow_rows:
        by_rank[r["daily_rank"]] = by_rank.get(r["daily_rank"], 0) + 1

    by_adv_quintile: dict[str, int] = {}
    for r in shadow_rows:
        key = r["adv_quintile"] or "UNKNOWN"
        by_adv_quintile[key] = by_adv_quintile.get(key, 0) + 1

    by_regime: dict[str, int] = {}
    for r in shadow_rows:
        key = r["market_regime"] or "UNKNOWN"
        by_regime[key] = by_regime.get(key, 0) + 1

    shadow_ranking_success = _shadow_ranking_success(shadow_rows, actionable_rows, label_lookup)

    max_simultaneous_open = _max_simultaneous_open(repo, signal_start, outcome_end)

    return {
        "period": {
            "signal_start_date": signal_start.isoformat(),
            "signal_end_date": signal_end.isoformat(),
            "outcome_data_end_date": outcome_end.isoformat(),
            "n_signal_dates": len(signal_dates),
        },
        "funnel": {
            "shadow_candidates_total": len(shadow_rows),
            "actionable_candidates_total": len(actionable_rows),
            "entry_orders_created": len(orders),
            "entry_orders_filled": len(filled_orders),
            "entry_orders_expired": len(expired_orders),
            "positions_opened": len(positions),
            "positions_closed": len(closed_positions),
            "positions_still_open_at_outcome_end": len(open_positions),
            "conversion_shadow_to_actionable": _success_rate(len(actionable_rows), len(shadow_rows)),
            "conversion_actionable_to_filled": _success_rate(len(filled_orders), len(actionable_rows)),
            "conversion_filled_to_target_exit": _success_rate(len(target_exits), len(closed_positions)),
        },
        "shadow_ranking": {
            "unique_shadow_candidate_rows": len(shadow_rows),
            "distribution_by_rank": dict(sorted(by_rank.items())),
            "distribution_by_adv_quintile": by_adv_quintile,
            "distribution_by_market_regime": by_regime,
            **shadow_ranking_success,
        },
        "candidate_selection": {
            "actionable_candidates_created": len(actionable_rows),
            "exclusions_by_reason": exclusion_reasons,
        },
        "entry_policy": {
            "orders_created": len(orders),
            "orders_filled": len(filled_orders),
            "fill_rate": _success_rate(len(filled_orders), len(orders)),
            "filled_at_open": len(fills_at_open),
            "filled_at_ceiling": len(fills_at_ceiling),
            "no_fill_attempts": len(no_fill_attempts),
            "orders_expired": len(expired_orders),
        },
        "position_lifecycle": {
            "positions_opened": len(positions),
            "positions_closed": len(closed_positions),
            "positions_still_open": len(open_positions),
            "sell_target_count": len(target_exits),
            "sell_target_pct": _success_rate(len(target_exits), len(closed_positions)),
            "sell_time_count": len(time_exits),
            "sell_time_pct": _success_rate(len(time_exits), len(closed_positions)),
            "holding_days_mean": statistics.mean(holding_days) if holding_days else None,
            "holding_days_median": statistics.median(holding_days) if holding_days else None,
            "realized_return_mean": statistics.mean(realized_returns) if realized_returns else None,
            "realized_return_median": statistics.median(realized_returns) if realized_returns else None,
            "win_rate": _success_rate(len(wins), len(realized_returns)),
            "mfe_mean": statistics.mean(mfe_values) if mfe_values else None,
            "mae_mean": statistics.mean(mae_values) if mae_values else None,
            "best_realized_return": max(realized_returns) if realized_returns else None,
            "worst_realized_return": min(realized_returns) if realized_returns else None,
            "total_virtual_pnl": sum(
                (p["exit_price"] - p["entry_price"]) * p["quantity"] for p in closed_positions if p["exit_price"] is not None
            ),
        },
        "operational": {
            "max_simultaneous_open_positions": max_simultaneous_open,
            "missing_data_events": conn.execute(
                "SELECT COUNT(*) FROM data_quality_events WHERE as_of_date BETWEEN ? AND ?",
                (signal_start.isoformat(), outcome_end.isoformat()),
            ).fetchone()[0],
        },
        "unresolved_positions": [
            {"position_id": p["position_id"], "symbol": p["symbol"], "entry_date": p["entry_date"]}
            for p in open_positions
        ],
    }


def _shadow_ranking_success(shadow_rows: list, actionable_rows: list, label_lookup: dict[tuple[str, str], bool] | None) -> dict[str, object]:
    """SWING_20 target-hit-rate (the frozen label, not an entry-price-relative
    redefinition) for the full shadow top-10 vs. the actionable top-3, and by rank,
    ADV quintile, and market regime -- the ranking-engine-level metric, independent
    of whether a candidate was ever filled."""

    if label_lookup is None:
        return {
            "note": "No label_lookup supplied -- shadow/actionable target-hit-rate not computed. "
            "Pass load_target_label_lookup(features_path) to build_replay_metrics to enable this."
        }

    def _hit(row) -> bool | None:
        return label_lookup.get((row["symbol"], row["as_of_date"]))

    def _rate(rows: list) -> tuple[float | None, int]:
        hits = [h for h in (_hit(r) for r in rows) if h is not None]
        return (_success_rate(sum(hits), len(hits)), len(hits))

    shadow_rate, shadow_n = _rate(shadow_rows)
    actionable_rate, actionable_n = _rate(actionable_rows)

    by_rank: dict[int, dict[str, object]] = {}
    for rank in sorted({r["daily_rank"] for r in shadow_rows}):
        rate, n = _rate([r for r in shadow_rows if r["daily_rank"] == rank])
        by_rank[rank] = {"target_hit_rate": rate, "n": n}

    by_adv: dict[str, dict[str, object]] = {}
    for quintile in sorted({r["adv_quintile"] for r in shadow_rows if r["adv_quintile"]}):
        rate, n = _rate([r for r in shadow_rows if r["adv_quintile"] == quintile])
        by_adv[quintile] = {"target_hit_rate": rate, "n": n}

    by_regime: dict[str, dict[str, object]] = {}
    for regime in sorted({r["market_regime"] for r in shadow_rows if r["market_regime"]}):
        rate, n = _rate([r for r in shadow_rows if r["market_regime"] == regime])
        by_regime[regime] = {"target_hit_rate": rate, "n": n}

    return {
        "top10_target_hit_rate": shadow_rate,
        "top10_n_labeled": shadow_n,
        "top3_actionable_target_hit_rate": actionable_rate,
        "top3_actionable_n_labeled": actionable_n,
        "target_hit_rate_by_rank": by_rank,
        "target_hit_rate_by_adv_quintile": by_adv,
        "target_hit_rate_by_market_regime": by_regime,
    }


def _final_snapshots(conn, position_ids: list[str]) -> dict[str, dict[str, object]]:
    """Each position's own latest (by as_of_date) position_snapshots row -- the
    correct source for a position's final holding_day_count/mfe/mae, per the
    EXP-004 review finding that virtual_positions' current-state columns could be
    one session stale for a closed position."""

    result: dict[str, dict[str, object]] = {}
    for position_id in position_ids:
        row = conn.execute(
            "SELECT holding_day_count, mfe, mae FROM position_snapshots "
            "WHERE position_id = ? ORDER BY as_of_date DESC LIMIT 1",
            (position_id,),
        ).fetchone()
        if row is not None:
            result[position_id] = {"holding_day_count": row["holding_day_count"], "mfe": row["mfe"], "mae": row["mae"]}
    return result


def _max_simultaneous_open(repo: SandboxRepository, signal_start: date, outcome_end: date) -> int:
    conn = repo.connection
    rows = conn.execute("SELECT entry_date, exit_date FROM virtual_positions").fetchall()
    events: list[tuple[str, int]] = []
    for r in rows:
        events.append((r["entry_date"], 1))
        if r["exit_date"]:
            events.append((r["exit_date"], -1))
    events.sort(key=lambda e: (e[0], -e[1]))  # opens before closes on the same date
    running = 0
    peak = 0
    for _, delta in events:
        running += delta
        peak = max(peak, running)
    return peak
