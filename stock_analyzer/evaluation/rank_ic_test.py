"""Rank predictive-power test (information coefficient + quintiles).

Question: does the rank the strategy assigns actually predict FORWARD returns?
Method: at ~monthly dates across 2021-2026, compute each symbol's rank using the
SAME pipeline as backtest._build_opportunity (minus the buy/momentum gates, so we
get the full cross-section), then correlate rank with the realized forward return.

Caveat: get_fundamentals() returns CURRENT fundamentals applied to past dates
(mild look-ahead that can only INFLATE predictive power). So a weak/zero IC here
is a robust negative; a strong IC would need a point-in-time recheck.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.services.analysis_service import (    DEFAULT_SCORING_MODE,
    apply_completeness_penalty,
    apply_fundamental_bias_adjustment,
    combine_hybrid_rank,
    normalize_rank,
    resolve_scoring_mode,
    stretch_rank_distribution,
)
from stock_analyzer.data.fundamentals import get_fundamentals, score_fundamental_factors
from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.core.strategy import generate_signal
from stock_analyzer.data.universes import UNIVERSES
from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.core.strategy import generate_signal
from stock_analyzer.data.universes import UNIVERSES
SYMBOLS = sorted({s for u in UNIVERSES.values() for s in u})
FETCH_START = pd.Timestamp("2020-01-01")
FETCH_END = pd.Timestamp("2026-06-30")
TEST_START = pd.Timestamp("2021-06-30")
_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "rank_ic_obs.csv"
TEST_END = pd.Timestamp("2026-03-31")
STEP_DAYS = 21
HORIZONS = {"1mo": 21, "3mo": 63}  # forward trading days
MODE = resolve_scoring_mode(DEFAULT_SCORING_MODE)
UNKNOWN_SECTOR_PENALTY = 0.80

WINDOWS = [
    ("2021-22", pd.Timestamp("2021-06-30"), pd.Timestamp("2022-06-30")),
    ("2022-23", pd.Timestamp("2022-06-30"), pd.Timestamp("2023-06-30")),
    ("2023-24", pd.Timestamp("2023-06-30"), pd.Timestamp("2024-06-30")),
    ("2024-25", pd.Timestamp("2024-06-30"), pd.Timestamp("2025-06-30")),
    ("2025-26", pd.Timestamp("2025-06-30"), pd.Timestamp("2026-06-30")),
]


def _fetch(symbol: str) -> pd.DataFrame:
    raw = yf.download(
        symbol, start=FETCH_START.to_pydatetime(),
        end=(FETCH_END + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20,
    )
    if raw.empty:
        raise ValueError("empty")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.loc[:, ["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    return calculate_indicators(raw.sort_index())


print(f"fetching {len(SYMBOLS)} symbols + fundamentals...", flush=True)
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

_fund_cache: dict[str, dict] = {}


def fundamentals_for(symbol: str) -> dict:
    if symbol not in _fund_cache:
        try:
            _fund_cache[symbol] = get_fundamentals(symbol)
        except Exception:  # noqa: BLE001
            _fund_cache[symbol] = {}
    return _fund_cache[symbol]


def rank_at(symbol: str, frame: pd.DataFrame, t: pd.Timestamp) -> float | None:
    hist = frame.loc[:t].dropna(subset=["Close"])
    if len(hist) < 60:
        return None
    try:
        sig = generate_signal(hist)
    except (ValueError, RuntimeError):
        return None
    fund = fundamentals_for(symbol)
    sector = str(fund.get("sector") or "").lower().strip() or None
    details = score_fundamental_factors(fund, mode=MODE, sector=sector)
    tech_rank = normalize_rank(sig)
    fs = details.get("fundamental_score")
    fs = float(fs) if isinstance(fs, (int, float)) else None
    comp = details.get("fundamental_completeness")
    comp = float(comp) if isinstance(comp, (int, float)) else None
    r = combine_hybrid_rank(technical_rank=tech_rank, fundamental_score=fs)
    r = apply_fundamental_bias_adjustment(r, fs)
    r = stretch_rank_distribution(r)
    r = apply_completeness_penalty(r, comp)
    if not sector or sector == "unknown":
        r *= UNKNOWN_SECTOR_PENALTY
    return round(min(1.0, max(0.0, r)), 4)


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


# ---- collect (date, symbol, rank, fwd_1mo, fwd_3mo) ----
rows = []
dates = pd.date_range(TEST_START, TEST_END, freq=f"{STEP_DAYS}D")
print(f"scoring {len(dates)} dates x {len(frames)} symbols...", flush=True)
for i, t in enumerate(dates):
    for s, frame in frames.items():
        r = rank_at(s, frame, t)
        if r is None:
            continue
        rec = {"date": t, "symbol": s, "rank": r}
        ok = False
        for hname, h in HORIZONS.items():
            fr = fwd_return(frame, t, h)
            rec[hname] = fr
            ok = ok or (fr is not None)
        if ok:
            rows.append(rec)
    if (i + 1) % 10 == 0:
_ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
df.to_csv(_OBS_PATH, index=False)
print(f"\ntotal observations: {len(df)} (saved to {_OBS_PATH})", flush=True)
df = pd.DataFrame(rows)
df.to_csv("rank_ic_obs.csv", index=False)
print(f"\ntotal observations: {len(df)} (saved to rank_ic_obs.csv)", flush=True)


def spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman = Pearson correlation of ranks (no scipy needed)."""
    if len(a) < 2:
        return float("nan")
    return a.rank().corr(b.rank())  # pandas default = Pearson, numpy-based

