"""Backtest experiment matrix.

Runs the existing run_backtest() engine under controlled variations, sharing one
in-memory price cache so each variant sees identical data and only ONE knob changes
at a time. Pure diagnostic scaffolding -- does not modify the engine on disk.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

import backtest as bt
from universes import UNIVERSES

START = "2025-06-30"
END = "2026-06-30"
REBALANCE = 7
MAX_POSITIONS = 10
LOOKBACK = bt.LOOKBACK_BUFFER_DAYS

ALL_SYMBOLS = sorted({s for u in UNIVERSES.values() for s in u})
MEGACAP = UNIVERSES["sp500"][:]  # SPY-like mega-cap quality names

# ---------------------------------------------------------------- price cache
_CACHE: dict[tuple, pd.DataFrame] = {}
_ADJUST = {"on": False}
_orig_normalize = bt._normalize_download_frame

_start_ts = pd.Timestamp(START).normalize()
_end_ts = pd.Timestamp(END).normalize()
_lookback_start = _start_ts - pd.Timedelta(days=LOOKBACK)


def _raw_fetch(symbol: str, adjust: bool) -> pd.DataFrame:
    raw = yf.download(
        symbol,
        start=_lookback_start.to_pydatetime(),
        end=(_end_ts + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d",
        auto_adjust=adjust,
        progress=False,
        threads=False,
        timeout=20,
    )
    return _orig_normalize(raw)


def cached_fetch(symbol: str, start_date, end_date) -> pd.DataFrame:
    key = (symbol, _ADJUST["on"])
    if key not in _CACHE:
        _CACHE[key] = _raw_fetch(symbol, _ADJUST["on"])
    return _CACHE[key].copy()


bt._fetch_history = cached_fetch


def prefetch(symbols: list[str], adjust: bool) -> None:
    _ADJUST["on"] = adjust
    todo = [s for s in symbols + ["SPY"] if (s, adjust) not in _CACHE]
    label = "adjusted" if adjust else "raw"
    print(f"  prefetching {len(todo)} symbols ({label})...", flush=True)
    ok = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_raw_fetch, s, adjust): s for s in todo}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                _CACHE[(s, adjust)] = fut.result()
                ok += 1
            except Exception:  # noqa: BLE001 - tolerate yfinance failures
                pass
    print(f"    cached {ok}/{len(todo)}", flush=True)


# ---------------------------------------------------------------- knob control
_ORIG_STOP = bt.LOSS_CUT_PCT
_ORIG_SHOULD_EXIT = bt.should_exit


def _reset_knobs() -> None:
    bt.LOSS_CUT_PCT = _ORIG_STOP
    bt.should_exit = _ORIG_SHOULD_EXIT


def run_exp(name, symbols, *, adjust=False, stop=None, disable_exits=False, rebalance=REBALANCE):
    _reset_knobs()
    _ADJUST["on"] = adjust
    if stop is not None:
        bt.LOSS_CUT_PCT = stop
    if disable_exits:
        bt.should_exit = lambda position, current_data: False
    try:
        result = bt.run_backtest(
            symbols,
            START,
            END,
            rebalance_days=rebalance,
            initial_capital=10_000.0,
            max_positions=MAX_POSITIONS,
        )
        m = result["metrics"]
        return {
            "name": name,
            "ret": m["total_return"],
            "bench": m["benchmark_total_return"],
            "excess": m["excess_return"],
            "sharpe": m["sharpe"],
            "mdd": m["max_drawdown"],
            "vol": m["volatility"],
        }
    finally:
        _reset_knobs()


def main() -> None:
    prefetch(ALL_SYMBOLS, adjust=False)

    rows = []
    print("\nrunning E0 baseline...", flush=True)
    rows.append(run_exp("E0 baseline", ALL_SYMBOLS))
    print("running E1 stop=15%...", flush=True)
    rows.append(run_exp("E1 stop 15%", ALL_SYMBOLS, stop=0.15))
    print("running E2 exits OFF...", flush=True)
    rows.append(run_exp("E2 exits OFF", ALL_SYMBOLS, disable_exits=True))
    print("running E4 mega-cap...", flush=True)
    rows.append(run_exp("E4 mega-cap", MEGACAP))
    print("running E5 mega-cap buy&hold...", flush=True)
    rows.append(run_exp("E5 megacap B&H", MEGACAP, disable_exits=True, rebalance=10**6))
    print("running E6 full universe buy&hold...", flush=True)
    rows.append(run_exp("E6 full B&H", ALL_SYMBOLS, disable_exits=True, rebalance=10**6))

    prefetch(ALL_SYMBOLS, adjust=True)
    print("running E3 dividend-adjusted...", flush=True)
    rows.append(run_exp("E3 div-adjusted", ALL_SYMBOLS, adjust=True))

    print("\n" + "=" * 78)
    print(f"{'experiment':<20}{'return':>9}{'excess':>9}{'sharpe':>8}{'maxDD':>9}{'vol':>9}")
    print("-" * 78)
    for r in rows:
        print(
            f"{r['name']:<20}"
            f"{r['ret']*100:>8.2f}%"
            f"{r['excess']*100:>8.2f}%"
            f"{r['sharpe']:>8.2f}"
            f"{r['mdd']*100:>8.2f}%"
            f"{r['vol']:>9.4f}"
        )
    print("=" * 78)
    print(f"(benchmark SPY return over window: {rows[0]['bench']*100:.2f}%)")


if __name__ == "__main__":
    main()
