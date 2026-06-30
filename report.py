"""Build human-readable analysis text."""

from __future__ import annotations

from typing import Any


def build_explanation(signal_data: dict[str, Any]) -> str:
    """Turn signal data into a concise, human-readable explanation."""

    signal = signal_data.get("signal", "HOLD")
    score = signal_data.get("score")
    confidence = signal_data.get("confidence")
    confidence_label = signal_data.get("confidence_label")
    confidence_interpretation = signal_data.get("confidence_interpretation")
    trend_strength = signal_data.get("trend_strength")
    market_bias = signal_data.get("market_bias")
    market_context_error = signal_data.get("market_context_error")
    rank = signal_data.get("rank")
    opportunity_type = signal_data.get("opportunity_type")
    reasons = signal_data.get("reasons") or []
    rsi = signal_data.get("rsi")
    price = signal_data.get("price")
    sma50 = signal_data.get("sma50")
    macd = signal_data.get("macd")
    macd_signal = signal_data.get("macd_signal")
    volume = signal_data.get("volume")
    volume_sma20 = signal_data.get("volume_sma20")

    lines = []

    if rsi is not None:
        if rsi < 30:
            lines.append(f"RSI is {rsi:.1f}, indicating oversold conditions.")
        elif rsi > 70:
            lines.append(f"RSI is {rsi:.1f}, indicating overbought conditions.")
        else:
            lines.append(f"RSI is {rsi:.1f}, indicating neutral momentum and no strong directional edge.")

    if price is not None and sma50 is not None:
        if price > sma50:
            lines.append(f"Price is above the 50-day average at {sma50:.2f}.")
        else:
            lines.append(f"Price is below the 50-day average at {sma50:.2f}.")

    if trend_strength and trend_strength != "unknown":
        lines.append(f"Trend strength: {trend_strength}.")

    if market_bias:
        lines.append(f"Market bias from SPY: {market_bias}.")

    if market_context_error:
        lines.append(f"Market context note: {market_context_error}")

    if macd is not None and macd_signal is not None:
        if macd > macd_signal:
            lines.append("MACD confirms bullish momentum.")
        elif macd < macd_signal:
            lines.append("MACD confirms bearish momentum.")
        else:
            lines.append("MACD is flat.")

    if volume is not None and volume_sma20 is not None:
        if volume > volume_sma20:
            lines.append("Volume is above its 20-day average, confirming stronger participation.")
        else:
            lines.append("Volume is below its 20-day average, suggesting weaker participation.")

    if score is not None:
        if confidence is not None:
            if confidence_label:
                lines.append(
                    f"Score: {score}. Confidence: {float(confidence):.2f} ({confidence_label}). Final signal: {signal}."
                )
            else:
                lines.append(f"Score: {score}. Confidence: {float(confidence):.2f}. Final signal: {signal}.")
        else:
            lines.append(f"Score: {score}. Final signal: {signal}.")

    if rank is not None:
        lines.append(f"Rank score: {float(rank):.2f}.")

    if opportunity_type:
        lines.append(f"Opportunity type: {opportunity_type}.")

    if confidence_interpretation:
        lines.append(f"Confidence interpretation: {confidence_interpretation}.")

    if signal == "STRONG BUY":
        lines.append("Conclusion: bullish factors strongly outweigh the bearish ones.")
    elif signal == "BUY":
        lines.append("Conclusion: bullish factors outweigh the weaker bearish ones.")
    elif signal == "STRONG SELL":
        lines.append("Conclusion: bearish factors strongly outweigh the bullish ones.")
    elif signal == "SELL":
        lines.append("Conclusion: bearish factors outweigh the weaker bullish ones.")
    else:
        lines.append("Conclusion: signals are mixed, so HOLD is recommended.")

    if reasons:
        lines.append("Key factors: " + " ".join(str(reason) for reason in reasons))

    return "\n".join(lines) if lines else "No clear signal was generated."