# ---- information coefficient (Spearman rank corr) ----
print("\n" + "=" * 70)
print("INFORMATION COEFFICIENT (Spearman corr of rank vs forward return)")
print("IC ~0 = rank has no predictive power. >0.05 = weak. >0.10 = decent.")
print("-" * 70)
for hname in HORIZONS:
    sub = df.dropna(subset=[hname])
    ic = spearman(sub["rank"], sub[hname]) if len(sub) > 30 else float("nan")
    print(f"  overall IC ({hname}, n={len(sub)}): {ic:+.4f}")

print("\nIC by window (1mo horizon):")
for wname, ws, we in WINDOWS:
    sub = df[(df["date"] >= ws) & (df["date"] < we)].dropna(subset=["1mo"])
    ic = spearman(sub["rank"], sub["1mo"]) if len(sub) > 30 else float("nan")
    print(f"  {wname} (n={len(sub):>5}): IC = {ic:+.4f}")

# ---- quintile forward returns ----
print("\n" + "-" * 70)
print("MEAN FORWARD RETURN by rank quintile (Q1=lowest rank, Q5=highest)")
print("If rank works: Q5 > Q4 > ... > Q1 (monotonic) and Q5-Q1 clearly positive.")
print("-" * 70)
for hname in HORIZONS:
    sub = df.dropna(subset=[hname]).copy()
    if len(sub) < 50:
        continue
    try:
        sub["q"] = pd.qcut(sub["rank"], 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
    except ValueError:
        continue
    means = sub.groupby("q", observed=True)[hname].mean() * 100
    spread = means.iloc[-1] - means.iloc[0]
    cells = "  ".join(f"Q{int(q)}={means[q]:+.1f}%" for q in means.index)
    print(f"  {hname}: {cells}   | Q5-Q1 spread = {spread:+.1f}%")

# ---- entry-threshold test ----
print("\n" + "-" * 70)
print("STRATEGY ENTRY THRESHOLD (rank>=0.60 vs rank<0.60), 1mo forward return")
print("-" * 70)
sub = df.dropna(subset=["1mo"])
hi = sub[sub["rank"] >= 0.60]["1mo"]
lo = sub[sub["rank"] < 0.60]["1mo"]
print(f"  rank >= 0.60 (would buy): mean {hi.mean()*100:+.2f}%  (n={len(hi)})")
print(f"  rank <  0.60 (skip)     : mean {lo.mean()*100:+.2f}%  (n={len(lo)})")
print(f"  edge from threshold     : {(hi.mean()-lo.mean())*100:+.2f}%")
print("=" * 70)
