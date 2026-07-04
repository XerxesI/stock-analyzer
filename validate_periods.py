"""Multi-period validation: is the universe edge persistent or one-window luck?

Same engine (auto_adjust fix already in backtest.py), same settings, across five
1-year windows including the 2021-22 tech crash. Shared in-memory cache per window
so the overlapping universes reuse downloads.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

import backtest as bt
from universes import UNIVERSES

FULL = sorted({s for u in UNIVERSES.values() for s in u})
NASDAQ = UNIVERSES["nasdaq"][:]
CORE = sorted(set(UNIVERSES["sp500"]) | set(UNIVERSES["nasdaq"]))

UNIVERSE_VARIANTS = {"full": FULL, "core": CORE, "nasdaq": NASDAQ}

WINDOWS = [
    ("2021-22 (crash)", "2021-06-30", "2022-06-30"),
    ("2022-23", "2022-06-30", "2023-06-30"),
    ("2023-24", "2023-06-30", "2024-06-30"),
    ("2024-25", "2024-06-30", "2025-06-30"),
    ("2025-26", "2025-06-30", "2026-06-30"),
]

# ---- in-memory cache keyed per (symbol, window) so universes reuse downloads ----
_CACHE: dict[tuple, pd.DataFrame] = {}
_orig_normalize = bt._normalize_download_frame
_ctx = {"win": None, "lb": None, "end": None}


def _raw_fetch(symbol: str) -> pd.DataFrame:
    raw = yf.download(
        symbol,
        start=_ctx["lb"].to_pydatetime(),
        end=(_ctx["end"] + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=20,
    )
    return _orig_normalize(raw)


def cached_fetch(symbol, start_date, end_date):
    key = (symbol, _ctx["win"])
    if key not in _CACHE:
        _CACHE[key] = _raw_fetch(symbol)
    return _CACHE[key].copy()


bt._fetch_history = cached_fetch


def prefetch(win):
    todo = [s for s in FULL + ["SPY"] if (s, win) not in _CACHE]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_raw_fetch, s): s for s in todo}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                _CACHE[(s, win)] = fut.result()
            except Exception:  # noqa: BLE001
                pass


results = {}  # (win, universe) -> (return, excess)
spy_by_win = {}

for win, start, end in WINDOWS:
    _ctx["win"] = win
    _ctx["lb"] = pd.Timestamp(start).normalize() - pd.Timedelta(days=bt.LOOKBACK_BUFFER_DAYS)
    _ctx["end"] = pd.Timestamp(end).normalize()
    print(f"prefetching {win}...", flush=True)
    prefetch(win)
    for uname, syms in UNIVERSE_VARIANTS.items():
        print(f"  running {win} / {uname}...", flush=True)
        try:
            r = bt.run_backtest(syms, start, end, rebalance_days=7, max_positions=10)
            m = r["metrics"]
            results[(win, uname)] = (m["total_return"], m["excess_return"])
            spy_by_win[win] = m["benchmark_total_return"]
        except Exception as exc:  # noqa: BLE001
            results[(win, uname)] = None
            print(f"    FAILED: {exc}", flush=True)

# ---------------------------------------------------------------- report
print("\n" + "=" * 78)
print("TOTAL RETURN by window x universe (SPY benchmark in last column)")
print("-" * 78)
print(f"{'window':<18}{'full':>11}{'core':>11}{'nasdaq':>11}{'SPY':>11}")
for win, _, _ in WINDOWS:
    cells = []
    for u in ("full", "core", "nasdaq"):
        v = results.get((win, u))
        cells.append(f"{v[0]*100:>10.1f}%" if v else f"{'n/a':>11}")
    spy = spy_by_win.get(win)
    spy_s = f"{spy*100:>10.1f}%" if spy is not None else f"{'n/a':>11}"
    print(f"{win:<18}{''.join(cells)}{spy_s}")

print("\n" + "-" * 78)
print("EXCESS vs SPY (positive = beat the index)")
print("-" * 78)
print(f"{'window':<18}{'full':>11}{'core':>11}{'nasdaq':>11}")
beat = {"full": 0, "core": 0, "nasdaq": 0}
for win, _, _ in WINDOWS:
    cells = []
    for u in ("full", "core", "nasdaq"):
        v = results.get((win, u))
        if v:
            cells.append(f"{v[1]*100:>10.1f}%")
            if v[1] > 0:
                beat[u] += 1
        else:
            cells.append(f"{'n/a':>11}")
    print(f"{win:<18}{''.join(cells)}")
print("-" * 78)
print(f"{'beat SPY (of 5)':<18}{beat['full']:>11}{beat['core']:>11}{beat['nasdaq']:>11}")
print("=" * 78)
