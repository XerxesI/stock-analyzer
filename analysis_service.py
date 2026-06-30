"""Shared analysis service for CLI tools and API endpoints."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Lock
from time import perf_counter
from typing import Any, Sequence

from data_fetcher import get_stock_data
from indicators import calculate_indicators
from market_context import resolve_market_context
from opportunities import classify_opportunity
from report import build_explanation
from strategy import generate_signal


DEFAULT_PERIOD = "1y"
RANK_LIMIT = 1.0
MAX_WORKERS = max(4, min(32, (os.cpu_count() or 4) * 2))
_METRICS_LOCK = Lock()
_METRICS: dict[str, float | int] = {
    "symbol_requests": 0,
    "symbol_failures": 0,
    "symbol_latency_ms_total": 0.0,
    "symbol_latency_samples": 0,
    "batch_requests": 0,
}


def _trend_weight(trend_strength: str | None) -> float:
    """Return a normalized weight for trend strength."""

    mapping = {
        "strong uptrend": 1.2,
        "strong downtrend": 1.2,
        "weak uptrend": 1.0,
        "weak downtrend": 1.0,
        "short-term uptrend": 0.9,
        "short-term downtrend": 0.9,
        "sideways": 0.8,
        "unknown": 0.8,
        None: 0.8,
    }
    return mapping.get(trend_strength, 0.8)


def _market_weight(signal: str | None, market_bias: str | None) -> float:
    """Return a normalized weight for market context alignment."""

    if not signal or not market_bias or market_bias == "neutral":
        return 1.0
    bullish = signal in {"BUY", "STRONG BUY"}
    bearish = signal in {"SELL", "STRONG SELL"}
    if (bullish and market_bias == "bullish") or (bearish and market_bias == "bearish"):
        return 1.1
    if bullish and market_bias == "bearish":
        return 0.8
    if bearish and market_bias == "bullish":
        return 0.8
    return 1.0


def confidence_interpretation(confidence_label: str | None) -> str:
    """Map confidence label to a short human-readable interpretation."""

    if confidence_label == "high":
        return "strong conviction, aligned signals"
    if confidence_label == "medium":
        return "moderate conviction, mixed signals present"
    return "weak conviction, signal quality is limited"


def normalize_rank(signal_data: dict[str, object]) -> float:
    """Combine confidence, trend, and market context into a normalized rank."""

    confidence = float(signal_data.get("confidence", 0) or 0)
    trend_weight = _trend_weight(str(signal_data.get("trend_strength", "unknown")))
    market_weight = _market_weight(
        str(signal_data.get("signal", "HOLD")),
        str(signal_data.get("market_bias", "neutral")),
    )
    rank = confidence * trend_weight * market_weight
    return round(min(RANK_LIMIT, rank), 2)


def analyze_symbol_data(
    symbol: str,
    period: str = DEFAULT_PERIOD,
    market_context: dict[str, object] | None = None,
) -> dict[str, object]:
    """Run the full analysis pipeline for a single symbol and return structured data."""

    started_at = perf_counter()
    successful = False
    try:
        if market_context is None:
            market_context = resolve_market_context(period)
        raw_data = get_stock_data(symbol, period)
        enriched_data = calculate_indicators(raw_data)
        signal_data = generate_signal(enriched_data, market_context=market_context)
        explanation = build_explanation(signal_data)
        rank = normalize_rank(signal_data)
        successful = True
        return {
            "symbol": symbol.upper(),
            "period": period,
            "market_context": market_context,
            "market_context_error": market_context.get("error"),
            "price": signal_data.get("price"),
            "rsi": signal_data.get("rsi"),
            "sma50": signal_data.get("sma50"),
            "sma200": signal_data.get("sma200"),
            "macd": signal_data.get("macd"),
            "macd_signal": signal_data.get("macd_signal"),
            "macd_hist": signal_data.get("macd_hist"),
            "volume": signal_data.get("volume"),
            "volume_sma20": signal_data.get("volume_sma20"),
            "score": signal_data.get("score"),
            "confidence": signal_data.get("confidence"),
            "confidence_label": signal_data.get("confidence_label"),
            "trend_strength": signal_data.get("trend_strength"),
            "market_bias": signal_data.get("market_bias"),
            "rank": rank,
            "confidence_interpretation": confidence_interpretation(signal_data.get("confidence_label")),
            "opportunity_type": classify_opportunity({**signal_data, "rank": rank}),
            "signal": signal_data.get("signal"),
            "explanation": explanation,
        }
    finally:
        latency_ms = (perf_counter() - started_at) * 1000
        with _METRICS_LOCK:
            _METRICS["symbol_requests"] = int(_METRICS["symbol_requests"]) + 1
            _METRICS["symbol_latency_ms_total"] = float(_METRICS["symbol_latency_ms_total"]) + latency_ms
            _METRICS["symbol_latency_samples"] = int(_METRICS["symbol_latency_samples"]) + 1
            if not successful:
                _METRICS["symbol_failures"] = int(_METRICS["symbol_failures"]) + 1


def _safe_analyze_symbol_data(
    symbol: str,
    period: str,
    market_context: dict[str, object],
) -> dict[str, object]:
    """Analyze one symbol and always return a result payload."""

    try:
        return analyze_symbol_data(symbol, period, market_context)
    except (ValueError, RuntimeError) as exc:
        return {"symbol": symbol.upper(), "period": period, "error": str(exc)}


def analyze_symbols_data(symbols: Sequence[str], period: str = DEFAULT_PERIOD) -> list[dict[str, object]]:
    """Analyze multiple symbols in parallel and return structured results."""

    symbol_list = [symbol for symbol in symbols if symbol and symbol.strip()]
    with _METRICS_LOCK:
        _METRICS["batch_requests"] = int(_METRICS["batch_requests"]) + 1
    if not symbol_list:
        return []

    market_context = resolve_market_context(period)
    workers = min(MAX_WORKERS, len(symbol_list))
    analyzer = partial(_safe_analyze_symbol_data, period=period, market_context=market_context)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(analyzer, symbol_list))


def get_analysis_metrics() -> dict[str, float | int]:
    """Expose throughput/latency/error metrics for runtime monitoring."""

    with _METRICS_LOCK:
        symbol_requests = int(_METRICS["symbol_requests"])
        symbol_failures = int(_METRICS["symbol_failures"])
        latency_total = float(_METRICS["symbol_latency_ms_total"])
        latency_samples = int(_METRICS["symbol_latency_samples"])
        batch_requests = int(_METRICS["batch_requests"])
    avg_latency = (latency_total / latency_samples) if latency_samples else 0.0
    error_rate = (symbol_failures / symbol_requests) if symbol_requests else 0.0
    return {
        "symbol_requests": symbol_requests,
        "symbol_failures": symbol_failures,
        "symbol_error_rate": round(error_rate, 4),
        "average_symbol_latency_ms": round(avg_latency, 2),
        "batch_requests": batch_requests,
        "max_workers": MAX_WORKERS,
    }
