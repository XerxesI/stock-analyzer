"""Fundamental data fetching and scoring for hybrid ranking."""

from __future__ import annotations

from typing import Any

import yfinance as yf

from cache_utils import TTLCache


FUNDAMENTALS_TTL_SECONDS = 3600
_fundamentals_cache: TTLCache[str, dict[str, float | None]] = TTLCache(
    maxsize=512,
    default_ttl_seconds=FUNDAMENTALS_TTL_SECONDS,
    name="fundamentals",
)

_FUNDAMENTAL_KEYS: dict[str, str] = {
    "pe": "trailingPE",
    "forward_pe": "forwardPE",
    "peg": "pegRatio",
    "pb": "priceToBook",
    "roe": "returnOnEquity",
    "profit_margin": "profitMargins",
    "revenue_growth": "revenueGrowth",
    "debt_to_equity": "debtToEquity",
}


def _safe_float(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _fetch_fundamentals(symbol: str) -> dict[str, float | None]:
    try:
        info = yf.Ticker(symbol).info
    except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, KeyError, TypeError):
        return {field: None for field in _FUNDAMENTAL_KEYS}
    if not isinstance(info, dict):
        return {field: None for field in _FUNDAMENTAL_KEYS}
    return {field: _safe_float(info, source_key) for field, source_key in _FUNDAMENTAL_KEYS.items()}


def get_fundamentals(symbol: str) -> dict[str, float | None]:
    """Fetch and normalize selected fundamental metrics for one symbol."""

    cleaned_symbol = symbol.strip().upper()
    if not cleaned_symbol:
        raise ValueError("Symbol must not be empty.")
    snapshot = _fundamentals_cache.get_or_set(cleaned_symbol, lambda: _fetch_fundamentals(cleaned_symbol))
    return dict(snapshot)


def _normalize_score(raw_score: int, max_abs: int = 4) -> float:
    normalized = (raw_score + max_abs) / (2 * max_abs)
    return round(max(0.0, min(1.0, normalized)), 2)


def score_fundamentals(fundamentals: dict[str, float | None]) -> dict[str, object]:
    """Score fundamentals across valuation, growth, quality, and risk."""

    raw_score = 0
    reasons: list[str] = []

    pe = fundamentals.get("pe")
    if pe is None:
        reasons.append("Valuation: P/E unavailable.")
    elif pe < 20:
        raw_score += 1
        reasons.append(f"Valuation: P/E {pe:.2f} is attractive (<20).")
    elif pe > 40:
        raw_score -= 1
        reasons.append(f"Valuation: P/E {pe:.2f} is expensive (>40).")
    else:
        reasons.append(f"Valuation: P/E {pe:.2f} is neutral.")

    revenue_growth = fundamentals.get("revenue_growth")
    if revenue_growth is None:
        reasons.append("Growth: revenue growth unavailable.")
    elif revenue_growth > 0.10:
        raw_score += 1
        reasons.append(f"Growth: revenue growth {revenue_growth:.2%} is strong (>10%).")
    elif revenue_growth < 0:
        raw_score -= 1
        reasons.append(f"Growth: revenue growth {revenue_growth:.2%} is negative.")
    else:
        reasons.append(f"Growth: revenue growth {revenue_growth:.2%} is moderate.")

    roe = fundamentals.get("roe")
    if roe is None:
        reasons.append("Quality: ROE unavailable.")
    elif roe > 0.15:
        raw_score += 1
        reasons.append(f"Quality: ROE {roe:.2%} is strong (>15%).")
    else:
        reasons.append(f"Quality: ROE {roe:.2%} is below preferred threshold.")

    debt_to_equity = fundamentals.get("debt_to_equity")
    if debt_to_equity is None:
        reasons.append("Risk: debt-to-equity unavailable.")
    elif debt_to_equity > 2:
        raw_score -= 1
        reasons.append(f"Risk: debt-to-equity {debt_to_equity:.2f} is elevated (>2).")
    else:
        reasons.append(f"Risk: debt-to-equity {debt_to_equity:.2f} is acceptable.")

    return {
        "fundamental_score": _normalize_score(raw_score),
        "raw_score": raw_score,
        "reasons": reasons,
    }


def get_fundamentals_metrics() -> dict[str, object]:
    """Expose fundamentals cache metrics."""

    return {"cache": _fundamentals_cache.snapshot()}
