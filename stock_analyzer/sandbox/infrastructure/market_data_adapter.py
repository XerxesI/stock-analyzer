"""Point-in-time market-data access for the sandbox.

Wraps the existing stock_analyzer.data.data_fetcher.get_stock_data -- does not
reimplement fetching or caching. The only new logic here is the as-of cutoff: unlike
stock_analyzer.datasets.swing_20.prepare._apply_current_day_cutoff (which drops bars
dated on or after "today" because today is still open), `as_of_date` here is asserted
by the caller to already be a *closed* trading day, so its own bar is included and
only bars strictly after it are excluded.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from stock_analyzer.data.data_fetcher import get_stock_data

DEFAULT_FETCH_PERIOD = "2y"


def fetch_as_of(symbol: str, as_of_date: date, period: str = DEFAULT_FETCH_PERIOD) -> pd.DataFrame:
    """OHLCV history for `symbol` truncated to bars dated `<= as_of_date`.

    Never returns a bar dated after `as_of_date`, regardless of what the underlying
    fetch call returns.
    """

    data = get_stock_data(symbol, period)
    if data.empty:
        return data
    keep_mask = pd.DatetimeIndex(data.index).date <= as_of_date
    return data.loc[keep_mask]


def latest_close(prices: pd.DataFrame) -> float | None:
    if prices.empty:
        return None
    value = prices["Close"].iloc[-1]
    return float(value) if pd.notna(value) else None


def session_bar(prices: pd.DataFrame, session_date: date) -> pd.Series | None:
    """The exact OHLCV bar for `session_date`, or None if that date has no bar
    (e.g. a non-trading day, or data not yet available for it)."""

    if prices.empty:
        return None
    matches = prices.loc[pd.DatetimeIndex(prices.index).date == session_date]
    if matches.empty:
        return None
    return matches.iloc[-1]
