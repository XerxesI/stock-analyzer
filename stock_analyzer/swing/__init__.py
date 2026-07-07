"""Swing-trade specific analysis: support zones and the 0-100 Trade Score.

This package is intentionally separate from ``core/strategy.py`` and
``core/opportunities.py``. Those modules produce a -6..+6 score and a
0..1 rank/confidence that ``backtesting/backtest.py`` is calibrated
against (MIN_ENTRY_RANK=0.60, KEEP_RANK_THRESHOLD=0.50). Mixing a new
0-100 scale into that scoring would silently break those thresholds.

Swing trade scoring reuses the indicators already computed in
``core/indicators.py`` (RSI, SMA50/200, MACD, Bollinger Bands) but keeps
its own scoring, thresholds, and vocabulary (Trade Score, BUY/HOLD/SELL).
"""

from __future__ import annotations

from stock_analyzer.swing.support_zones import SupportZone, build_support_zones, nearest_zone
from stock_analyzer.swing.trade_score import calculate_trade_score, classify_trade_score

__all__ = [
    "SupportZone",
    "build_support_zones",
    "nearest_zone",
    "calculate_trade_score",
    "classify_trade_score",
]
