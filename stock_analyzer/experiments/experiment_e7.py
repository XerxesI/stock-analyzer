"""E7: with the auto_adjust fix already in backtest.py, compare universes.

Isolates SELECTION: same engine, same window, only the candidate universe changes.
"""

from __future__ import annotations

from stock_analyzer.backtesting.backtest import run_backtest
from stock_analyzer.data.universes import UNIVERSES
START, END = "2025-06-30", "2026-06-30"

FULL = sorted({s for u in UNIVERSES.values() for s in u})
SP500 = UNIVERSES["sp500"][:]
NASDAQ = UNIVERSES["nasdaq"][:]
CORE = sorted(set(SP500) | set(NASDAQ))  # mega-cap core, deduplicated

VARIANTS = {
    "full universe": FULL,
    "sp500 megacap": SP500,
    "nasdaq megacap": NASDAQ,
    "core (sp500+nasdaq)": CORE,
}

rows = []
for name, syms in VARIANTS.items():
    print(f"running {name} ({len(syms)} symbols)...", flush=True)
    r = run_backtest(syms, START, END, rebalance_days=7, max_positions=10)
    m = r["metrics"]
    rows.append((name, m["total_return"], m["excess_return"], m["sharpe"], m["max_drawdown"], m["benchmark_total_return"]))

print("\n" + "=" * 74)
print(f"{'universe':<22}{'return':>9}{'excess':>9}{'sharpe':>8}{'maxDD':>9}")
print("-" * 74)
for name, ret, exc, sh, mdd, _ in rows:
    print(f"{name:<22}{ret*100:>8.2f}%{exc*100:>8.2f}%{sh:>8.2f}{mdd*100:>8.2f}%")
print("=" * 74)
print(f"(SPY benchmark: {rows[0][5]*100:.2f}%)")
