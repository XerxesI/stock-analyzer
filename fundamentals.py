"""Fundamental data fetching and scoring for hybrid ranking."""

from __future__ import annotations

import logging
import math
from typing import Any

import yfinance as yf

from cache_utils import TTLCache


LOGGER = logging.getLogger(__name__)

FUNDAMENTALS_TTL_SECONDS = 86400
_fundamentals_cache: TTLCache[str, dict[str, Any]] = TTLCache(
    maxsize=512,
    default_ttl_seconds=FUNDAMENTALS_TTL_SECONDS,
    name="fundamentals",
)

# Only fields actually consumed by ``score_fundamental_factors`` are fetched, so
# the missing-data ratio reflects fields we truly rely on rather than fetched-but-ignored ones.
_FUNDAMENTAL_KEYS: dict[str, str] = {
    "pe": "trailingPE",
    "roe": "returnOnEquity",
    "profit_margin": "profitMargins",
    "revenue_growth": "revenueGrowth",
    "debt_to_equity": "debtToEquity",
}
SUPPORTED_SCORING_MODES = ("growth", "balanced", "defensive")
# Debt-to-equity from Yahoo is expressed as a percentage (e.g. MSFT ~30, NBIS ~132),
# so each scale is the upper bound above which the risk factor saturates to 0.
# Higher scale => more leverage tolerated before the risk factor bottoms out.
SECTOR_RISK_SCALES = {
    "energy": 300,
    "utilities": 300,
    "real_estate": 300,
    "financial": 200,
    "technology": 150,
    "communication": 150,
    "consumer_discretionary": 150,
    "industrials": 150,
    "materials": 150,
    "healthcare": 100,
    "consumer_staples": 100,
    "default": 150,
}
# Ordered keyword matches against the lowercased Yahoo sector label. First hit wins,
# so more specific groups (real estate, consumer defensive/cyclical) precede broader ones.
_SECTOR_RISK_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("energy", "oil", "gas"), "energy"),
    (("utility", "utilities"), "utilities"),
    (("real estate", "reit"), "real_estate"),
    (("financial", "bank", "insurance"), "financial"),
    (("consumer defensive", "consumer staples", "staples"), "consumer_staples"),
    (("consumer cyclical", "consumer discretionary", "discretionary", "retail"), "consumer_discretionary"),
    (("healthcare", "health", "pharma", "biotech", "medical"), "healthcare"),
    (("technology", "software", "semiconductor", "tech"), "technology"),
    (("communication", "telecom", "media"), "communication"),
    (("industrial",), "industrials"),
    (("materials", "material", "mining", "chemical"), "materials"),
)
_MODE_WEIGHTS: dict[str, dict[str, float]] = {
    "growth": {"valuation": 0.5, "growth": 1.5, "quality": 1.0, "risk": 0.5},
    "balanced": {"valuation": 1.0, "growth": 1.0, "quality": 1.0, "risk": 1.0},
    "defensive": {"valuation": 1.2, "growth": 0.5, "quality": 1.2, "risk": 1.5},
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


def extract_real_sector(info: dict[str, Any] | None) -> str:
    """Extract the real sector from a Yahoo Finance info payload."""

    if not info:
        return ""
    return str(info.get("sector") or "").lower().strip()


def _fetch_fundamentals(symbol: str) -> dict[str, Any]:
    try:
        info = yf.Ticker(symbol).info
    except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        # Previously swallowed silently, which let a transient failure masquerade
        # downstream as "no sector" (and then as a universe-category pseudo-sector).
        LOGGER.warning("Fundamentals fetch failed for %s: %s: %s", symbol, type(exc).__name__, exc)
        return {field: None for field in _FUNDAMENTAL_KEYS} | {"sector": ""}
    if not isinstance(info, dict):
        LOGGER.warning("Fundamentals fetch for %s returned no usable info payload.", symbol)
        return {field: None for field in _FUNDAMENTAL_KEYS} | {"sector": ""}
    fundamentals = {field: _safe_float(info, source_key) for field, source_key in _FUNDAMENTAL_KEYS.items()}
    fundamentals["sector"] = extract_real_sector(info)
    if not fundamentals["sector"]:
        LOGGER.warning("No sector reported for %s; downstream sector grouping will fall back to 'unknown'.", symbol)
    return fundamentals


def get_fundamentals(symbol: str) -> dict[str, Any]:
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


def _sector_risk_scale(sector: str | None) -> int:
    sector_name = (sector or "").lower().strip()
    if not sector_name:
        return SECTOR_RISK_SCALES["default"]
    for keywords, scale_key in _SECTOR_RISK_KEYWORDS:
        if any(keyword in sector_name for keyword in keywords):
            return SECTOR_RISK_SCALES[scale_key]
    return SECTOR_RISK_SCALES["default"]


def _normalize(value: float | None, low: float, high: float) -> float:
    if value is None:
        return 0.5
    if high <= low:
        raise ValueError("high must be greater than low.")
    scaled = (value - low) / (high - low)
    return round(max(0.0, min(1.0, scaled)), 2)


def _normalize_inverse(value: float | None, low: float, high: float) -> float:
    if value is None:
        return 0.5
    return round(1.0 - _normalize(value, low, high), 2)


def classify_fundamental_bias(
    score: float | None,
    bullish_threshold: float = 0.65,
    bearish_threshold: float = 0.35,
) -> str:
    """Classify fundamental bias from normalized fundamental score."""

    if score is None:
        return "neutral"
    if score >= bullish_threshold:
        return "bullish"
    if score <= bearish_threshold:
        return "bearish"
    return "neutral"


def score_fundamental_factors(
    fundamentals: dict[str, Any],
    mode: str = "balanced",
    sector: str | None = None,
    penalize_missing: bool = False,
    missing_penalty_weight: float = 0.15,
) -> dict[str, object]:
    """Score fundamentals as valuation/growth/quality/risk factors."""

    effective_mode = mode.lower().strip()
    if effective_mode not in SUPPORTED_SCORING_MODES:
        raise ValueError(
            f"Unsupported fundamentals scoring mode '{mode}'. Choose one of: {', '.join(SUPPORTED_SCORING_MODES)}."
        )
    weights = _MODE_WEIGHTS[effective_mode]

    reasons: list[str] = []

    pe = fundamentals.get("pe")
    revenue_growth = fundamentals.get("revenue_growth")
    roe = fundamentals.get("roe")
    profit_margin = fundamentals.get("profit_margin")
    debt_to_equity = fundamentals.get("debt_to_equity")

    sector_scale = _sector_risk_scale(sector)
    factor_scores: dict[str, float] = {
        "valuation": _normalize_inverse(pe, low=10.0, high=40.0),
        "growth": _normalize(revenue_growth, low=-0.1, high=0.3),
        "quality": round((0.6 * _normalize(roe, low=0.05, high=0.25)) + (0.4 * _normalize(profit_margin, low=0.05, high=0.3)), 2),
        "risk": _normalize_inverse(debt_to_equity, low=0.0, high=float(sector_scale)),
    }

    reasons.append(f"Valuation factor: {factor_scores['valuation']:.2f} (P/E={pe if pe is not None else 'N/A'}).")
    reasons.append(
        f"Growth factor: {factor_scores['growth']:.2f} (rev growth={f'{revenue_growth:.2%}' if revenue_growth is not None else 'N/A'})."
    )
    reasons.append(
        f"Quality factor: {factor_scores['quality']:.2f} (ROE={f'{roe:.2%}' if roe is not None else 'N/A'}, margin={f'{profit_margin:.2%}' if profit_margin is not None else 'N/A'})."
    )
    reasons.append(
        f"Risk factor: {factor_scores['risk']:.2f} (debt/equity={f'{debt_to_equity:.2f}' if debt_to_equity is not None else 'N/A'}, scale={sector_scale})."
    )

    weighted_scores = {factor: factor_scores[factor] * weights[factor] for factor in factor_scores}
    total_weight = sum(weights.values())
    weighted_score = sum(weighted_scores.values()) / total_weight if total_weight else 0.5
    interaction_penalty = 0.0
    if factor_scores["growth"] > 0.7 and factor_scores["risk"] < 0.3:
        interaction_penalty -= 0.1
        reasons.append("Interaction penalty: high growth with elevated financial risk.")
    if factor_scores["valuation"] > 0.7 and factor_scores["quality"] < 0.3:
        interaction_penalty -= 0.1
        reasons.append("Interaction penalty: possible value trap (cheap but weak quality).")
    if factor_scores["growth"] > 0.7 and factor_scores["quality"] > 0.7:
        interaction_penalty += 0.05
        reasons.append("Interaction bonus: strong growth and high quality align.")
    weighted_score = max(0.0, min(1.0, weighted_score + interaction_penalty))
    missing_fields = [key for key, value in fundamentals.items() if value is None]
    missing_ratio = len(missing_fields) / len(_FUNDAMENTAL_KEYS)
    fundamental_score = round(max(0.0, min(1.0, weighted_score)), 2)
    if penalize_missing and missing_ratio > 0:
        fundamental_score = round(max(0.0, fundamental_score - (missing_ratio * missing_penalty_weight)), 2)
        reasons.append(
            f"Missing-data penalty applied ({missing_ratio:.0%} missing, weight {missing_penalty_weight:.2f})."
        )
    else:
        reasons.append(f"Missing fundamentals: {len(missing_fields)}/{len(_FUNDAMENTAL_KEYS)} ({missing_ratio:.0%}).")

    return {
        "mode": effective_mode,
        "sector": (sector or "").lower().strip() or None,
        "risk_scale": sector_scale,
        "fundamental_score": fundamental_score,
        "raw_score": round(weighted_score, 4),
        "factors": factor_scores,
        "weighted_factor_scores": weighted_scores,
        "weights": weights,
        "interaction_penalty": round(interaction_penalty, 2),
        "missing_fundamentals_ratio": round(missing_ratio, 2),
        "fundamental_completeness": round(1.0 - missing_ratio, 2),
        "missing_fundamentals_fields": missing_fields,
        "reasons": reasons,
    }


def score_fundamentals(
    fundamentals: dict[str, Any],
    mode: str = "balanced",
    sector: str | None = None,
    penalize_missing: bool = False,
    missing_penalty_weight: float = 0.15,
) -> dict[str, object]:
    """Backward-compatible wrapper for factor scoring."""

    return score_fundamental_factors(
        fundamentals,
        mode=mode,
        sector=sector,
        penalize_missing=penalize_missing,
        missing_penalty_weight=missing_penalty_weight,
    )


def get_fundamentals_metrics() -> dict[str, object]:
    """Expose fundamentals cache metrics."""

    return {"cache": _fundamentals_cache.snapshot()}
