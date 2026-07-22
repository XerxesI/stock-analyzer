"""EXP-005 -- read-only post-hoc price-path study for the real Variant B replay.

Answers a narrower question than the full feasibility verdict: did the stocks Model 2
selected actually go UP after purchase, and did the program exit at a sensible moment?
This is an "event study" over price action around each of the 108 actually-filled BUY
positions -- NOT a new exit policy, NOT a capital-constrained portfolio simulation, and
Variant D is never touched.

Read-only by construction: opens the existing, already-audited Variant B replay.db via
a SQLite immutable/read-only URI connection (`mode=ro`), so a write is not merely
avoided by discipline but structurally impossible. Loads through the same mediated
`load_diagnostics_context` boundary the rest of Stage 11-15 uses, so this analysis is
provably tied to the exact same frozen manifest/prices artifact as everything else --
never a separately-read, unverified copy.

For each position, the observation window is:
    [-5 .. -1] sessions before entry_date (that SYMBOL's own trading sessions,
               mirroring the existing diagnostics package's `_shared.symbol_sessions`
               convention -- never a generic business-day calendar)
    0          entry_date itself
    [+1 .. +42] sessions after entry_date

This intentionally runs PAST the experiment's own `outcome_data_end_date` (2025-10-20)
-- that boundary exists to stop new decision-making, not to hide already-frozen
historical price data from a purely observational post-hoc study. The frozen
prices.parquet artifact itself extends well past it (into 2026), so this is still
exclusively "already-frozen" data, per the pre-registration's Section 30 contract.

Outputs (all under the same real-run directory, alongside the existing artifacts):
    variant_b_price_path_daily.csv     -- one row per (position, date)
    variant_b_price_path_summary.csv   -- one row per position
    variant_b_price_path_equal_weight_index.csv -- analytical cohort index (NOT a
                                           tradeable portfolio -- see column docs below)
    variant_b_price_path_metadata.json -- hashes, window definition, counts, checksums
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_analyzer.sandbox.domain.position import CLOSED
from stock_analyzer.sandbox.exp005.diagnostics._shared import symbol_sessions
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import load_diagnostics_context
from stock_analyzer.sandbox.exp005.domain.execution import BUY, SELL
from stock_analyzer.sandbox.exp005.domain.units import price_units_to_float
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import sha256_of_file

REPLAY_ID = "exp005_real_variant_b_2024_11_2025_10"
RUN_ROOT = PROJECT_ROOT / "artifacts" / "sandbox" / "exp005" / "real_runs" / REPLAY_ID
MANIFEST_PATH = RUN_ROOT / "experiment_manifest.json"
DB_PATH = RUN_ROOT / "replay.db"
FEATURE_SNAPSHOT_DIR = "artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z"

PRE_ENTRY_SESSIONS = 5
POST_ENTRY_SESSIONS = 42
TOUCH_THRESHOLDS_PCT = (0.05, 0.10, 0.15, 0.20, 0.25)
HYPOTHETICAL_BASKET_TOTAL = 100_000.0

DAILY_CSV = RUN_ROOT / "variant_b_price_path_daily.csv"
SUMMARY_CSV = RUN_ROOT / "variant_b_price_path_summary.csv"
INDEX_CSV = RUN_ROOT / "variant_b_price_path_equal_weight_index.csv"
METADATA_JSON = RUN_ROOT / "variant_b_price_path_metadata.json"


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_execution(executions, side: str):
    return next((e for e in executions if e.side == side), None)


def main() -> None:
    if not DB_PATH.exists():
        print(f"[ERROR] {DB_PATH} does not exist -- run scripts/run_exp005_real_variant_b.py first.")
        raise SystemExit(1)

    # Read-only by construction -- SQLite immutable/read-only URI, not merely a
    # promise never to call an insert/update method.
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    context = load_diagnostics_context(conn, MANIFEST_PATH, FEATURE_SNAPSHOT_DIR, REPLAY_ID)

    closed_positions = [p for p in context.sandbox_repo.list_all_positions() if p.status == CLOSED]
    print(f"[study] closed positions: {len(closed_positions)}")

    daily_rows: list[dict] = []
    summary_rows: list[dict] = []
    complete_count = 0
    censored_count = 0

    for position in closed_positions:
        executions = context.portfolio_repo.list_executions_for_position(position.position_id)
        buy_execution = _find_execution(executions, BUY)
        sell_execution = _find_execution(executions, SELL)
        if buy_execution is None:
            print(f"[WARN] position {position.position_id} has no BUY execution -- skipping.")
            continue

        raw_entry_price = price_units_to_float(buy_execution.raw_market_fill_price_units)
        effective_entry_price = price_units_to_float(buy_execution.effective_fill_price_units)
        raw_exit_price = price_units_to_float(sell_execution.raw_market_fill_price_units) if sell_execution else None
        effective_exit_price = price_units_to_float(sell_execution.effective_fill_price_units) if sell_execution else None

        sessions = symbol_sessions(context.prices_df, position.symbol)
        session_dates = list(sessions.index)
        if position.entry_date not in session_dates:
            print(f"[WARN] position {position.position_id}: entry_date {position.entry_date} has no bar for "
                  f"{position.symbol} -- skipping.")
            continue
        entry_idx = session_dates.index(position.entry_date)

        pre_dates = session_dates[max(0, entry_idx - PRE_ENTRY_SESSIONS):entry_idx]
        post_dates = session_dates[entry_idx + 1: entry_idx + 1 + POST_ENTRY_SESSIONS]
        window_dates = [(-len(pre_dates) + i, d) for i, d in enumerate(pre_dates)]
        window_dates.append((0, position.entry_date))
        window_dates.extend((i + 1, d) for i, d in enumerate(post_dates))

        is_censored = len(post_dates) < POST_ENTRY_SESSIONS
        if is_censored:
            censored_count += 1
        else:
            complete_count += 1

        snapshots = {s.as_of_date: s for s in context.sandbox_repo.get_snapshots_for_position(position.position_id)}

        exit_offset = None
        if position.exit_date is not None and position.exit_date in session_dates:
            exit_offset = session_dates.index(position.exit_date) - entry_idx

        post_exit_returns: dict[int, float | None] = {5: None, 10: None, 20: None}
        peak_before_exit: float | None = None
        peak_after_exit: float | None = None
        session_returns: dict[int, float | None] = {}
        mfe_by_session: dict[int, float | None] = {}
        mae_by_session: dict[int, float | None] = {}
        first_positive_close_session: int | None = None
        first_touch_session: dict[float, int | None] = {t: None for t in TOUCH_THRESHOLDS_PCT}
        peak_return, peak_session, peak_date = None, None, None
        trough_return, trough_session, trough_date = None, None, None
        running_max_high_return, running_min_low_return = None, None

        for offset, d in window_dates:
            row = sessions.loc[d]
            o, h, low, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
            close_return = (c - effective_entry_price) / effective_entry_price
            high_return = (h - effective_entry_price) / effective_entry_price
            low_return = (low - effective_entry_price) / effective_entry_price

            if offset >= 0:
                running_max_high_return = high_return if running_max_high_return is None else max(running_max_high_return, high_return)
                running_min_low_return = low_return if running_min_low_return is None else min(running_min_low_return, low_return)
                if peak_return is None or high_return > peak_return:
                    peak_return, peak_session, peak_date = high_return, offset, d
                if trough_return is None or low_return < trough_return:
                    trough_return, trough_session, trough_date = low_return, offset, d
                if first_positive_close_session is None and close_return > 0:
                    first_positive_close_session = offset
                for t in TOUCH_THRESHOLDS_PCT:
                    if first_touch_session[t] is None and high_return >= t:
                        first_touch_session[t] = offset
                if offset in (1, 5, 10, 20, 42):
                    session_returns[offset] = close_return
                    mfe_by_session[offset] = running_max_high_return
                    mae_by_session[offset] = running_min_low_return
                if exit_offset is not None:
                    if offset <= exit_offset:
                        peak_before_exit = high_return if peak_before_exit is None else max(peak_before_exit, high_return)
                    if offset >= exit_offset:
                        peak_after_exit = high_return if peak_after_exit is None else max(peak_after_exit, high_return)
                    if offset in (exit_offset + 5, exit_offset + 10, exit_offset + 20):
                        k = offset - exit_offset
                        post_exit_returns[k] = close_return

            is_pre_entry = offset < 0
            is_post_exit = exit_offset is not None and offset > exit_offset
            if is_pre_entry:
                recommendation = "PRE_ENTRY"
            elif is_post_exit:
                recommendation = "CLOSED"
            elif d in snapshots:
                recommendation = snapshots[d].recommendation
            elif d == position.exit_date:
                recommendation = position.exit_reason
            elif d == position.entry_date:
                recommendation = "ENTRY_FILL"
            else:
                recommendation = None

            daily_rows.append({
                "replay_id": REPLAY_ID,
                "position_id": position.position_id,
                "candidate_id": position.candidate_id,
                "symbol": position.symbol,
                "initial_rank": position.initial_rank,
                "initial_model_score": position.initial_model_score,
                "initial_market_regime": position.initial_market_regime,
                "initial_adv_quintile": position.initial_adv_quintile,
                "session_offset": offset,
                "date": d.isoformat(),
                "open": o, "high": h, "low": low, "close": c,
                "raw_entry_price": raw_entry_price,
                "effective_entry_price": effective_entry_price,
                "target_price": position.target_price,
                "recommendation": recommendation,
                "actual_exit_date": position.exit_date.isoformat() if position.exit_date else None,
                "actual_exit_reason": position.exit_reason,
                "raw_exit_price": raw_exit_price,
                "effective_exit_price": effective_exit_price,
                "is_before_program_exit": (exit_offset is None or offset < exit_offset),
                "is_after_program_exit": (exit_offset is not None and offset > exit_offset),
                "close_return_vs_entry": close_return,
                "intraday_high_return_vs_entry": high_return,
                "intraday_low_return_vs_entry": low_return,
            })

        realized_return_at_exit = (
            (effective_exit_price - effective_entry_price) / effective_entry_price if effective_exit_price is not None else None
        )
        peak_to_exit_giveback = (peak_return - realized_return_at_exit) if (peak_return is not None and realized_return_at_exit is not None) else None
        censoring_reason = "INSUFFICIENT_FROZEN_DATA" if is_censored else None

        summary_rows.append({
            "replay_id": REPLAY_ID,
            "position_id": position.position_id,
            "candidate_id": position.candidate_id,
            "symbol": position.symbol,
            "initial_rank": position.initial_rank,
            "initial_model_score": position.initial_model_score,
            "entry_date": position.entry_date.isoformat(),
            "effective_entry_price": effective_entry_price,
            "target_price": position.target_price,
            "planned_time_exit_date": position.planned_time_exit_date.isoformat() if position.planned_time_exit_date else None,
            "return_session_1": session_returns.get(1),
            "return_session_5": session_returns.get(5),
            "return_session_10": session_returns.get(10),
            "return_session_20": session_returns.get(20),
            "return_session_42": session_returns.get(42),
            "mfe_session_1": mfe_by_session.get(1), "mae_session_1": mae_by_session.get(1),
            "mfe_session_5": mfe_by_session.get(5), "mae_session_5": mae_by_session.get(5),
            "mfe_session_10": mfe_by_session.get(10), "mae_session_10": mae_by_session.get(10),
            "mfe_session_20": mfe_by_session.get(20), "mae_session_20": mae_by_session.get(20),
            "mfe_session_42": mfe_by_session.get(42), "mae_session_42": mae_by_session.get(42),
            "first_positive_close_session": first_positive_close_session,
            "first_touch_5pct_session": first_touch_session[0.05],
            "first_touch_10pct_session": first_touch_session[0.10],
            "first_touch_15pct_session": first_touch_session[0.15],
            "first_touch_20pct_session": first_touch_session[0.20],
            "first_touch_25pct_session": first_touch_session[0.25],
            "peak_session": peak_session, "peak_date": peak_date.isoformat() if peak_date else None, "peak_return": peak_return,
            "trough_session": trough_session, "trough_date": trough_date.isoformat() if trough_date else None, "trough_return": trough_return,
            "program_exit_session": exit_offset,
            "program_exit_date": position.exit_date.isoformat() if position.exit_date else None,
            "program_exit_reason": position.exit_reason,
            "program_exit_return": realized_return_at_exit,
            "return_5_sessions_after_exit": post_exit_returns.get(5),
            "return_10_sessions_after_exit": post_exit_returns.get(10),
            "return_20_sessions_after_exit": post_exit_returns.get(20),
            "peak_before_exit": peak_before_exit,
            "peak_after_exit": peak_after_exit,
            "peak_to_exit_giveback": peak_to_exit_giveback,
            "window_sessions_observed_post_entry": len(post_dates),
            "is_censored": is_censored,
            "censoring_reason": censoring_reason,
        })

    conn.close()

    # --- Equal-weight normalized cohort index (Section per user request) --
    # ANALYTICAL ONLY. Each position's own value is indexed to 100 at its own entry
    # date (using the ACTUAL effective entry price as the anchor, applied across the
    # whole window including pre-entry sessions). This is a cross-sectional average
    # of 108 independently-dated positions, aligned by SESSION OFFSET FROM ENTRY, not
    # by calendar date -- it is explicitly NOT a capital-constrained, actually
    # tradeable portfolio (positions entered on different real calendar dates could
    # never simultaneously hold 1/108th of a real $100,000 the way this index implies).
    by_offset: dict[int, list[float]] = {}
    for row in daily_rows:
        normalized = 100.0 * row["close"] / row["effective_entry_price"]
        by_offset.setdefault(row["session_offset"], []).append(normalized)

    index_rows = []
    for offset in sorted(by_offset):
        values = by_offset[offset]
        mean_v = statistics.fmean(values)
        median_v = statistics.median(values)
        index_rows.append({
            "session_offset": offset,
            "position_count": len(values),
            "mean_normalized_value": mean_v,
            "median_normalized_value": median_v,
            "hypothetical_100k_equal_weight_basket_value": mean_v * (HYPOTHETICAL_BASKET_TOTAL / 100.0),
        })

    # --- Write outputs ---
    with open(DAILY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(daily_rows[0].keys()))
        writer.writeheader()
        writer.writerows(daily_rows)

    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    with open(INDEX_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(index_rows[0].keys()))
        writer.writeheader()
        writer.writerows(index_rows)

    # Checksum of key metrics, independent of file encoding, for a reviewer to
    # cross-check without re-deriving the whole pipeline.
    checksum_payload = {
        "position_count": len(summary_rows),
        "complete_count": complete_count,
        "censored_count": censored_count,
        "sum_return_session_42": sum(r["return_session_42"] for r in summary_rows if r["return_session_42"] is not None),
        "sum_peak_return": sum(r["peak_return"] for r in summary_rows if r["peak_return"] is not None),
        "sum_program_exit_return": sum(r["program_exit_return"] for r in summary_rows if r["program_exit_return"] is not None),
        "daily_row_count": len(daily_rows),
    }
    checksum_str = json.dumps(checksum_payload, sort_keys=True)
    metrics_checksum = hashlib.sha256(checksum_str.encode("utf-8")).hexdigest()

    metadata = {
        "replay_id": REPLAY_ID,
        "generated_from": {
            "replay_db_path": str(DB_PATH),
            "replay_db_sha256": _sha256_of_file(DB_PATH),
            "manifest_path": str(MANIFEST_PATH),
            "manifest_artifact_hash": context.manifest_artifact_hash,
            "prices_artifact_ohlc_hash": context.manifest.ohlc_hash,
            "configuration_hash": context.configuration_hash,
        },
        "window_definition": {
            "pre_entry_sessions": PRE_ENTRY_SESSIONS,
            "post_entry_sessions": POST_ENTRY_SESSIONS,
            "session_basis": "each symbol's own observed trading sessions in the frozen prices artifact "
                              "(_shared.symbol_sessions), not a generic business-day calendar",
            "note": "this window intentionally extends past the experiment's own outcome_data_end_date "
                     "(2025-10-20) -- pure historical observation of already-frozen price data, not a new "
                     "decision-time replay.",
        },
        "position_counts": {
            "total_closed_positions_analyzed": len(summary_rows),
            "complete_42_session_window": complete_count,
            "censored_insufficient_frozen_data": censored_count,
        },
        "no_new_exit_policy_applied": True,
        "no_capital_constrained_portfolio_simulated": True,
        "variant_d_run": False,
        "equal_weight_index_is_analytical_not_tradeable": True,
        "outputs": {
            "daily_csv": {"path": str(DAILY_CSV), "sha256": None, "row_count": len(daily_rows)},
            "summary_csv": {"path": str(SUMMARY_CSV), "sha256": None, "row_count": len(summary_rows)},
            "equal_weight_index_csv": {"path": str(INDEX_CSV), "sha256": None, "row_count": len(index_rows)},
        },
        "metrics_checksum_sha256": metrics_checksum,
        "metrics_checksum_payload": checksum_payload,
    }
    # Fill in output file hashes now that they're written.
    metadata["outputs"]["daily_csv"]["sha256"] = _sha256_of_file(DAILY_CSV)
    metadata["outputs"]["summary_csv"]["sha256"] = _sha256_of_file(SUMMARY_CSV)
    metadata["outputs"]["equal_weight_index_csv"]["sha256"] = _sha256_of_file(INDEX_CSV)

    METADATA_JSON.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"[study] positions analyzed: {len(summary_rows)} (complete={complete_count}, censored={censored_count})")
    print(f"[study] daily rows: {len(daily_rows)}")
    print(f"[study] wrote {DAILY_CSV}")
    print(f"[study] wrote {SUMMARY_CSV}")
    print(f"[study] wrote {INDEX_CSV}")
    print(f"[study] wrote {METADATA_JSON}")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
