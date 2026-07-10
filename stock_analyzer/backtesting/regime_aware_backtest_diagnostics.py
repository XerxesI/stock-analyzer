"""Diagnostic follow-up to Cycle #3's baseline portfolio backtest: quantifies
transaction cost drag, regime-level P&L contribution, and identifies the
maximum-drawdown period (to check whether it's linked to calendar-time
clustering of same-day entries).

DIAGNOSTIC ONLY - does not change the frozen architecture or re-tune anything.
Reuses the already-saved regime_aware_backtest_candidates.csv (no re-fetch,
no network needed) and re-runs the same (deterministic) portfolio simulation.

Usage:
    python -m stock_analyzer.backtesting.regime_aware_backtest_diagnostics
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_analyzer.backtesting.regime_aware_backtest import (
    INITIAL_CAPITAL,
    TRANSACTION_COST_BPS,
    simulate_portfolio,
)

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_CANDIDATES_PATH = _ARTIFACTS_REPORTS / "regime_aware_backtest_candidates.csv"


def main() -> None:
    print(f"loading saved candidates from {_CANDIDATES_PATH} ...", flush=True)
    candidates = pd.read_csv(_CANDIDATES_PATH, parse_dates=["entry_date", "exit_date"])
    print(f"  {len(candidates)} candidates loaded", flush=True)

    equity_curve, trades = simulate_portfolio(candidates)
    cost_frac = TRANSACTION_COST_BPS / 10_000.0

    print("\n" + "=" * 78)
    print("1. TRANSACTION COST DRAG")
    print("=" * 78)
    total_cost = (trades["position_value"] * cost_frac * 2).sum()  # entry + exit
    gross_pnl = (trades["position_value"] * trades["realized_return"]).sum()
    # NOTE: trades["net_pnl"] only reflects the EXIT cost (entry cost was already
    # deducted from equity separately, at entry time, in simulate_portfolio) - so
    # trades["net_pnl"].sum() alone understates total costs. The figure that
    # actually reconciles with the equity curve's total change is gross_pnl minus
    # BOTH entry and exit costs:
    net_pnl = gross_pnl - total_cost
    print(f"  Gross P&L (before costs):  ${gross_pnl:,.0f}")
    print(f"  Total transaction costs:   ${total_cost:,.0f}  (entry + exit, both legs)")
    print(f"  Net P&L (after costs):     ${net_pnl:,.0f}  (reconciles with equity curve's total change)")
    print(f"  Cost as % of gross P&L:    {total_cost / abs(gross_pnl) * 100 if gross_pnl != 0 else float('nan'):.1f}%")
    print(f"  Cost as % of initial capital: {total_cost / INITIAL_CAPITAL * 100:.1f}%")

    print("\n" + "=" * 78)
    print("2. REGIME-LEVEL P&L CONTRIBUTION")
    print("=" * 78)
    # trades["net_pnl"] only reflects the exit-leg cost - add the entry-leg cost
    # back in so this breakdown is fully cost-adjusted and comparable to section 1.
    trades["adjusted_net_pnl"] = trades["net_pnl"] - trades["position_value"] * cost_frac
    by_regime = trades.groupby("regime").agg(
        trade_count=("adjusted_net_pnl", "size"),
        total_net_pnl=("adjusted_net_pnl", "sum"),
        mean_net_pnl=("adjusted_net_pnl", "mean"),
        win_rate=("adjusted_net_pnl", lambda x: (x > 0).mean()),
    )
    print(by_regime.to_string())

    print("\n" + "=" * 78)
    print("3. MAXIMUM DRAWDOWN PERIOD")
    print("=" * 78)
    equity_curve = equity_curve.sort_values("date").reset_index(drop=True)
    running_max = equity_curve["equity"].cummax()
    drawdown = equity_curve["equity"] / running_max - 1
    trough_idx = drawdown.idxmin()
    trough_date = equity_curve.loc[trough_idx, "date"]
    trough_equity = equity_curve.loc[trough_idx, "equity"]

    peak_idx = equity_curve.loc[:trough_idx, "equity"].idxmax()
    peak_date = equity_curve.loc[peak_idx, "date"]
    peak_equity = equity_curve.loc[peak_idx, "equity"]

    print(f"  Peak:   {peak_date.date()}  equity=${peak_equity:,.0f}")
    print(f"  Trough: {trough_date.date()}  equity=${trough_equity:,.0f}")
    print(f"  Drawdown: {drawdown.min():.2%}  (duration: {(trough_date - peak_date).days} calendar days)")

    trades_in_drawdown = trades[(trades["entry_date"] >= peak_date) & (trades["entry_date"] <= trough_date)]
    print(f"\n  Trades ENTERED during this drawdown window: {len(trades_in_drawdown)}")
    print(f"  Their combined net P&L (cost-adjusted): ${trades_in_drawdown['adjusted_net_pnl'].sum():,.0f}")
    print(f"  Their win rate: {(trades_in_drawdown['adjusted_net_pnl'] > 0).mean():.2%}")
    print(f"  Regime mix during drawdown: {trades_in_drawdown['regime'].value_counts(normalize=True).to_dict()}")

    entries_per_day_in_window = trades_in_drawdown.groupby("entry_date").size()
    if len(entries_per_day_in_window):
        print(f"  Max same-day entries within drawdown window: {entries_per_day_in_window.max()}")
        clustered_days = entries_per_day_in_window[entries_per_day_in_window >= 10]
        if len(clustered_days):
            print(f"  Days with >=10 simultaneous entries during drawdown:")
            for d, cnt in clustered_days.items():
                print(f"    {d.date()}: {cnt} entries")

    print("\n" + "=" * 78)
    print("Interpretation guide:")
    print("  - High cost-as-%-of-gross-P&L: trade frequency/position sizing may need")
    print("    reconsideration (fewer, larger, more selective trades) - this is a")
    print("    system-design question, not a signal-quality question.")
    print("  - If drawdown trades cluster heavily on a few same-entry-day dates with")
    print("    low win rate: confirms calendar-time clustering risk - position limits")
    print("    alone (concurrent COUNT cap) don't protect against correlated same-day")
    print("    risk the way a same-day ENTRY cap or sector cap would.")
    print("=" * 78)


if __name__ == "__main__":
    main()