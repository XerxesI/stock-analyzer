"""Fetch historical stock data from Yahoo Finance."""

from __future__ import annotations

import logging
from functools import lru_cache
from time import sleep

import pandas as pd
import yfinance as yf


REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 0.4
REQUEST_TIMEOUT_SECONDS = 15

# yfinance emits noisy per-symbol error logs; we surface failures via raised exceptions.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def _normalize_data(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance output to a flat OHLCV frame."""

    if data.empty:
        raise ValueError("No data returned.")

    if isinstance(data.columns, pd.MultiIndex):
        flattened = data.copy()
        if all(column in flattened.columns.get_level_values(0) for column in REQUIRED_COLUMNS):
            flattened.columns = flattened.columns.get_level_values(0)
        elif all(column in flattened.columns.get_level_values(-1) for column in REQUIRED_COLUMNS):
            flattened.columns = flattened.columns.get_level_values(-1)
        else:
            raise ValueError("Fetched data has an unexpected column layout and cannot be normalized.")
        data = flattened

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing_columns:
        raise ValueError(f"Fetched data is missing required columns: {', '.join(missing_columns)}.")

    return data.loc[:, REQUIRED_COLUMNS].copy()


@lru_cache(maxsize=256)
def _fetch_with_retry(cleaned_symbol: str, cleaned_period: str) -> pd.DataFrame:
    """Fetch and cache stock data with retry support."""

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = yf.download(
                cleaned_symbol,
                period=cleaned_period,
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            return _normalize_data(data)
        except (ConnectionError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                sleep(RETRY_SLEEP_SECONDS * attempt)

    if isinstance(last_error, ValueError):
        raise ValueError(f"No usable data returned for symbol '{cleaned_symbol}'.") from last_error
    if last_error is not None:
        raise RuntimeError(f"Failed to fetch data for {cleaned_symbol}: {last_error}") from last_error
    raise RuntimeError(f"Failed to fetch data for {cleaned_symbol}.")


def get_stock_data(symbol: str, period: str) -> pd.DataFrame:
    """Return historical OHLCV data for ``symbol`` over ``period``.

    Args:
        symbol: Stock ticker symbol, for example ``AAPL``.
        period: Yahoo Finance period such as ``1mo`` or ``1y``.

    Returns:
        A pandas DataFrame containing Open, High, Low, Close, and Volume.

    Raises:
        ValueError: If the symbol/period is invalid or no data is returned.
        RuntimeError: If Yahoo Finance data retrieval fails unexpectedly.
    """

    cleaned_symbol = symbol.strip().upper()
    cleaned_period = period.strip()

    if not cleaned_symbol:
        raise ValueError("Symbol must not be empty.")
    if not cleaned_period:
        raise ValueError("Period must not be empty.")

    # Return a copy so downstream indicator enrichment never mutates cached frames.
    return _fetch_with_retry(cleaned_symbol, cleaned_period).copy()
