"""EXP-005 -- full Stage 11-14 diagnostics report for a completed real replay.

Loads a completed replay through the ONE mediated boundary
(`exp005.diagnostics.diagnostics.load_diagnostics_context`), computes the financial-
feasibility report (Section 10) and the full decision-quality summary (Section 25),
applies the pre-registered feasibility criteria UNCHANGED (no `feasibility_criteria`
argument exists to override them -- Stage 11-15 third/fourth closure), and applies
the early-stop rule: if any of the four criteria individually determinable from
Variant B ALONE (positive net P&L, drawdown within threshold, profit factor within
threshold, largest-winner concentration within threshold) is a CONFIRMED failure,
the overall verdict is already False and the 50 Variant D control seeds would not be
able to change it -- so this script reports that explicitly rather than silently
implying D should run next.

Writes both a machine-readable JSON dump and a human-readable Markdown report next to
the replay database.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_analyzer.sandbox.exp005.diagnostics._shared import full_market_calendar
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import load_diagnostics_context
from stock_analyzer.sandbox.exp005.diagnostics.financial_performance import compute_feasibility_verdict, compute_financial_performance
from stock_analyzer.sandbox.exp005.diagnostics.report_generator import compute_run_summary
from stock_analyzer.sandbox.infrastructure.schema import connect

REPLAY_ID = "exp005_real_variant_b_2024_11_2025_10"
RUN_ROOT = PROJECT_ROOT / "artifacts" / "sandbox" / "exp005" / "real_runs" / REPLAY_ID
MANIFEST_PATH = RUN_ROOT / "experiment_manifest.json"
DB_PATH = RUN_ROOT / "replay.db"
FEATURE_SNAPSHOT_DIR = "artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z"
JSON_REPORT_PATH = RUN_ROOT / "variant_b_diagnostics_report.json"
MD_REPORT_PATH = RUN_ROOT / "variant_b_diagnostics_report.md"


def _jsonable(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, float) and obj != obj:  # NaN
        return None
    return obj


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.2f}%"


def _num(x: float | None, digits: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def main() -> None:
    if not DB_PATH.exists():
        print(f"[ERROR] {DB_PATH} does not exist -- run scripts/run_exp005_real_variant_b.py first.")
        raise SystemExit(1)

    conn = connect(str(DB_PATH))
    context = load_diagnostics_context(conn, MANIFEST_PATH, FEATURE_SNAPSHOT_DIR, REPLAY_ID)
    calendar = full_market_calendar(context.prices_df)

    financial_report = compute_financial_performance(context)
    summary = compute_run_summary(context, calendar)
    # No Variant D reports yet -- beats_control_percentile is correctly undetermined
    # (None); the other four criteria are fully determinable from Variant B alone.
    verdict = compute_feasibility_verdict(financial_report, [])
    conn.close()

    non_percentile_criteria = [c for c in verdict.criteria if c.name != "beats_control_percentile"]
    confirmed_failures = [c for c in non_percentile_criteria if c.passed is False]
    early_stop = len(confirmed_failures) > 0

    dump = {
        "replay_id": REPLAY_ID,
        "manifest": {
            "model_version": context.manifest.model_version,
            "feature_snapshot_id": context.manifest.feature_snapshot_id,
            "signal_start_date": context.manifest.signal_start_date.isoformat(),
            "signal_end_date": context.manifest.signal_end_date.isoformat(),
            "outcome_data_end_date": context.manifest.outcome_data_end_date.isoformat(),
            "calendar_session_count": context.manifest.calendar_session_count,
        },
        "variant_id": context.variant_id,
        "control_seed": context.control_seed,
        "financial_performance": _jsonable(financial_report),
        "decision_quality_summary": _jsonable(summary),
        "feasibility_verdict": _jsonable(verdict),
        "early_stop": early_stop,
        "early_stop_reason": (
            [f"{c.name}: value={c.value!r} threshold={c.threshold!r} comparison={c.comparison!r}" for c in confirmed_failures]
            if early_stop else None
        ),
    }
    JSON_REPORT_PATH.write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")

    fr = financial_report
    lines: list[str] = []
    lines.append(f"# EXP-005 Variant B -- Real Run Diagnostics Report")
    lines.append("")
    lines.append(f"replay_id: `{REPLAY_ID}`  ")
    lines.append(f"variant_id: `{context.variant_id}`  control_seed: `{context.control_seed}`  ")
    lines.append(
        f"period: `{context.manifest.signal_start_date} .. {context.manifest.signal_end_date}` "
        f"(outcome through `{context.manifest.outcome_data_end_date}`, "
        f"{context.manifest.calendar_session_count} sessions)  "
    )
    lines.append(f"model_version: `{context.manifest.model_version}`  ")
    lines.append("")

    lines.append("## 1. Net P&L and net return")
    lines.append(f"- starting_equity: {fr.starting_equity:.2f}")
    lines.append(f"- ending_equity: {fr.ending_equity:.2f}")
    lines.append(f"- net_pnl: {fr.net_pnl:.2f}")
    lines.append(f"- net_return_pct: {_pct(fr.net_return_pct)}")
    lines.append("")

    lines.append("## 2. Max drawdown")
    dd = fr.drawdown
    lines.append(f"- max_drawdown_pct: {_pct(dd.max_drawdown_pct)}")
    lines.append(f"- peak_date/equity: {dd.peak_date} / {dd.peak_equity}")
    lines.append(f"- trough_date/equity: {dd.trough_date} / {dd.trough_equity}")
    lines.append("")

    lines.append("## 3. Profit factor")
    lines.append(f"- profit_factor: {_num(fr.profit_factor, 4)}")
    lines.append(f"- win_count: {fr.win_count}   loss_count: {fr.loss_count}   closed_trade_count: {fr.closed_trade_count}")
    lines.append("")

    lines.append("## 4. Closed/open position contribution")
    lines.append(f"- closed_trade_count: {fr.closed_trade_count}")
    lines.append(
        f"- largest_open_position: "
        f"{fr.largest_open_position.symbol if fr.largest_open_position else 'none (undetermined -- no positive open winner)'}"
    )
    lines.append(f"- largest_open_position_pct_of_net_pnl: {_pct(fr.largest_open_position_pct_of_net_pnl)}")
    lines.append(f"- open_position_market_value_pct_of_ending_equity: {_pct(fr.open_position_market_value_pct_of_ending_equity)}")
    lines.append("")

    lines.append("## 5. Largest-winner concentration")
    lines.append(
        f"- largest_closed_winning_trade: "
        f"{fr.largest_closed_winning_trade.symbol if fr.largest_closed_winning_trade else 'none'}"
    )
    lines.append(f"- largest_closed_winning_trade_pct_of_net_pnl: {_pct(fr.largest_closed_winning_trade_pct_of_net_pnl)}")
    lines.append(f"- net_pnl_minus_largest_winning_trade: {_num(fr.net_pnl_minus_largest_winning_trade, 2)}")
    lines.append(f"- remains_positive_after_removing_largest_winner: {fr.remains_positive_after_removing_largest_winner}")
    lines.append("")

    lines.append("## 6. Quarterly returns")
    for q in fr.quarterly_returns:
        lines.append(f"- {q.year}Q{q.quarter} [{q.start_date}..{q.end_date}]: {_pct(q.return_pct)} (equity {q.start_equity:.2f} -> {q.end_equity:.2f})")
    lines.append("")

    lines.append("## 7. BUY quality")
    b = summary.buy
    lines.append(f"- filled_count: {b.filled_count}   expired_count: {b.expired_count}   fill_rate: {_pct(b.fill_rate)}")
    lines.append(f"- entry_session_ambiguity_count (FILLED_AT_CEILING): {b.entry_session_ambiguity_count}")
    lines.append(f"- mean_entry_gap_pct: {_pct(b.mean_entry_gap_pct)}   mean_slippage_cost: {_num(b.mean_slippage_cost, 4)}")
    lines.append(f"- target_hit_rate: {_pct(b.target_hit_rate)}")
    lines.append("")

    lines.append("## 8. HOLD quality")
    h = summary.hold
    lines.append(f"- hold_decision_count: {h.hold_decision_count}")
    lines.append(f"- profitable_continuation_rate: {_pct(h.profitable_continuation_rate)}   adverse_continuation_rate: {_pct(h.adverse_continuation_rate)}")
    lines.append(f"- target_eventually_reached_rate: {_pct(h.target_eventually_reached_rate)}   time_exit_eventually_reached_rate: {_pct(h.time_exit_eventually_reached_rate)}")
    lines.append(f"- unresolved_rate: {_pct(h.unresolved_rate)}")
    lines.append("")

    lines.append("## 9. SELL quality")
    s = summary.sell
    lines.append(f"- closed_position_count: {s.closed_position_count}   target_exit_count: {s.target_exit_count}   time_exit_count: {s.time_exit_count}")
    lines.append(f"- mean_realized_return_pct: {_pct(s.mean_realized_return_pct)}")
    lines.append(f"- target_exit_mean_realized_return_pct: {_pct(s.target_exit_mean_realized_return_pct)}")
    lines.append(f"- time_exit_mean_realized_return_pct: {_pct(s.time_exit_mean_realized_return_pct)}")
    lines.append("")

    lines.append("## 10. MFE/MAE")
    lines.append(f"- mean_mfe_captured_pct (SELL): {_pct(s.mean_mfe_captured_pct)}")
    lines.append(f"- mean_peak_to_exit_giveback_pct (SELL): {_pct(s.mean_peak_to_exit_giveback_pct)}")
    lines.append(f"- mean_exit_efficiency (SELL): {_num(s.mean_exit_efficiency, 4)}")
    for horizon in sorted(b.horizon_mean_mfe_pct):
        lines.append(
            f"- BUY horizon {horizon}: mean_mfe={_pct(b.horizon_mean_mfe_pct[horizon])} "
            f"mean_mae={_pct(b.horizon_mean_mae_pct[horizon])} "
            f"complete={b.horizon_complete_count[horizon]}"
        )
    lines.append("")

    lines.append("## 11. Capacity and opportunity-cost diagnostics")
    c = summary.capacity
    lines.append(f"- no_capacity_count: {c.no_capacity_count}   hypothetical_fill_rate: {_pct(c.hypothetical_fill_rate)}")
    lines.append(f"- mean_open_position_count: {_num(c.mean_open_position_count, 2)}   mean_reserved_order_count: {_num(c.mean_reserved_order_count, 2)}")
    lines.append(f"- idle_cash_day_count: {c.idle_cash_day_count} / total_equity_snapshot_days: {c.total_equity_snapshot_days}")
    lines.append(f"- accepted_mean_realized_return_pct: {_pct(c.accepted_mean_realized_return_pct)}")
    lines.append(f"- rejected_mean_horizon20_return_pct: {_pct(c.rejected_mean_horizon20_return_pct)}")
    lines.append("")

    lines.append("## 12. Censored-observation counts")
    for label, sec in (("BUY", b), ("HOLD", h), ("SELL", s), ("CAPACITY", c)):
        for horizon in sorted(sec.horizon_complete_count):
            eoe = sec.horizon_censored_end_of_experiment_count[horizon]
            mmd = sec.horizon_censored_missing_market_data_count[horizon]
            complete = sec.horizon_complete_count[horizon]
            if eoe or mmd:
                lines.append(f"- {label} horizon {horizon}: complete={complete} censored_end_of_experiment={eoe} censored_missing_market_data={mmd}")
    lines.append("")

    lines.append("## 13. Feasibility verdict (pre-registered criteria, unchanged)")
    for crit in verdict.criteria:
        lines.append(f"- {crit.name}: value={crit.value!r} threshold={crit.threshold!r} comparison={crit.comparison!r} -> passed={crit.passed}")
    lines.append(f"- **overall verdict: {verdict.verdict}** (variant_d_seed_count={verdict.variant_d_seed_count})")
    lines.append("")

    lines.append("## 14. Early-stop determination")
    if early_stop:
        lines.append("**EARLY STOP: Variant B failed at least one absolute, already-determinable criterion.**")
        lines.append("The 50 Variant D control seeds cannot change a confirmed False -- they will NOT be run.")
        for c_ in confirmed_failures:
            lines.append(f"- FAILED: {c_.name} (value={c_.value!r}, threshold={c_.threshold!r}, comparison={c_.comparison!r})")
    else:
        lines.append("Variant B passed every absolute, already-determinable criterion.")
        lines.append("The 50 frozen Variant D control seeds may now be run to determine `beats_control_percentile` "
                      "and the final verdict -- pending explicit go-ahead.")
    lines.append("")

    MD_REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[report] wrote {JSON_REPORT_PATH}")
    print(f"[report] wrote {MD_REPORT_PATH}")


if __name__ == "__main__":
    main()
