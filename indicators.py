"""Calculate technical indicators for stock data."""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add technical indicators to a stock OHLCV DataFrame.

    Args:
        df: DataFrame containing at least a ``Close`` column.

    Returns:
        A copy of ``df`` with RSI, SMA50, SMA200, volume trend, and optional indicators.

    Raises:
        ValueError: If the input DataFrame is empty or missing required data.
    """

    if df.empty:
        raise ValueError("Input data is empty.")
    if "Close" not in df.columns:
        raise ValueError("Input data must contain a Close column.")

    enriched = df.copy()
    close = enriched["Close"]

    enriched["RSI"] = ta.rsi(close, length=14)
    enriched["SMA50"] = ta.sma(close, length=50)
    enriched["SMA200"] = ta.sma(close, length=200)
    enriched["VOLUME_SMA20"] = ta.sma(enriched["Volume"], length=20)

    macd = ta.macd(close=close)
    if macd is not None and not macd.empty:
        enriched["MACD"] = macd.iloc[:, 0]
        enriched["MACD_SIGNAL"] = macd.iloc[:, 1]
        enriched["MACD_HIST"] = macd.iloc[:, 2]

    bands = ta.bbands(close=close)
    if bands is not None and not bands.empty:
        enriched["BBL"] = bands.iloc[:, 0]
        enriched["BBM"] = bands.iloc[:, 1]
        enriched["BBU"] = bands.iloc[:, 2]
        enriched["BBP"] = bands.iloc[:, 4]

    return enriched
