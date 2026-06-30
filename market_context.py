"""Market context helpers for benchmark-aware analysis."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from data_fetcher import get_stock_data
from indicators import calculate_indicators
from strategy import generate_signal


MARKET_BENCHMARK = "SPY"


def _market_bias(signal: str) -> str:
    if signal in {"BUY", "STRONG BUY"}:
        return "bullish"
    if signal in {"SELL", "STRONG SELL"}:
        return "bearish"
    return "neutral"


def get_market_context(period: str) -> dict[str, Any]:
    """Return benchmark context derived from SPY."""

    raw_data = get_stock_data(MARKET_BENCHMARK, period)
    enriched_data = calculate_indicators(raw_data)
    signal_data = generate_signal(enriched_data)
    bias = _market_bias(str(signal_data.get("signal", "HOLD")))

    return {
        "benchmark": MARKET_BENCHMARK,
        "signal": signal_data.get("signal"),
        "score": signal_data.get("score"),
        "confidence": signal_data.get("confidence"),
        "confidence_label": signal_data.get("confidence_label"),
        "trend_strength": signal_data.get("trend_strength"),
        "bias": bias,
        "explanation": signal_data.get("decision_summary"),
    }


@lru_cache(maxsize=10)
def _cached_market_context(period: str) -> dict[str, Any]:
    """Cache benchmark context per period to avoid repeated SPY fetches."""

    return get_market_context(period)


def resolve_market_context(period: str) -> dict[str, Any]:
    """Return market context, falling back to neutral if benchmark data is unavailable."""

    try:
        return _cached_market_context(period).copy()
    except (ValueError, RuntimeError) as exc:
        return {
            "benchmark": MARKET_BENCHMARK,
            "signal": "HOLD",
            "score": 0,
            "confidence": 0.0,
            "confidence_label": "low",
            "trend_strength": "unknown",
            "bias": "neutral",
            "error": str(exc),
            "explanation": "Market context unavailable, so the benchmark bias is treated as neutral.",
        }
