"""Walk-forward runner for the hybrid core-satellite strategy.

Evaluates 3 ablation configs across 5 one-year windows (the same windows used to
prove the old strategy had no edge), plus a SPY buy-and-hold benchmark. Data is
fetched once over a wide span and sliced per window.

Usage:
  python run_hybrid.py                 # full walk-forward + ablation
  python run_hybrid.py --selftest      # no-lookahead sanity check only
  python run_hybrid.py --costs         # also print transaction-cost sensitivity
  python run_hybrid.py --core QQQ --core-weight 0.7 --sat-n 10
"""

from __future__ import annotations

import argparse

import pandas as pd

from factors import momentum_12_1, price_asof
from hybrid_backtest import (
    DEFAULT_COST_RATE,
    fetch_frames,
    prepare_spy_indicators,
    run_hybrid_backtest,
    spy_buy_hold,
    _metrics,
)
from universes import LIQUID_LARGECAP

FETCH_START = pd.Timestamp("2020-01-01")
FETCH_END = pd.Timestamp("2026-06-30")
INITIAL = 10_000.0

WINDOWS = [
    ("2021-22 (crash)", "2021-06-30", "2022-06-30"),
    ("2022-23", "2022-06-30", "2023-06-30"),
    ("2023-24", "2023-06-30", "2024-06-30"),
    ("2024-25", "2024-06-30", "2025-06-30"),
    ("2025-26", "2025-06-30", "2026-06-30"),
]

CONFIGS = [
    ("core-only", {"use_satellite": False, "use_overlay": False}),
    ("core+sat", {"use_satellite": True, "use_overlay": False}),
    ("core+sat+overlay", {"use_satellite": True, "use_overlay": True}),
]


def selftest(frames: dict[str, pd.DataFrame]) -> None:
    """Verify momentum/price are invariant to the presence of future rows."""

    print("no-lookahead self-test:")
    asof = pd.Timestamp("2023-06-30")
    checked = 0
    failures = 0
    for symbol, frame in frames.items():
        full_m = momentum_12_1(frame, asof)
        truncated = frame.loc[:asof]
        trunc_m = momentum_12_1(truncated, asof)
        full_p = price_asof(frame, asof)
        trunc_p = price_asof(truncated, asof)
        if full_m is None and trunc_m is None:
            same_m = True
        else:
            same_m = full_m is not None and trunc_m is not None and abs(full_m - trunc_m) < 1e-12
        same_p = abs(full_p - trunc_p) < 1e-9
        if not (same_m and same_p):
            failures += 1
            print(f"  MISMATCH {symbol}: m={full_m} vs {trunc_m}, p={full_p} vs {trunc_p}")
        checked += 1
    verdict = "PASS" if failures == 0 else f"FAIL ({failures} mismatches)"
    print(f"  checked {checked} symbols -> {verdict}")


