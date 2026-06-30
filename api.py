"""FastAPI application for stock analysis."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from threading import Lock
from time import perf_counter
from typing import Callable

from fastapi import FastAPI, HTTPException, Query

from analysis_service import analyze_symbol_data, analyze_symbols_data, get_analysis_metrics
from cache_utils import TTLCache
from data_fetcher import get_fetcher_metrics
from market_context import get_market_context_metrics
from metrics_store import load_metrics_section, metrics_store_path, persist_metrics_section
from opportunity_service import analyze_and_rank_opportunities, rank_analysis_results
from universes import get_universe


app = FastAPI(title="Stock Analysis API", version="1.0.0")
API_CACHE_TTL_SECONDS = 45
_response_cache: TTLCache[tuple[str, tuple[object, ...]], dict[str, object]] = TTLCache(
    maxsize=512,
    default_ttl_seconds=API_CACHE_TTL_SECONDS,
    name="api_response",
)
_API_METRICS_LOCK = Lock()
_API_METRICS: dict[str, float | int] = {
    "requests": 0,
    "failures": 0,
    "latency_ms_total": 0.0,
}
_API_METRICS.update(load_metrics_section("api", _API_METRICS))
_API_METRICS_UPDATES = 0
_API_METRICS_PERSIST_EVERY = 10


def _persist_api_metrics() -> None:
    with _API_METRICS_LOCK:
        snapshot = {
            "requests": int(_API_METRICS["requests"]),
            "failures": int(_API_METRICS["failures"]),
            "latency_ms_total": float(_API_METRICS["latency_ms_total"]),
        }
    persist_metrics_section("api", snapshot)


def _record_api_request(successful: bool, latency_ms: float) -> None:
    should_persist = False
    with _API_METRICS_LOCK:
        global _API_METRICS_UPDATES
        _API_METRICS["requests"] = int(_API_METRICS["requests"]) + 1
        _API_METRICS["latency_ms_total"] = float(_API_METRICS["latency_ms_total"]) + latency_ms
        if not successful:
            _API_METRICS["failures"] = int(_API_METRICS["failures"]) + 1
        _API_METRICS_UPDATES += 1
        should_persist = (_API_METRICS_UPDATES % _API_METRICS_PERSIST_EVERY) == 0
    if should_persist:
        _persist_api_metrics()


def _api_metrics_snapshot() -> dict[str, float | int | dict[str, float | int | str]]:
    with _API_METRICS_LOCK:
        requests = int(_API_METRICS["requests"])
        failures = int(_API_METRICS["failures"])
        latency_ms_total = float(_API_METRICS["latency_ms_total"])
    avg_latency_ms = (latency_ms_total / requests) if requests else 0.0
    error_rate = (failures / requests) if requests else 0.0
    return {
        "requests": requests,
        "failures": failures,
        "error_rate": round(error_rate, 4),
        "average_latency_ms": round(avg_latency_ms, 2),
        "cache": _response_cache.snapshot(),
    }


def _cached_response(
    namespace: str,
    cache_key_parts: tuple[object, ...],
    builder: Callable[[], dict[str, object]],
) -> dict[str, object]:
    cached_value = _response_cache.get_or_set((namespace, cache_key_parts), builder)
    return deepcopy(cached_value)


def _build_analyze_response(symbol: str, period: str) -> dict[str, object]:
    normalized_symbol = symbol.strip().upper()
    normalized_period = period.strip()
    return _cached_response(
        "analyze",
        (normalized_symbol, normalized_period),
        lambda: analyze_symbol_data(normalized_symbol, normalized_period),
    )


def _build_batch_response(symbol_list: list[str], period: str) -> dict[str, object]:
    normalized_symbols = tuple(symbol.upper() for symbol in symbol_list)
    normalized_period = period.strip()
    return _cached_response(
        "batch",
        (normalized_symbols, normalized_period),
        lambda: {"results": analyze_symbols_data(normalized_symbols, normalized_period)},
    )


@app.get("/analyze")
async def analyze(symbol: str = Query(..., min_length=1), period: str = Query("1y", min_length=1)) -> dict[str, object]:
    """Analyze a ticker symbol and return structured JSON."""

    started_at = perf_counter()
    successful = False
    try:
        response = await asyncio.to_thread(_build_analyze_response, symbol, period)
        successful = True
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        _record_api_request(successful, (perf_counter() - started_at) * 1000)


@app.get("/batch")
async def batch(symbols: str = Query(..., min_length=1), period: str = Query("1y", min_length=1)) -> dict[str, object]:
    """Analyze multiple comma-separated ticker symbols and return structured JSON."""

    started_at = perf_counter()
    successful = False
    symbol_list = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    if not symbol_list:
        _record_api_request(False, (perf_counter() - started_at) * 1000)
        raise HTTPException(status_code=400, detail="At least one symbol is required.")

    try:
        response = await asyncio.to_thread(_build_batch_response, symbol_list, period)
        successful = True
        return response
    finally:
        _record_api_request(successful, (perf_counter() - started_at) * 1000)


def _build_opportunity_results(
    market: str,
    top_n: int,
    period: str,
    symbols: str | None,
) -> dict[str, object]:
    try:
        symbol_list = get_universe(
            market,
            [] if symbols is None else [symbol.strip() for symbol in symbols.split(",") if symbol.strip()],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    normalized_symbols = tuple(str(symbol).upper() for symbol in symbol_list)
    normalized_period = period.strip()
    return _cached_response(
        "opportunities",
        (market, top_n, normalized_symbols, normalized_period),
        lambda: {
            "market": market,
            "top": top_n,
            "results": analyze_and_rank_opportunities(normalized_symbols, normalized_period, top_n=top_n),
        },
    )


@app.get("/opportunities")
async def opportunities(
    market: str = Query("sp500"),
    limit: int = Query(5, ge=1, le=50),
    symbols: str | None = Query(default=None),
    period: str = Query("1y", min_length=1),
) -> dict[str, object]:
    """Return the top buy opportunities for a market or custom symbol list."""

    started_at = perf_counter()
    successful = False
    try:
        response = await asyncio.to_thread(_build_opportunity_results, market, limit, period, symbols)
        successful = True
        return response
    finally:
        _record_api_request(successful, (perf_counter() - started_at) * 1000)


@app.get("/top")
async def top(
    market: str = Query("sp500"),
    limit: int = Query(5, ge=1, le=50),
    symbols: str | None = Query(default=None),
    period: str = Query("1y", min_length=1),
) -> dict[str, object]:
    """Alias for the opportunities endpoint."""

    started_at = perf_counter()
    successful = False
    try:
        response = await asyncio.to_thread(_build_opportunity_results, market, limit, period, symbols)
        successful = True
        return response
    finally:
        _record_api_request(successful, (perf_counter() - started_at) * 1000)


@app.get("/compare")
async def compare(symbols: str = Query(..., min_length=1), period: str = Query("1y", min_length=1)) -> dict[str, object]:
    """Compare multiple symbols and return the best-ranked result."""

    started_at = perf_counter()
    successful = False
    symbol_list = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    if not symbol_list:
        _record_api_request(False, (perf_counter() - started_at) * 1000)
        raise HTTPException(status_code=400, detail="At least one symbol is required.")

    normalized_symbols = tuple(symbol.upper() for symbol in symbol_list)
    normalized_period = period.strip()
    try:
        response = await asyncio.to_thread(
            _cached_response,
            "compare",
            (normalized_symbols, normalized_period),
            lambda: {
                "results": rank_analysis_results(analyze_symbols_data(normalized_symbols, normalized_period)),
                "winner": None,
            },
        )
        if response["results"]:
            response["winner"] = response["results"][0]
        successful = True
        return response
    finally:
        _record_api_request(successful, (perf_counter() - started_at) * 1000)


@app.get("/metrics")
async def metrics() -> dict[str, object]:
    """Return runtime metrics for API, analysis pipeline, and cache behavior."""

    _persist_api_metrics()
    return {
        "metrics_store_path": metrics_store_path(),
        "api": _api_metrics_snapshot(),
        "analysis": get_analysis_metrics(),
        "data_fetcher": get_fetcher_metrics(),
        "market_context": get_market_context_metrics(),
    }
