"""Isolate which Trade Score component (if any) actually predicts forward returns.

The combined score showed a NEGATIVE information coefficient (see
swing_rank_ic_test.py output). Before changing weights blindly, this
checks each ingredient in isolation:

  - trend_points   (0-34): price vs SMA50/SMA200, Golden Cross
  - momentum_points (0-33): MACD vs signal, histogram improvement
  - support_points  (0-33): proximity to a support zone + recent bounce
  - rsi             (0-100): raw RSI value, as a simple oversold/overbought probe

If, say, RSI alone has a strong negative IC (low RSI -> high forward
return) while trend_points has a strongly negative IC too, that tells us
the model's trend component is actively fighting the mean-reversion
effect that is actually present in this universe/period - useful before
deciding whether to reweight, invert, or replace a component.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universes import get_universe
from stock_analyzer.swing.trade_score import calculate_trade_score

SYMBOLS = sorted(set(get_universe("ai")) | set(get_universe("nuclear_energy")))

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START = FETCH_START + pd.Timedelta(days=210)
TEST_END = FETCH_END - pd.Timedelta(days=50)
STEP_DAYS = 5

HORIZONS = {"2wk": 10, "6wk": 30}
MIN_HISTORY_BARS = 210

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "swing_component_ic_obs.csv"


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


print(f"fetching {len(SYMBOLS)} symbols...", flush=True)
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


def components_at(frame: pd.DataFrame, t: pd.Timestamp) -> dict | None:
    hist = frame.loc[:t].dropna(subset=["Close"])
    if len(hist) < MIN_HISTORY_BARS:
        return None
    try:
        result = calculate_trade_score(hist)
    except (ValueError, RuntimeError):
        return None
    last_row = hist.iloc[-1]
    rsi = last_row.get("RSI")
    if pd.isna(rsi):
        rsi = None
    return {
        "trend_points": result["components"]["trend"]["points"],
        "momentum_points": result["components"]["momentum"]["points"],
        "support_points": result["components"]["support"]["points"],
        "rsi": float(rsi) if rsi is not None else None,
    }


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
print(f"scoring {len(dates)} dates x {len(frames)} symbols...", flush=True)
for i, t in enumerate(dates):
    for s, frame in frames.items():
        comp = components_at(frame, t)
        if comp is None:
            continue
        rec = {"date": t, "symbol": s, **comp}
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


FACTORS = ["trend_points", "momentum_points", "support_points", "rsi"]

print("\n" + "=" * 70)
print("INFORMATION COEFFICIENT per factor (Spearman corr vs forward return)")
print("Positive IC = higher factor value -> higher forward return.")
print("Negative IC = higher factor value -> LOWER forward return (factor is")
print("  fighting the actual edge, or the edge is inverse/mean-reverting).")
print("-" * 70)
for hname in HORIZONS:
    sub = df.dropna(subset=[hname])
    print(f"\n  horizon={hname} (n={len(sub)}):")
    for factor in FACTORS:
        fsub = sub.dropna(subset=[factor])
        ic = spearman(fsub[factor], fsub[hname]) if len(fsub) > 30 else float("nan")
        print(f"    {factor:<18}: IC = {ic:+.4f}  (n={len(fsub)})")

print("\n" + "-" * 70)
print("MEAN FORWARD RETURN by factor quintile (Q1=lowest, Q5=highest)")
print("-" * 70)
for hname in HORIZONS:
    sub = df.dropna(subset=[hname])
    print(f"\n  horizon={hname}:")
    for factor in FACTORS:
        fsub = sub.dropna(subset=[factor]).copy()
        if len(fsub) < 50:
            continue
        try:
            fsub["q"] = pd.qcut(fsub[factor], 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
        except ValueError:
            continue
        means = fsub.groupby("q", observed=True)[hname].mean() * 100
        if len(means) < 2:
            continue
        spread = means.iloc[-1] - means.iloc[0]
        cells = "  ".join(f"Q{int(q)}={means[q]:+.1f}%" for q in means.index)
        print(f"    {factor:<18}: {cells}   | Q5-Q1 spread = {spread:+.1f}%")
print("=" * 70)