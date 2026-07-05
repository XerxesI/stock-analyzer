"""Cross-sectional factor primitives for the hybrid strategy.

Pure functions, no I/O. Everything is strictly no-lookahead: a value computed
"as of" date T depends only on rows with index <= T, and is invariant to whether
future rows exist in the frame (verified in run_hybrid.py --selftest).
"""

from __future__ import annotations

from typing import Mapping

import pandas as pd

MOMENTUM_LOOKBACK = 252  # ~12 months of trading days
MOMENTUM_SKIP = 21       # skip most recent ~1 month (avoids short-term reversal)


def _pos_asof(index: pd.DatetimeIndex, asof: pd.Timestamp) -> int:
    """Positional index of the last bar with date <= asof (-1 if none)."""

    return int(index.searchsorted(asof, side="right")) - 1


def price_asof(frame: pd.DataFrame, asof: pd.Timestamp) -> float:
    """Most recent close at or before ``asof``; 0.0 if no data yet."""

    pos = _pos_asof(frame.index, asof)
    if pos < 0:
        return 0.0
    return float(frame["Close"].to_numpy()[pos])


def momentum_12_1(
    frame: pd.DataFrame,
    asof: pd.Timestamp,
    lookback: int = MOMENTUM_LOOKBACK,
    skip: int = MOMENTUM_SKIP,
) -> float | None:
    """Trailing 12-1 total return: close[T-skip] / close[T-lookback] - 1.

    Returns None if there is not enough history at ``asof``. Uses positional
    access so future rows never leak into the result.
    """

    pos = _pos_asof(frame.index, asof)
    if pos < lookback:
        return None
    close = frame["Close"].to_numpy()
    p_recent = close[pos - skip]
    p_old = close[pos - lookback]
    if p_old <= 0:
        return None
    return float(p_recent / p_old - 1.0)


def cross_sectional_rank(scores: Mapping[str, float]) -> dict[str, float]:
    """Percentile rank of each symbol within the peer group, in [0, 1].

    Highest raw score -> 1.0. This is the relative/cross-sectional step the old
    absolute per-stock rank never did.
    """

    if not scores:
        return {}
    ordered = sorted(scores.items(), key=lambda kv: kv[1])
    n = len(ordered)
    if n == 1:
        return {ordered[0][0]: 1.0}
    return {sym: i / (n - 1) for i, (sym, _) in enumerate(ordered)}


def top_n_by_momentum(
    frames: Mapping[str, pd.DataFrame],
    asof: pd.Timestamp,
    n: int,
    exclude: set[str] | None = None,
) -> list[str]:
    """Symbols with the highest 12-1 momentum as of ``asof`` (descending)."""

    exclude = exclude or set()
    scores: dict[str, float] = {}
    for symbol, frame in frames.items():
        if symbol in exclude:
            continue
        m = momentum_12_1(frame, asof)
        if m is not None:
            scores[symbol] = m
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [symbol for symbol, _ in ranked[:n]]
