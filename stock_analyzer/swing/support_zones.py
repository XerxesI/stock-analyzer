"""Detect and score support zones from historical price action.

Yahoo Finance does not provide support levels directly, so we derive
them from pivot lows in the ``Low`` series: local minima are clustered
into price zones (not exact lines), and each zone is scored by how many
times price has bounced off it and how recently that happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

DEFAULT_PIVOT_WINDOW = 5
DEFAULT_ZONE_TOLERANCE_PCT = 0.015  # cluster pivot lows within 1.5% of each other
DEFAULT_MAX_ZONES = 5
DEFAULT_BOUNCE_LOOKBACK_DAYS = 10
DEFAULT_BOUNCE_MIN_GAIN_PCT = 0.02  # 2% rise off the low counts as a "bounce"
DEFAULT_PROXIMITY_PCT = 0.05  # within 5% of a zone counts as "near support"


@dataclass
class SupportZone:
    """A clustered support price zone derived from historical pivot lows."""

    low: float
    high: float
    level: float  # representative price (mean of clustered pivot lows)
    touches: int
    last_touch_date: pd.Timestamp
    strength: str  # "weak" | "moderate" | "strong"
    bounce_count: int = 0
    touch_dates: list[pd.Timestamp] = field(default_factory=list)

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high

    def distance_pct(self, price: float) -> float:
        """Signed distance from ``price`` to the zone's nearest edge, as a fraction of price.

        Zero if price is inside the zone. Positive if price is above the zone
        (zone acts as support below), negative if price is below the zone.
        """

        if self.contains(price):
            return 0.0
        if price > self.high:
            return (price - self.high) / price
        return (price - self.low) / price


def find_pivot_lows(df: pd.DataFrame, window: int = DEFAULT_PIVOT_WINDOW) -> pd.Series:
    """Return a boolean Series marking bars where ``Low`` is a local minimum.

    A bar is a pivot low if its Low is the minimum within a symmetric
    window of ``window`` bars on each side. Edge bars that lack a full
    window on either side are never flagged.

    Args:
        df: DataFrame with a ``Low`` column, indexed chronologically.
        window: Number of bars required on each side to confirm a pivot.

    Returns:
        Boolean Series aligned to ``df.index``.

    Raises:
        ValueError: If ``df`` is empty or missing a ``Low`` column.
    """

    if df.empty:
        raise ValueError("Input data is empty.")
    if "Low" not in df.columns:
        raise ValueError("Input data must contain a Low column.")

    low = df["Low"]
    is_pivot = pd.Series(False, index=df.index)

    for i in range(window, len(df) - window):
        segment = low.iloc[i - window : i + window + 1]
        if low.iloc[i] == segment.min() and (segment == low.iloc[i]).sum() == 1:
            is_pivot.iloc[i] = True

    return is_pivot


def _strength_label(touches: int) -> str:
    if touches >= 3:
        return "strong"
    if touches == 2:
        return "moderate"
    return "weak"


def build_support_zones(
    df: pd.DataFrame,
    window: int = DEFAULT_PIVOT_WINDOW,
    tolerance_pct: float = DEFAULT_ZONE_TOLERANCE_PCT,
    max_zones: int = DEFAULT_MAX_ZONES,
    bounce_lookback_days: int = DEFAULT_BOUNCE_LOOKBACK_DAYS,
    bounce_min_gain_pct: float = DEFAULT_BOUNCE_MIN_GAIN_PCT,
) -> list[SupportZone]:
    """Cluster pivot lows into support zones and score their strength.

    Args:
        df: OHLCV DataFrame indexed chronologically (needs Low and Close).
        window: Pivot-low detection window, see ``find_pivot_lows``.
        tolerance_pct: Pivot lows within this fraction of each other's price
            are merged into the same zone (e.g. 0.015 = 1.5%).
        max_zones: Maximum number of zones to return, ranked by strength
            (touches) then recency.
        bounce_lookback_days: How many trailing bars to check for a recent
            bounce off each zone.
        bounce_min_gain_pct: Minimum rise from a touch's Low to the latest
            Close to count as a confirmed bounce.

    Returns:
        List of ``SupportZone`` sorted by strength (touches desc), then by
        most recent touch first. Empty list if no pivot lows are found.

    Raises:
        ValueError: If ``df`` is empty or missing required columns.
    """

    if df.empty:
        raise ValueError("Input data is empty.")
    if "Close" not in df.columns:
        raise ValueError("Input data must contain a Close column.")

    pivot_mask = find_pivot_lows(df, window=window)
    pivots = df.loc[pivot_mask, ["Low"]].copy()
    pivots["date"] = pivots.index

    if pivots.empty:
        return []

    # Cluster by ascending price so nearby pivots merge into one zone.
    pivots = pivots.sort_values("Low")
    clusters: list[list[tuple[float, pd.Timestamp]]] = []

    for _, row in pivots.iterrows():
        price = float(row["Low"])
        date = row["date"]
        if clusters and abs(price - clusters[-1][-1][0]) / price <= tolerance_pct:
            clusters[-1].append((price, date))
        else:
            clusters.append([(price, date)])

    latest_close = float(df["Close"].iloc[-1])
    latest_index = len(df) - 1
    zones: list[SupportZone] = []

    for cluster in clusters:
        prices = [p for p, _ in cluster]
        dates = [d for _, d in cluster]
        low, high = min(prices), max(prices)
        level = sum(prices) / len(prices)
        last_touch = max(dates)
        touches = len(cluster)

        # Bounce check: did any touch within the lookback window see price
        # subsequently rise by at least bounce_min_gain_pct off that low?
        bounce_count = 0
        for price, date in cluster:
            touch_pos = df.index.get_loc(date)
            if isinstance(touch_pos, slice):
                continue
            if latest_index - touch_pos > bounce_lookback_days:
                continue
            if price <= 0:
                continue
            gain = (latest_close - price) / price
            if gain >= bounce_min_gain_pct:
                bounce_count += 1

        zones.append(
            SupportZone(
                low=low,
                high=high,
                level=level,
                touches=touches,
                last_touch_date=last_touch,
                strength=_strength_label(touches),
                bounce_count=bounce_count,
                touch_dates=sorted(dates),
            )
        )

    zones.sort(key=lambda z: (-z.touches, -z.last_touch_date.value))
    return zones[:max_zones]


def nearest_zone(zones: list[SupportZone], price: float) -> tuple[SupportZone, float] | None:
    """Return the closest zone to ``price`` and its signed distance fraction.

    Zones the price is currently inside are treated as distance 0 and take
    priority; otherwise the zone with the smallest absolute distance wins.

    Returns:
        Tuple of (zone, distance_pct), or ``None`` if ``zones`` is empty.
    """

    if not zones:
        return None

    best_zone = None
    best_distance = float("inf")

    for zone in zones:
        distance = zone.distance_pct(price)
        if abs(distance) < abs(best_distance):
            best_zone = zone
            best_distance = distance

    if best_zone is None:
        return None
    return best_zone, best_distance
