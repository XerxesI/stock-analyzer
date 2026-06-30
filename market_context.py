"""Market context helpers for benchmark-aware analysis."""

from __future__ import annotations

from typing import Any

from cache_utils import TTLCache
from data_fetcher import get_stock_data
from indicators import calculate_indicators
from strategy import generate_signal


MARKET_BENCHMARK = "SPY"
MARKET_CONTEXT_TTL_SECONDS = 180
_market_context_cache: TTLCache[str, dict[str, Any]] = TTLCache(
    maxsize=16,
    default_ttl_seconds=MARKET_CONTEXT_TTL_SECONDS,
    name="market_context",
)


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


def resolve_market_context(period: str) -> dict[str, Any]:
    """Return market context, falling back to neutral if benchmark data is unavailable."""

    cleaned_period = period.strip()
    if not cleaned_period:
        raise ValueError("Period must not be empty.")

    try:
        cached_context = _market_context_cache.get_or_set(cleaned_period, lambda: get_market_context(cleaned_period))
        return cached_context.copy()
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


def get_market_context_metrics() -> dict[str, object]:
    """Expose market-context cache metrics for runtime monitoring."""

    return {"cache": _market_context_cache.snapshot()}
