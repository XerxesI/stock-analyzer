"""Out-of-sample check: does the v2 Trade Score (reweighted after IC testing
on ai+nuclear_energy) still show a sane IC on a COMPLETELY DIFFERENT universe?

This is the critical guard against in-sample overfitting: the v2 weights
(trend cut to 10, new RSI component, momentum/support adjusted) were
chosen based on IC evidence from a 30-symbol thematic universe. If that
was just noise in that specific sample, the effect should vanish or
flip sign on LIQUID_LARGECAP (~190 symbols, broad/diversified,
non-thematic). If the same rough direction (less negative / mildly
positive IC) shows up here too, that's real out-of-sample support.

Same walk-forward methodology as swing_rank_ic_test.py, just pointed at
a different universe.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universes import LIQUID_LARGECAP
from stock_analyzer.swing.trade_score import calculate_trade_score

SYMBOLS = LIQUID_LARGECAP

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START = FETCH_START + pd.Timedelta(days=210)
TEST_END = FETCH_END - pd.Timedelta(days=50)
STEP_DAYS = 5

HORIZONS = {"2wk": 10, "6wk": 30}
MIN_HISTORY_BARS = 210

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "swing_v2_oos_liquid_largecap_obs.csv"


def _fetch(symbol: str) -> pd.DataFrame:
    raw = yf.download(
        symbol,
        start=FETCH_START.to_pydatetime(),
        end=(FETCH_END + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=20,
    )
    if raw.empty:
        raise ValueError("empty")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.loc[:, ["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    return calculate_indicators(raw.sort_index())


print(f"fetching {len(SYMBOLS)} symbols (LIQUID_LARGECAP, out-of-sample)...", flush=True)
frames: dict[str, pd.DataFrame] = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(_fetch, s): s for s in SYMBOLS}
    for fut in as_completed(futs):
        s = futs[fut]
        try:
            frames[s] = fut.result()
        except Exception:  # noqa: BLE001
            pass
print(f"  loaded {len(frames)}/{len(SYMBOLS)} price frames", flush=True)


def score_at(frame: pd.DataFrame, t: pd.Timestamp) -> dict | None:
    hist = frame.loc[:t].dropna(subset=["Close"])
    if len(hist) < MIN_HISTORY_BARS:
        return None
    try:
        result = calculate_trade_score(hist)
    except (ValueError, RuntimeError):
        return None
    return {"trade_score": result["trade_score"], "classification": result["classification"]}


def fwd_return(frame: pd.DataFrame, t: pd.Timestamp, h: int) -> float | None:
    pos = frame.index.get_indexer([t], method="ffill")[0]
    if pos < 0:
        return None
    fpos = pos + h
    if fpos >= len(frame):
        return None
    p0 = float(frame["Close"].iloc[pos])
    p1 = float(frame["Close"].iloc[fpos])
    return (p1 / p0 - 1.0) if p0 > 0 else None


rows = []
dates = pd.date_range(TEST_START, TEST_END, freq=f"{STEP_DAYS}D")
print(f"scoring {len(dates)} dates x {len(frames)} symbols (this will take longer than the 30-symbol run)...", flush=True)
for i, t in enumerate(dates):
    for s, frame in frames.items():
        scored = score_at(frame, t)
        if scored is None:
            continue
        rec = {"date": t, "symbol": s, **scored}
        ok = False
        for hname, h in HORIZONS.items():
            fr = fwd_return(frame, t, h)
            rec[hname] = fr
            ok = ok or (fr is not None)
        if ok:
            rows.append(rec)
    if (i + 1) % 10 == 0:
        print(f"  processed {i + 1}/{len(dates)} dates", flush=True)

_ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
df = pd.DataFrame(rows)
df.to_csv(_OBS_PATH, index=False)
print(f"\ntotal observations: {len(df)} (saved to {_OBS_PATH})", flush=True)


def spearman(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 2:
        return float("nan")
    return a.rank().corr(b.rank())


print("\n" + "=" * 70)
print("OUT-OF-SAMPLE CHECK: v2 Trade Score on LIQUID_LARGECAP")
print("Compare these IC values to the in-sample (ai+nuclear_energy) run.")
print("=" * 70)
for hname in HORIZONS:
    sub = df.dropna(subset=[hname])
    ic = spearman(sub["trade_score"], sub[hname]) if len(sub) > 30 else float("nan")
    print(f"  overall IC ({hname}, n={len(sub)}): {ic:+.4f}")

print("\n" + "-" * 70)
print("MEAN FORWARD RETURN by Trade Score quintile (Q1=lowest, Q5=highest)")
print("-" * 70)
for hname in HORIZONS:
    sub = df.dropna(subset=[hname]).copy()
    if len(sub) < 50:
        continue
    try:
        sub["q"] = pd.qcut(sub["trade_score"], 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
    except ValueError:
        continue
    means = sub.groupby("q", observed=True)[hname].mean() * 100
    spread = means.iloc[-1] - means.iloc[0]
    cells = "  ".join(f"Q{int(q)}={means[q]:+.1f}%" for q in means.index)
    print(f"  {hname}: {cells}   | Q5-Q1 spread = {spread:+.1f}%")

print("\n" + "-" * 70)
print("MEAN FORWARD RETURN by classification (WEAK_SELL / HOLD / BUY)")
print("-" * 70)
for hname in HORIZONS:
    sub = df.dropna(subset=[hname])
    if sub.empty:
        continue
    means = sub.groupby("classification")[hname].mean() * 100
    counts = sub.groupby("classification")[hname].count()
    cells = "  ".join(
        f"{cls}={means.get(cls, float('nan')):+.1f}% (n={counts.get(cls, 0)})"
        for cls in ["WEAK_SELL", "HOLD", "BUY"]
        if cls in means.index
    )
    print(f"  {hname}: {cells}")
print("=" * 70)