def run(core_assets: list[str], core_weight: float, sat_n: int, show_costs: bool) -> None:
    universe = sorted(set(LIQUID_LARGECAP) | set(core_assets) | {"SPY"})
    print(f"fetching {len(universe)} symbols ({FETCH_START.date()}..{FETCH_END.date()})...", flush=True)
    frames = fetch_frames(universe, FETCH_START, FETCH_END)
    print(f"  loaded {len(frames)}/{len(universe)} frames", flush=True)
    spy_ind = prepare_spy_indicators(frames)

    # collected per config: list of (window, metrics); and SPY metrics per window
    per_config: dict[str, list[dict]] = {name: [] for name, _ in CONFIGS}
    spy_metrics: list[dict] = []

    print(f"\n{'=' * 92}")
    print(f"WALK-FORWARD  (core={'+'.join(core_assets)} @ {core_weight:.0%}, satellite top-{sat_n} 12-1 momentum,")
    print(f"               monthly rebalance, cost {DEFAULT_COST_RATE:.2%}/side)")
    print(f"{'=' * 92}")
    print(f"{'window':<18}{'config':<20}{'return':>9}{'sharpe':>8}{'maxDD':>9}")
    print("-" * 92)

    for wname, wstart, wend in WINDOWS:
        days = None
        for cname, kwargs in CONFIGS:
            res = run_hybrid_backtest(
                frames, spy_ind, core_assets, wstart, wend,
                core_weight=core_weight, satellite_n=sat_n, initial_capital=INITIAL, **kwargs,
            )
            m = res["metrics"]
            per_config[cname].append({"window": wname, **m})
            print(f"{wname:<18}{cname:<20}{m['total_return']*100:>8.1f}%{m['sharpe']:>8.2f}{m['max_drawdown']*100:>8.1f}%")
            days = res["dates"]
        spy_vals = spy_buy_hold(spy_ind, days, INITIAL)
        sm = _metrics(spy_vals)
        spy_metrics.append({"window": wname, **sm})
        print(f"{wname:<18}{'SPY (benchmark)':<20}{sm['total_return']*100:>8.1f}%{sm['sharpe']:>8.2f}{sm['max_drawdown']*100:>8.1f}%")
        print("-" * 92)

    # ---- honest summary: does each layer earn its keep? ----
    full = per_config["core+sat+overlay"]
    core_only = per_config["core-only"]
    beat_spy_ret = sum(1 for f, s in zip(full, spy_metrics) if f["total_return"] > s["total_return"])
    higher_sharpe_spy = sum(1 for f, s in zip(full, spy_metrics) if f["sharpe"] > s["sharpe"])
    lower_dd_spy = sum(1 for f, s in zip(full, spy_metrics) if f["max_drawdown"] > s["max_drawdown"])  # less negative
    higher_sharpe_core = sum(1 for f, c in zip(full, core_only) if f["sharpe"] > c["sharpe"])

    print("\nSUMMARY (full config = core+sat+overlay), out of 5 windows:")
    print(f"  beat SPY on return    : {beat_spy_ret}/5")
    print(f"  higher Sharpe than SPY: {higher_sharpe_spy}/5")
    print(f"  smaller drawdown SPY  : {lower_dd_spy}/5")
    print(f"  higher Sharpe than core-only (does the satellite/overlay add value?): {higher_sharpe_core}/5")
    print("\nReminder: the honest bar is better risk-adjusted return (Sharpe up, drawdown down),")
    print("NOT beating SPY every year. If the full config doesn't beat core-only on Sharpe,")
    print("the rational choice is core (SPY) + overlay without the satellite.")
    print("Note: LIQUID_LARGECAP is a current-constituent snapshot -> mild survivorship bias.")

    if show_costs:
        print(f"\n{'-' * 60}\nTRANSACTION-COST SENSITIVITY (full config, mean return over 5 windows)\n{'-' * 60}")
        for rate in (0.0, 0.0010, 0.0025):
            rets = []
            for wname, wstart, wend in WINDOWS:
                res = run_hybrid_backtest(
                    frames, spy_ind, core_assets, wstart, wend,
                    core_weight=core_weight, satellite_n=sat_n, cost_rate=rate,
                    use_satellite=True, use_overlay=True, initial_capital=INITIAL,
                )
                rets.append(res["metrics"]["total_return"])
            print(f"  cost {rate:.2%}/side : mean return {sum(rets)/len(rets)*100:+.2f}%")


def main() -> int:
    parser = argparse.ArgumentParser(description="Hybrid core-satellite walk-forward backtest.")
    parser.add_argument("--selftest", action="store_true", help="Run the no-lookahead check and exit.")
    parser.add_argument("--costs", action="store_true", help="Also print transaction-cost sensitivity.")
    parser.add_argument("--core", default="SPY", help="Core asset(s), comma-separated (default SPY).")
    parser.add_argument("--core-weight", type=float, default=0.70, help="Core weight (default 0.70).")
    parser.add_argument("--sat-n", type=int, default=10, help="Satellite size (default 10).")
    args = parser.parse_args()

    core_assets = [c.strip().upper() for c in args.core.split(",") if c.strip()]

    if args.selftest:
        universe = sorted(set(LIQUID_LARGECAP) | set(core_assets) | {"SPY"})
        print(f"fetching {len(universe)} symbols for self-test...", flush=True)
        frames = fetch_frames(universe, FETCH_START, FETCH_END)
        print(f"  loaded {len(frames)}/{len(universe)} frames", flush=True)
        selftest(frames)
        return 0

    run(core_assets, args.core_weight, args.sat_n, args.costs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
