"""Shared analysis service for CLI tools and API endpoints."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import BoundedSemaphore, Lock
from time import perf_counter
from typing import Any, Sequence

from data_fetcher import get_stock_data
from fundamentals import classify_fundamental_bias, get_fundamentals, score_fundamental_factors
from indicators import calculate_indicators
from market_context import resolve_market_context
from metrics_store import load_metrics_section, persist_metrics_section
from opportunities import classify_opportunity
from report import build_explanation
from runtime_limits import ANALYSIS_BATCH_WORKERS, GLOBAL_ANALYSIS_CONCURRENCY
from strategy import generate_signal


DEFAULT_PERIOD = "1y"
DEFAULT_SCORING_MODE = "balanced"
SUPPORTED_SCORING_MODES = ("growth", "balanced", "defensive", "auto")
RANK_LIMIT = 1.0
RANK_DISTRIBUTION_EXPONENT = 1.8
CONFIDENCE_DISTRIBUTION_EXPONENT = 1.5
TECHNICAL_MAX_SCORE = 6.0
PENALIZE_MISSING_FUNDAMENTALS = False
MISSING_FUNDAMENTALS_PENALTY_WEIGHT = 0.15
BIAS_RANK_ADJUSTMENT_MAX = 0.10
APPLY_COMPLETENESS_PENALTY = False
COMPLETENESS_PENALTY_WEIGHT = 0.15
MAX_WORKERS = ANALYSIS_BATCH_WORKERS
_ANALYSIS_SEMAPHORE = BoundedSemaphore(GLOBAL_ANALYSIS_CONCURRENCY)
_METRICS_LOCK = Lock()
_METRICS: dict[str, float | int] = {
    "symbol_requests": 0,
    "symbol_failures": 0,
    "symbol_latency_ms_total": 0.0,
    "symbol_latency_samples": 0,
    "batch_requests": 0,
}
_METRICS.update(load_metrics_section("analysis", _METRICS))
_METRICS_UPDATES = 0
_METRICS_PERSIST_EVERY = 20
SECTOR_MODE_MAP: dict[str, str] = {
    "energy": "defensive",
    "sp500": "balanced",
    "nasdaq": "growth",
    "ai": "growth",
    "biotech_genomics": "growth",
}


def _persist_analysis_metrics() -> None:
    with _METRICS_LOCK:
        snapshot = {
            "symbol_requests": int(_METRICS["symbol_requests"]),
            "symbol_failures": int(_METRICS["symbol_failures"]),
            "symbol_latency_ms_total": float(_METRICS["symbol_latency_ms_total"]),
            "symbol_latency_samples": int(_METRICS["symbol_latency_samples"]),
            "batch_requests": int(_METRICS["batch_requests"]),
        }
    persist_metrics_section("analysis", snapshot)


def resolve_scoring_mode(
    requested_mode: str,
    market: str | None = None,
    universe_category: str | None = None,
) -> str:
    """Resolve scoring mode, supporting optional auto-mode mapping."""

    cleaned_mode = requested_mode.lower().strip()
    if cleaned_mode not in SUPPORTED_SCORING_MODES:
        raise ValueError(
            f"Unsupported mode '{requested_mode}'. Choose one of: {', '.join(SUPPORTED_SCORING_MODES)}."
        )
    if cleaned_mode != "auto":
        return cleaned_mode

    category_key = (universe_category or "").lower().strip()
    market_key = (market or "").lower().strip()
    if market_key in SECTOR_MODE_MAP:
        return SECTOR_MODE_MAP[market_key]
    if category_key in SECTOR_MODE_MAP:
        return SECTOR_MODE_MAP[category_key]
    return DEFAULT_SCORING_MODE


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
    """Combine confidence, trend, and market context into a normalized technical rank."""

    confidence = float(signal_data.get("confidence", 0) or 0)
    trend_weight = _trend_weight(str(signal_data.get("trend_strength", "unknown")))
    market_weight = _market_weight(
        str(signal_data.get("signal", "HOLD")),
        str(signal_data.get("market_bias", "neutral")),
    )
    rank = confidence * trend_weight * market_weight
    return round(min(RANK_LIMIT, rank), 2)


def combine_hybrid_rank(technical_rank: float, fundamental_score: float | None) -> float:
    """Combine technical and fundamentals into one interpretable hybrid rank."""

    if fundamental_score is None:
        return round(min(RANK_LIMIT, max(0.0, technical_rank)), 2)
    multiplier = max(0.8, 0.6 + (0.4 * fundamental_score))
    hybrid_rank = technical_rank * multiplier
    return round(min(RANK_LIMIT, max(0.0, hybrid_rank)), 2)


def apply_fundamental_bias_adjustment(rank: float, fundamental_score: float | None) -> float:
    """Apply smooth fundamentals-based rank adjustment."""

    if fundamental_score is None:
        return round(min(RANK_LIMIT, max(0.0, rank)), 2)
    bias_adjustment = (fundamental_score - 0.5) * BIAS_RANK_ADJUSTMENT_MAX
    adjusted_rank = rank * (1.0 + bias_adjustment)
    return round(min(RANK_LIMIT, max(0.0, adjusted_rank)), 2)


def stretch_rank_distribution(rank: float) -> float:
    """Apply non-linear expansion so high ranks separate better."""

    stretched = max(0.0, rank) ** RANK_DISTRIBUTION_EXPONENT
    return round(min(RANK_LIMIT, stretched), 2)


def resolve_fundamental_sector(
    market: str | None,
    universe_category: str | None,
) -> str | None:
    """Resolve the actual sector label used for sector-aware fundamentals."""

    category = (universe_category or "").lower().strip()
    if category in {"core", "sector", "thematic", "experimental"}:
        return (market or "").lower().strip() or None
    return category or (market or "").lower().strip() or None


def distribute_confidence(base_confidence: float) -> float:
    """Apply non-linear confidence scaling to reduce clustering."""

    normalized = abs(base_confidence) / TECHNICAL_MAX_SCORE if TECHNICAL_MAX_SCORE else abs(base_confidence)
    distributed = max(0.0, min(RANK_LIMIT, normalized)) ** CONFIDENCE_DISTRIBUTION_EXPONENT
    return round(min(RANK_LIMIT, distributed), 2)


def classify_investment_type(technical_rank: float | None, fundamental_score: float | None) -> str:
    """Classify the setup into a readable investment type."""

    tech = float(technical_rank or 0)
    fund = float(fundamental_score or 0)
    if tech > 0.7 and fund < 0.4:
        return "short_term_trade"
    if tech > 0.6 and fund > 0.6:
        return "high_conviction"
    if fund > 0.6:
        return "long_term_candidate"
    return "mixed"


def apply_completeness_penalty(value: float, completeness: float | None) -> float:
    """Apply optional penalty when fundamentals completeness is low."""

    if not APPLY_COMPLETENESS_PENALTY or completeness is None:
        return round(min(RANK_LIMIT, max(0.0, value)), 2)
    penalty = (1.0 - completeness) * COMPLETENESS_PENALTY_WEIGHT
    adjusted = value * max(0.0, 1.0 - penalty)
    return round(min(RANK_LIMIT, max(0.0, adjusted)), 2)


def adjusted_confidence(confidence: float, fundamental_score: float | None, completeness: float | None) -> float:
    """Build conviction-aware confidence from technical confidence plus fundamentals."""

    if fundamental_score is None:
        base = confidence
    else:
        base = confidence * (0.7 + (0.3 * fundamental_score))
    return apply_completeness_penalty(base, completeness)


def analyze_symbol_data(
    symbol: str,
    period: str = DEFAULT_PERIOD,
    mode: str = DEFAULT_SCORING_MODE,
    market: str | None = None,
    universe_category: str | None = None,
    debug: bool = False,
    market_context: dict[str, object] | None = None,
) -> dict[str, object]:
    """Run the full analysis pipeline for a single symbol and return structured data."""

    started_at = perf_counter()
    successful = False
    try:
        with _ANALYSIS_SEMAPHORE:
            if market_context is None:
                market_context = resolve_market_context(period)
            raw_data = get_stock_data(symbol, period)
            enriched_data = calculate_indicators(raw_data)
            signal_data = generate_signal(enriched_data, market_context=market_context)
            fundamentals = get_fundamentals(symbol)
            effective_mode = resolve_scoring_mode(mode, market=market, universe_category=universe_category)
            fundamental_sector = str(fundamentals.get("sector") or "").lower().strip() or None
            fundamental_details = score_fundamental_factors(
                fundamentals,
                mode=effective_mode,
                sector=fundamental_sector,
                penalize_missing=PENALIZE_MISSING_FUNDAMENTALS,
                missing_penalty_weight=MISSING_FUNDAMENTALS_PENALTY_WEIGHT,
            )
            technical_rank = normalize_rank(signal_data)
            raw_fundamental_score = fundamental_details.get("fundamental_score")
            fundamental_score = float(raw_fundamental_score) if isinstance(raw_fundamental_score, (int, float)) else None
            fundamental_bias = classify_fundamental_bias(fundamental_score)
            fundamental_completeness_raw = fundamental_details.get("fundamental_completeness")
            fundamental_completeness = (
                float(fundamental_completeness_raw)
                if isinstance(fundamental_completeness_raw, (int, float))
                else None
            )
            base_hybrid_rank = combine_hybrid_rank(
                technical_rank=technical_rank,
                fundamental_score=fundamental_score,
            )
            bias_adjusted_rank = apply_fundamental_bias_adjustment(base_hybrid_rank, fundamental_score)
            stretched_rank = stretch_rank_distribution(bias_adjusted_rank)
            rank = apply_completeness_penalty(stretched_rank, fundamental_completeness)
            rank = round(min(RANK_LIMIT, max(0.0, rank)), 2)
            technical_score = signal_data.get("technical_score", signal_data.get("score"))
            base_technical_confidence = float(signal_data.get("confidence", 0) or 0)
            technical_confidence = distribute_confidence(float(technical_score or 0))
            technical_confidence = min(RANK_LIMIT, round(technical_confidence + (0.05 * technical_rank), 2))
            conviction_confidence = adjusted_confidence(
                confidence=technical_confidence,
                fundamental_score=fundamental_score,
                completeness=fundamental_completeness,
            )
            factors = fundamental_details.get("factors", {})
            result = {
                "symbol": symbol.upper(),
                "period": period,
                "market": market,
                "universe_category": universe_category,
                "fundamental_sector": fundamental_sector,
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
                "score": technical_score,
                "technical_score": technical_score,
                "base_confidence": base_technical_confidence,
                "confidence": technical_confidence,
                "adjusted_confidence": conviction_confidence,
                "confidence_label": signal_data.get("confidence_label"),
                "trend_strength": signal_data.get("trend_strength"),
                "market_bias": signal_data.get("market_bias"),
                "technical_rank": technical_rank,
                "fundamentals": fundamentals,
                "fundamental_score": fundamental_score,
                "fundamental_bias": fundamental_bias,
                "fundamental_mode": effective_mode,
                "fundamental_raw_score": fundamental_details.get("raw_score"),
                "fundamental_risk_scale": fundamental_details.get("risk_scale"),
                "fundamental_factors": factors,
                "fundamental_factor_scores": factors,
                "fundamental_weighted_factor_scores": fundamental_details.get("weighted_factor_scores", {}),
                "fundamental_weights": fundamental_details.get("weights", {}),
                "fundamental_interaction_penalty": fundamental_details.get("interaction_penalty"),
                "fundamental_completeness": fundamental_completeness,
                "missing_fundamentals_ratio": fundamental_details.get("missing_fundamentals_ratio"),
                "missing_fundamentals_fields": fundamental_details.get("missing_fundamentals_fields", []),
                "fundamental_reasons": fundamental_details.get("reasons", []),
                "base_hybrid_rank": base_hybrid_rank,
                "bias_adjusted_rank": bias_adjusted_rank,
                "stretched_rank": stretched_rank,
                "rank": rank,
                "investment_type": classify_investment_type(technical_rank, fundamental_score),
                "confidence_interpretation": confidence_interpretation(signal_data.get("confidence_label")),
                "opportunity_type": classify_opportunity({**signal_data, "rank": rank}),
                "signal": signal_data.get("signal"),
                "reasons": signal_data.get("reasons", []),
            }
            if debug:
                print(
                    result["symbol"],
                    f"sector={fundamental_sector or 'n/a'}",
                    f"risk_scale={fundamental_details.get('risk_scale', 'n/a')}",
                    f"tech={technical_rank:.2f}",
                    f"fund={(fundamental_score if fundamental_score is not None else 0.0):.2f}",
                    f"growth={float(factors.get('growth', 0) or 0):.2f}",
                    f"quality={float(factors.get('quality', 0) or 0):.2f}",
                    f"risk={float(factors.get('risk', 0) or 0):.2f}",
                    f"tech_conf={technical_confidence:.2f}",
                    f"adj_conf={conviction_confidence:.2f}",
                    f"val={float(factors.get('valuation', 0) or 0):.2f}",
                    f"rank={float(result['rank'] or 0):.2f}",
                )
            result["explanation"] = build_explanation(result)
            successful = True
            return result
    finally:
        should_persist = False
        latency_ms = (perf_counter() - started_at) * 1000
        with _METRICS_LOCK:
            global _METRICS_UPDATES
            _METRICS["symbol_requests"] = int(_METRICS["symbol_requests"]) + 1
            _METRICS["symbol_latency_ms_total"] = float(_METRICS["symbol_latency_ms_total"]) + latency_ms
            _METRICS["symbol_latency_samples"] = int(_METRICS["symbol_latency_samples"]) + 1
            if not successful:
                _METRICS["symbol_failures"] = int(_METRICS["symbol_failures"]) + 1
            _METRICS_UPDATES += 1
            should_persist = (_METRICS_UPDATES % _METRICS_PERSIST_EVERY) == 0
        if should_persist:
            _persist_analysis_metrics()


def _safe_analyze_symbol_data(
    symbol: str,
    period: str,
    mode: str,
    market: str | None,
    universe_category: str | None,
    debug: bool,
    market_context: dict[str, object],
) -> dict[str, object]:
    """Analyze one symbol and always return a result payload."""

    try:
        return analyze_symbol_data(
            symbol,
            period,
            mode=mode,
            market=market,
            universe_category=universe_category,
            debug=debug,
            market_context=market_context,
        )
    except (ValueError, RuntimeError) as exc:
        return {"symbol": symbol.upper(), "period": period, "error": str(exc)}


def analyze_symbols_data(
    symbols: Sequence[str],
    period: str = DEFAULT_PERIOD,
    mode: str = DEFAULT_SCORING_MODE,
    market: str | None = None,
    universe_category: str | None = None,
    debug: bool = False,
) -> list[dict[str, object]]:
    """Analyze multiple symbols in parallel and return structured results."""

    symbol_list = [symbol for symbol in symbols if symbol and symbol.strip()]
    with _METRICS_LOCK:
        global _METRICS_UPDATES
        _METRICS["batch_requests"] = int(_METRICS["batch_requests"]) + 1
        _METRICS_UPDATES += 1
    if not symbol_list:
        return []

    market_context = resolve_market_context(period)
    workers = min(MAX_WORKERS, len(symbol_list))
    analyzer = partial(
        _safe_analyze_symbol_data,
        period=period,
        mode=mode,
        market=market,
        universe_category=universe_category,
        debug=debug,
        market_context=market_context,
    )
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
    _persist_analysis_metrics()
    return {
        "symbol_requests": symbol_requests,
        "symbol_failures": symbol_failures,
        "symbol_error_rate": round(error_rate, 4),
        "average_symbol_latency_ms": round(avg_latency, 2),
        "batch_requests": batch_requests,
        "max_workers": MAX_WORKERS,
        "global_analysis_concurrency": GLOBAL_ANALYSIS_CONCURRENCY,
    }
