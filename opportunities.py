"""Opportunity filtering and classification helpers."""

from __future__ import annotations

from typing import Any, Sequence


BUY_SIGNALS = {"BUY", "STRONG BUY"}


def classify_opportunity(item: dict[str, Any]) -> str:
    """Classify a signal as trend-following, reversal, or mixed."""

    signal = str(item.get("signal", "HOLD"))
    trend = str(item.get("trend_strength", "unknown"))
    rsi = float(item.get("rsi", 0) or 0)
    macd = item.get("macd")
    macd_signal = item.get("macd_signal")

    bullish_macd = macd is not None and macd_signal is not None and float(macd) > float(macd_signal)

    if signal in BUY_SIGNALS and "uptrend" in trend:
        return "trend_following"
    if signal in BUY_SIGNALS and rsi < 30 and bullish_macd:
        return "reversal"
    if signal in BUY_SIGNALS:
        return "mixed_buy"
    if signal in {"SELL", "STRONG SELL"} and "downtrend" in trend:
        return "trend_reversal_down"
    return "mixed"


def is_buy_opportunity(
    item: dict[str, Any],
    min_confidence: float = 0.5,
    min_rank: float = 0.4,
    min_fundamental_score: float | None = None,
) -> bool:
    """Return True when a result is worth surfacing as a buy opportunity."""

    signal = str(item.get("signal", "HOLD"))
    confidence = float(item.get("confidence", 0) or 0)
    rank = float(item.get("rank", 0) or 0)
    if signal not in BUY_SIGNALS or confidence < min_confidence or rank < min_rank:
        return False
    if min_fundamental_score is None:
        return True
    fundamental_score = float(item.get("fundamental_score", 0) or 0)
    return fundamental_score >= min_fundamental_score


def rank_opportunities(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort opportunities by normalized rank and confidence."""

    return sorted(
        items,
        key=lambda item: (
            float(item.get("rank", 0) or 0),
            float(item.get("confidence", 0) or 0),
        ),
        reverse=True,
    )
