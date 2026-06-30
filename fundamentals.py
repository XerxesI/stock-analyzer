"""Fundamental data fetching and scoring for hybrid ranking."""

from __future__ import annotations

import math
from typing import Any

import yfinance as yf

from cache_utils import TTLCache


FUNDAMENTALS_TTL_SECONDS = 86400
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


def _normalize_score(raw_score: float, max_abs: int = 4) -> float:
    normalized = (raw_score + max_abs) / (2 * max_abs)
    return round(max(0.0, min(1.0, normalized)), 2)


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_fundamentals(
    fundamentals: dict[str, float | None],
    penalize_missing: bool = False,
    missing_penalty_weight: float = 0.15,
) -> dict[str, object]:
    """Score fundamentals across valuation, growth, quality, and risk."""

    factor_scores: dict[str, float] = {
        "valuation": 0.0,
        "growth": 0.0,
        "quality": 0.0,
        "risk": 0.0,
    }
    reasons: list[str] = []

    pe = fundamentals.get("pe")
    if pe is None:
        reasons.append("Valuation: P/E unavailable.")
    else:
        factor_scores["valuation"] = _clamp((20.0 - pe) / 20.0)
        reasons.append(f"Valuation: P/E {pe:.2f} scored {factor_scores['valuation']:+.2f}.")

    revenue_growth = fundamentals.get("revenue_growth")
    if revenue_growth is None:
        reasons.append("Growth: revenue growth unavailable.")
    else:
        factor_scores["growth"] = _clamp(math.tanh(revenue_growth * 3.0))
        reasons.append(f"Growth: revenue growth {revenue_growth:.2%} scored {factor_scores['growth']:+.2f}.")

    roe = fundamentals.get("roe")
    if roe is None:
        reasons.append("Quality: ROE unavailable.")
    else:
        factor_scores["quality"] = _clamp((roe - 0.15) / 0.15)
        reasons.append(f"Quality: ROE {roe:.2%} scored {factor_scores['quality']:+.2f}.")

    debt_to_equity = fundamentals.get("debt_to_equity")
    if debt_to_equity is None:
        reasons.append("Risk: debt-to-equity unavailable.")
    else:
        factor_scores["risk"] = _clamp((2.0 - debt_to_equity) / 2.0)
        reasons.append(f"Risk: debt-to-equity {debt_to_equity:.2f} scored {factor_scores['risk']:+.2f}.")

    raw_score = sum(factor_scores.values())
    missing_fields = [key for key, value in fundamentals.items() if value is None]
    missing_ratio = len(missing_fields) / len(_FUNDAMENTAL_KEYS)
    fundamental_score = _normalize_score(raw_score)
    if penalize_missing and missing_ratio > 0:
        fundamental_score = round(max(0.0, fundamental_score - (missing_ratio * missing_penalty_weight)), 2)
        reasons.append(
            f"Missing-data penalty applied ({missing_ratio:.0%} missing, weight {missing_penalty_weight:.2f})."
        )
    else:
        reasons.append(f"Missing fundamentals: {len(missing_fields)}/{len(_FUNDAMENTAL_KEYS)} ({missing_ratio:.0%}).")

    return {
        "fundamental_score": fundamental_score,
        "raw_score": round(raw_score, 2),
        "factor_scores": factor_scores,
        "missing_fundamentals_ratio": round(missing_ratio, 2),
        "missing_fundamentals_fields": missing_fields,
        "reasons": reasons,
    }


def get_fundamentals_metrics() -> dict[str, object]:
    """Expose fundamentals cache metrics."""

    return {"cache": _fundamentals_cache.snapshot()}
