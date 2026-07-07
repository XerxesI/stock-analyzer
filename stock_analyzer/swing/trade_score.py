"""Combine momentum, RSI, support, and trend signals into a 0-100 Trade Score.

v2 weighting, revised after out-of-sample validation showed v1 (equal
34/33/33 weights) had a NEGATIVE information coefficient overall, driven
almost entirely by the trend component:

    factor            2wk IC    6wk IC
    trend_points      -0.025    -0.081   <- actively harmful, lagging
    momentum_points   -0.004    +0.013   <- roughly neutral
    support_points    -0.037    -0.006   <- not doing what we hoped
    rsi (raw)         +0.019    +0.008   <- weak but consistently positive

See stock_analyzer/evaluation/swing_rank_ic_test.py and
component_ic_test.py for the methodology (walk-forward, causal scoring,
Spearman IC vs realized forward return).

v2 changes:
    - trend weight cut sharply (34 -> 10): SMA200/Golden Cross confirms a
      trend only after much of the move has happened; keep it as a small
      tie-breaker, not a primary driver.
    - new continuous RSI component (0 -> 25): the only factor that was
      consistently on the right side of zero. Linear, not binary.
    - momentum kept close to its prior weight (33 -> 30): it was neutral,
      not harmful, and MACD histogram improvement is a reasonable proxy
      for a fresh short-term turn.
    - support kept but shrunk (33 -> 20) pending a redesign; it has not
      yet proven itself in isolation.

IMPORTANT CAVEAT: the IC evidence above came from a 30-symbol thematic
universe (ai + nuclear_energy) over ~3 years. These weights should be
re-validated out-of-sample (e.g. on LIQUID_LARGECAP) before being
trusted; a weak signal on one universe is not a proven general edge.

Score bands (per the swing trade spec):
    0-39   -> WEAK / SELL
    40-69  -> HOLD
    70-100 -> BUY
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from stock_analyzer.swing.support_zones import (
    DEFAULT_PROXIMITY_PCT,
    SupportZone,
    build_support_zones,
    nearest_zone,
)

TREND_MAX = 10
MOMENTUM_MAX = 30
RSI_MAX = 25
SUPPORT_MAX = 20
MAX_TOTAL = TREND_MAX + MOMENTUM_MAX + RSI_MAX + SUPPORT_MAX  # 85

SELL_THRESHOLD = 40  # scores below this are WEAK/SELL (on the normalized 0-100 scale)
BUY_THRESHOLD = 70  # scores at/above this are BUY (on the normalized 0-100 scale)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    return float(value)


def _trend_component(last_row: pd.Series) -> tuple[float, list[str]]:
    """Trend component: price vs SMA50/SMA200 and Golden Cross, max 10 points.

    Deliberately small in v2: this is a lagging confirmation signal, not
    a driver. See module docstring for the IC evidence.
    """

    price = _as_float(last_row.get("Close"))
    sma50 = _as_float(last_row.get("SMA50"))
    sma200 = _as_float(last_row.get("SMA200"))

    points = 0.0
    reasons: list[str] = []

    if price is None or sma50 is None:
        reasons.append("Trend data is incomplete (missing price or SMA50).")
        return points, reasons

    if price > sma50:
        points += 3
        reasons.append("Price is above SMA50.")
    else:
        reasons.append("Price is below SMA50.")

    if sma200 is not None:
        if price > sma200:
            points += 3
            reasons.append("Price is above SMA200.")
        else:
            reasons.append("Price is below SMA200.")

        if sma50 > sma200:
            points += 4
            reasons.append("SMA50 is above SMA200 (Golden Cross configuration).")
        else:
            reasons.append("SMA50 is below SMA200, so no Golden Cross confirmation.")
    else:
        reasons.append("SMA200 unavailable; long-term trend and Golden Cross not scored.")

    return points, reasons


def _momentum_component(df: pd.DataFrame) -> tuple[float, list[str]]:
    """Momentum component: MACD vs signal and histogram improvement, max 30 points."""

    last_row = df.iloc[-1]
    macd = _as_float(last_row.get("MACD"))
    macd_signal = _as_float(last_row.get("MACD_SIGNAL"))
    macd_hist = _as_float(last_row.get("MACD_HIST"))

    points = 0.0
    reasons: list[str] = []

    if macd is None or macd_signal is None:
        reasons.append("MACD data is unavailable.")
        return points, reasons

    if macd > macd_signal:
        points += 14
        reasons.append("MACD is above its signal line.")
    else:
        reasons.append("MACD is below its signal line.")

    if macd_hist is not None and len(df) > 1:
        prev_hist = _as_float(df.iloc[-2].get("MACD_HIST"))
        if prev_hist is not None and macd_hist > prev_hist:
            points += 16
            reasons.append("MACD histogram is improving (rising).")
        elif prev_hist is not None:
            reasons.append("MACD histogram is not improving.")
        else:
            reasons.append("Not enough history to judge MACD histogram direction.")
    else:
        reasons.append("MACD histogram is unavailable.")

    return points, reasons


def _rsi_component(last_row: pd.Series) -> tuple[float, list[str]]:
    """RSI component: continuous linear score, max 25 points.

    New in v2. Linear mapping of raw RSI (0-100) onto 0-25 points, rather
    than a binary oversold/overbought rule. This was the only factor with
    a consistently positive (if weak) IC in the validation run: higher
    RSI weakly predicted higher forward return in the tested universe.

    Deliberately NOT capped/inverted at high RSI (e.g. >70 "overbought")
    since the data did not show that reversal in the tested sample; this
    should be revisited if a broader out-of-sample test shows otherwise.
    """

    rsi = _as_float(last_row.get("RSI"))
    points = 0.0
    reasons: list[str] = []

    if rsi is None:
        reasons.append("RSI is unavailable.")
        return points, reasons

    points = round(RSI_MAX * max(0.0, min(100.0, rsi)) / 100.0, 1)
    reasons.append(f"RSI is {rsi:.1f} (linear score: {points:.1f}/{RSI_MAX}).")
    return points, reasons


def _support_component(
    df: pd.DataFrame,
    zones: list[SupportZone],
    proximity_pct: float,
) -> tuple[float, list[str], SupportZone | None]:
    """Support component: proximity to a support zone and recent bounce, max 20 points.

    Shrunk in v2 pending a redesign; it did not show a clear positive IC
    in isolation during validation, but it is kept (rather than removed)
    because a single-universe null result is not strong enough evidence
    to discard it outright.
    """

    inside_zone_points = 11
    bounce_points = 9

    points = 0.0
    reasons: list[str] = []

    if not zones:
        reasons.append("No support zones could be identified from price history yet.")
        return points, reasons, None

    price = float(df["Close"].iloc[-1])
    result = nearest_zone(zones, price)
    if result is None:
        reasons.append("No support zones could be identified from price history yet.")
        return points, reasons, None

    zone, distance = result
    abs_distance = abs(distance)

    if distance == 0.0:
        points += inside_zone_points
        reasons.append(
            f"Price is inside a {zone.strength} support zone (~{zone.low:.2f}-{zone.high:.2f})."
        )
    elif distance > 0 and abs_distance <= proximity_pct:
        proximity_points = round(inside_zone_points * (1 - abs_distance / proximity_pct), 1)
        points += proximity_points
        reasons.append(
            f"Price is {abs_distance * 100:.1f}% above a {zone.strength} support zone "
            f"(~{zone.low:.2f}-{zone.high:.2f})."
        )
    else:
        reasons.append(
            f"Nearest support zone (~{zone.low:.2f}-{zone.high:.2f}) is "
            f"{abs_distance * 100:.1f}% away, outside the proximity range."
        )

    if zone.bounce_count > 0:
        points += bounce_points
        reasons.append(
            f"Price has bounced off this zone {zone.bounce_count} time(s) recently."
        )
    else:
        reasons.append("No recent bounce confirmed off the nearest zone.")

    return points, reasons, zone


def classify_trade_score(score: float) -> str:
    """Map a normalized 0-100 Trade Score to a WEAK_SELL / HOLD / BUY label."""

    if score < SELL_THRESHOLD:
        return "WEAK_SELL"
    if score < BUY_THRESHOLD:
        return "HOLD"
    return "BUY"


def calculate_trade_score(
    df: pd.DataFrame,
    proximity_pct: float = DEFAULT_PROXIMITY_PCT,
) -> dict[str, Any]:
    """Calculate the 0-100 swing-trade Trade Score for the latest bar of ``df``.

    Args:
        df: OHLCV DataFrame already enriched by
            ``core.indicators.calculate_indicators`` (needs SMA50/200, RSI,
            MACD, MACD_SIGNAL, MACD_HIST, Close, Low).
        proximity_pct: How close (as a fraction of price) counts as "near"
            a support zone for scoring purposes.

    Returns:
        Dict with ``trade_score`` (0-100, normalized), ``raw_score``
        (sum of component points before normalization), ``classification``
        (WEAK_SELL/HOLD/BUY), per-component point breakdown, reasons, and
        the nearest support zone (if any).

    Raises:
        ValueError: If ``df`` is empty or missing a Close column.
    """

    if df.empty:
        raise ValueError("Input data is empty.")
    if "Close" not in df.columns:
        raise ValueError("Input data must contain a Close column.")

    last_row = df.iloc[-1]

    trend_points, trend_reasons = _trend_component(last_row)
    momentum_points, momentum_reasons = _momentum_component(df)
    rsi_points, rsi_reasons = _rsi_component(last_row)

    zones = build_support_zones(df)
    support_points, support_reasons, nearest = _support_component(df, zones, proximity_pct)

    raw_total = trend_points + momentum_points + rsi_points + support_points
    normalized_total = round(100 * raw_total / MAX_TOTAL)
    classification = classify_trade_score(normalized_total)

    return {
        "trade_score": normalized_total,
        "raw_score": round(raw_total, 1),
        "classification": classification,
        "components": {
            "trend": {"points": trend_points, "max": TREND_MAX, "reasons": trend_reasons},
            "momentum": {"points": momentum_points, "max": MOMENTUM_MAX, "reasons": momentum_reasons},
            "rsi": {"points": rsi_points, "max": RSI_MAX, "reasons": rsi_reasons},
            "support": {"points": support_points, "max": SUPPORT_MAX, "reasons": support_reasons},
        },
        "reasons": trend_reasons + momentum_reasons + rsi_reasons + support_reasons,
        "support_zones": zones,
        "nearest_support_zone": nearest,
        "price": _as_float(last_row.get("Close")),
    }
