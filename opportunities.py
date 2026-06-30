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
    min_growth_score: float | None = None,
    max_risk_score: float | None = None,
    debug: bool = False,
) -> bool:
    """Return True when a result is worth surfacing as a buy opportunity."""

    signal = str(item.get("signal", "HOLD"))
    technical_confidence = float(item.get("confidence", 0) or 0)
    adjusted_confidence = float(item.get("adjusted_confidence", technical_confidence) or 0)
    rank = float(item.get("rank", 0) or 0)
    if debug:
        factors = item.get("fundamental_factors", item.get("fundamental_factor_scores", {}))
        print(
            str(item.get("symbol", "UNKNOWN")),
            f"tech_conf={technical_confidence:.2f}",
            f"adj_conf={adjusted_confidence:.2f}",
            f"fund={float(item.get('fundamental_score', 0) or 0):.2f}",
            f"growth={float((factors or {}).get('growth', 0) or 0):.2f}",
            f"val={float((factors or {}).get('valuation', 0) or 0):.2f}",
            f"rank={rank:.2f}",
        )
    if signal not in BUY_SIGNALS or technical_confidence < min_confidence or rank < min_rank:
        return False
    if min_fundamental_score is None:
        pass
    else:
        fundamental_score = float(item.get("fundamental_score", 0) or 0)
        if fundamental_score < min_fundamental_score:
            return False
    factors = item.get("fundamental_factors", item.get("fundamental_factor_scores", {}))
    if isinstance(factors, dict):
        if min_growth_score is not None and float(factors.get("growth", 0) or 0) < min_growth_score:
            return False
        if max_risk_score is not None and float(factors.get("risk", 1) or 1) > max_risk_score:
            return False
    fundamental_score = float(item.get("fundamental_score", 0) or 0)
    return min_fundamental_score is None or fundamental_score >= min_fundamental_score


def rank_opportunities(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort opportunities by normalized rank and confidence."""

    return sorted(
        items,
        key=lambda item: (
            float(item.get("rank", 0) or 0),
            float(item.get("adjusted_confidence", item.get("confidence", 0)) or 0),
        ),
        reverse=True,
    )
