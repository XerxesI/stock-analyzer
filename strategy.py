"""Generate trading signals from indicator data."""

from __future__ import annotations

from typing import Any

import pandas as pd

MAX_SCORE = 6.0


def _as_float(value: Any) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


def _trend_label(price: float | None, sma50: float | None, sma200: float | None) -> tuple[int, str, str]:
    """Return weighted trend score, label, and explanation."""

    if price is None or sma50 is None:
        return 0, "unknown", "Trend data is incomplete."

    if sma200 is None:
        if price > sma50:
            return 1, "short-term uptrend", "SMA200 is unavailable, so the short-term trend is above SMA50."
        if price < sma50:
            return -1, "short-term downtrend", "SMA200 is unavailable, so the short-term trend is below SMA50."
        return 0, "sideways", "SMA200 is unavailable and price is near SMA50, so the trend is mixed."

    if price > sma50 and sma50 > sma200:
        return 2, "strong uptrend", "Price is above SMA50 and SMA50 is above SMA200."
    if price > sma50:
        return 1, "weak uptrend", "Price is above SMA50, but the longer trend is not fully confirmed."
    if price < sma50 and sma50 < sma200:
        return -2, "downtrend", "Price is below SMA50 and SMA50 is below SMA200."
    if price < sma50:
        return -1, "weak downtrend", "Price is below SMA50, but the longer trend is not fully confirmed."
    return 0, "sideways", "Price is close to SMA50, so the trend is mixed."


def generate_signal(df: pd.DataFrame, market_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generate a BUY, SELL, or HOLD signal from the last row of ``df``."""

    if df.empty:
        raise ValueError("Input data is empty.")

    last_row = df.iloc[-1]
    price = _as_float(last_row.get("Close"))
    rsi = _as_float(last_row.get("RSI"))
    sma50 = _as_float(last_row.get("SMA50"))
    sma200 = _as_float(last_row.get("SMA200"))
    macd = _as_float(last_row.get("MACD"))
    macd_signal = _as_float(last_row.get("MACD_SIGNAL"))
    macd_hist = _as_float(last_row.get("MACD_HIST"))
    volume = _as_float(last_row.get("Volume"))
    volume_sma20 = _as_float(last_row.get("VOLUME_SMA20"))

    if price is None:
        raise ValueError("Latest row is missing Close data.")
    if rsi is None or sma50 is None:
        return {
            "signal": "HOLD",
            "score": 0,
            "technical_score": 0,
            "confidence": 0.0,
            "confidence_label": "low",
            "rsi": rsi,
            "price": price,
            "sma50": sma50,
            "sma200": sma200,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "volume": volume,
            "volume_sma20": volume_sma20,
            "trend_strength": "unknown",
            "reasons": ["Insufficient indicator data for a signal yet."],
            "decision_summary": "Insufficient indicator data for a signal yet.",
            "market_bias": "neutral",
        }

    technical_score = 0
    reasons: list[str] = []
    market_bias = "neutral"

    if rsi < 30:
        technical_score += 1
        reasons.append(f"RSI is oversold at {rsi:.1f}.")
    elif rsi > 70:
        technical_score -= 1
        reasons.append(f"RSI is overbought at {rsi:.1f}.")
    else:
        reasons.append(f"RSI is neutral at {rsi:.1f}.")

    trend_score, trend_strength, trend_reason = _trend_label(price, sma50, sma200)
    technical_score += trend_score
    reasons.append(trend_reason)

    if macd is not None and macd_signal is not None:
        if macd > macd_signal and (macd_hist is None or macd_hist >= 0):
            technical_score += 2
            reasons.append("MACD confirms bullish momentum.")
        elif macd > macd_signal:
            technical_score += 1
            reasons.append("MACD is slightly bullish.")
        elif macd < macd_signal and (macd_hist is None or macd_hist <= 0):
            technical_score -= 2
            reasons.append("MACD confirms bearish momentum.")
        elif macd < macd_signal:
            technical_score -= 1
            reasons.append("MACD is slightly bearish.")
        else:
            reasons.append("MACD is flat.")

    if volume is not None and volume_sma20 is not None:
        if volume > volume_sma20:
            technical_score += 1
            reasons.append("Volume is above its 20-day average and confirms participation.")
        else:
            reasons.append("Volume is below its 20-day average, so it does not confirm the move.")

    if market_context is not None:
        market_bias = str(market_context.get("bias", "neutral"))
        if market_bias == "bullish" and technical_score > 0:
            reasons.append("Bullish market context from SPY supports this bullish setup.")
        elif market_bias == "bearish" and technical_score < 0:
            reasons.append("Bearish market context from SPY supports this bearish setup.")
        elif market_bias == "bearish" and technical_score > 0:
            reasons.append("Bearish market context from SPY tempers upside conviction.")
        elif market_bias == "bullish" and technical_score < 0:
            reasons.append("Bullish market context from SPY tempers downside conviction.")

    confidence = min(1.0, (abs(technical_score) / MAX_SCORE) ** 1.3)
    if confidence < 0.3:
        confidence_label = "low"
    elif confidence < 0.7:
        confidence_label = "medium"
    else:
        confidence_label = "high"

    if technical_score >= 4 and confidence_label == "high":
        signal = "STRONG BUY"
    elif technical_score >= 2:
        signal = "BUY"
    elif technical_score <= -4 and confidence_label == "high":
        signal = "STRONG SELL"
    elif technical_score <= -2:
        signal = "SELL"
    else:
        signal = "HOLD"

    if signal == "BUY":
        decision_summary = "Bullish factors outweigh the weaker bearish ones."
    elif signal == "STRONG BUY":
        decision_summary = "Bullish factors strongly outweigh the bearish ones."
    elif signal == "SELL":
        decision_summary = "Bearish factors outweigh the weaker bullish ones."
    elif signal == "STRONG SELL":
        decision_summary = "Bearish factors strongly outweigh the bullish ones."
    else:
        decision_summary = "Signals are mixed, so the setup remains inconclusive."

    return {
        "signal": signal,
        "score": technical_score,
        "technical_score": technical_score,
        "confidence": round(confidence, 2),
        "confidence_label": confidence_label,
        "rsi": rsi,
        "price": price,
        "sma50": sma50,
        "sma200": sma200,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "volume": volume,
        "volume_sma20": volume_sma20,
        "trend_strength": trend_strength,
        "reasons": reasons,
        "decision_summary": decision_summary,
        "market_bias": market_bias,
    }


def apply_universe_weight(score: float, category: str) -> float:
    """Apply universe category weighting to a score.
    
    Weights:
    - thematic: 1.2x (growth-focused, expect higher scores)
    - sector: 1.0x (baseline)
    - core: 0.8x (baseline markets, more conservative)
    - experimental: 0.6x (unreliable, penalize)
    """
    weights = {
        "thematic": 1.2,
        "sector": 1.0,
        "core": 0.8,
        "experimental": 0.6,
    }
    weight = weights.get(category.lower(), 1.0)
    return score * weight
