"""FastAPI application for stock analysis."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, Query

from analysis_service import analyze_symbol_data, analyze_symbols_data
from opportunities import classify_opportunity, is_buy_opportunity, rank_opportunities
from universes import get_universe


app = FastAPI(title="Stock Analysis API", version="1.0.0")


@app.get("/analyze")
async def analyze(symbol: str = Query(..., min_length=1), period: str = Query("1y", min_length=1)) -> dict[str, object]:
    """Analyze a ticker symbol and return structured JSON."""

    try:
        return await asyncio.to_thread(analyze_symbol_data, symbol, period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/batch")
async def batch(symbols: str = Query(..., min_length=1), period: str = Query("1y", min_length=1)) -> dict[str, object]:
    """Analyze multiple comma-separated ticker symbols and return structured JSON."""

    symbol_list = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="At least one symbol is required.")

    return {"results": await asyncio.to_thread(analyze_symbols_data, symbol_list, period)}


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
    results = analyze_symbols_data(symbol_list, period)
    opportunities = [item for item in results if "error" not in item and is_buy_opportunity(item)]
    ranked = rank_opportunities(opportunities)
    for item in ranked:
        item["opportunity_type"] = classify_opportunity(item)
    return {
        "market": market,
        "top": top_n,
        "results": ranked[:top_n],
    }


@app.get("/opportunities")
async def opportunities(
    market: str = Query("sp500"),
    limit: int = Query(5, ge=1, le=50),
    symbols: str | None = Query(default=None),
    period: str = Query("1y", min_length=1),
) -> dict[str, object]:
    """Return the top buy opportunities for a market or custom symbol list."""

    return await asyncio.to_thread(_build_opportunity_results, market, limit, period, symbols)


@app.get("/top")
async def top(
    market: str = Query("sp500"),
    limit: int = Query(5, ge=1, le=50),
    symbols: str | None = Query(default=None),
    period: str = Query("1y", min_length=1),
) -> dict[str, object]:
    """Alias for the opportunities endpoint."""

    return await asyncio.to_thread(_build_opportunity_results, market, limit, period, symbols)


@app.get("/compare")
async def compare(symbols: str = Query(..., min_length=1), period: str = Query("1y", min_length=1)) -> dict[str, object]:
    """Compare multiple symbols and return the best-ranked result."""

    symbol_list = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="At least one symbol is required.")

    results = await asyncio.to_thread(analyze_symbols_data, symbol_list, period)
    ranked = [item for item in results if "error" not in item]
    ranked.sort(key=lambda item: float(item.get("rank", 0) or 0), reverse=True)
    winner = ranked[0] if ranked else None
    return {
        "results": ranked,
        "winner": winner,
    }
