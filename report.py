"""Build human-readable analysis text."""

from __future__ import annotations

from typing import Any


def build_explanation(signal_data: dict[str, Any]) -> str:
    """Turn signal data into a concise, human-readable explanation."""

    signal = signal_data.get("signal", "HOLD")
    score = signal_data.get("score")
    confidence = signal_data.get("confidence")
    adjusted_confidence = signal_data.get("adjusted_confidence")
    confidence_label = signal_data.get("confidence_label")
    confidence_interpretation = signal_data.get("confidence_interpretation")
    trend_strength = signal_data.get("trend_strength")
    market_bias = signal_data.get("market_bias")
    market_context_error = signal_data.get("market_context_error")
    rank = signal_data.get("rank")
    opportunity_type = signal_data.get("opportunity_type")
    investment_type = signal_data.get("investment_type")
    reasons = signal_data.get("reasons") or []
    rsi = signal_data.get("rsi")
    price = signal_data.get("price")
    sma50 = signal_data.get("sma50")
    macd = signal_data.get("macd")
    macd_signal = signal_data.get("macd_signal")
    volume = signal_data.get("volume")
    volume_sma20 = signal_data.get("volume_sma20")
    technical_rank = signal_data.get("technical_rank")
    fundamental_score = signal_data.get("fundamental_score")
    fundamental_bias = signal_data.get("fundamental_bias")
    base_hybrid_rank = signal_data.get("base_hybrid_rank")
    bias_adjusted_rank = signal_data.get("bias_adjusted_rank")
    fundamental_raw_score = signal_data.get("fundamental_raw_score")
    fundamental_factor_scores = (
        signal_data.get("fundamental_factors")
        or signal_data.get("fundamental_factor_scores")
        or {}
    )
    fundamental_completeness = signal_data.get("fundamental_completeness")
    fundamental_interaction_penalty = signal_data.get("fundamental_interaction_penalty")
    missing_fundamentals_ratio = signal_data.get("missing_fundamentals_ratio")
    missing_fundamentals_fields = signal_data.get("missing_fundamentals_fields") or []
    fundamental_reasons = signal_data.get("fundamental_reasons") or []
    fundamentals = signal_data.get("fundamentals") or {}
    market = str(signal_data.get("market", "")).lower()
    sector = str(signal_data.get("universe_category", "")).lower()

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
    if isinstance(adjusted_confidence, (int, float)):
        lines.append(f"Adjusted confidence (fundamentals-aware): {float(adjusted_confidence):.2f}.")

    if rank is not None:
        lines.append(f"Rank score: {float(rank):.2f}.")
    if technical_rank is not None:
        lines.append(f"Technical rank component: {float(technical_rank):.2f}.")
    if base_hybrid_rank is not None:
        lines.append(f"Hybrid rank before bias-adjustment: {float(base_hybrid_rank):.2f}.")
    if bias_adjusted_rank is not None:
        lines.append(f"Hybrid rank after bias-adjustment: {float(bias_adjusted_rank):.2f}.")

    if opportunity_type:
        lines.append(f"Opportunity type: {opportunity_type}.")
    if investment_type:
        lines.append(f"Investment type: {investment_type}.")

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

    if isinstance(fundamentals, dict):
        lines.append("")
        lines.append("Fundamental analysis:")
        pe = fundamentals.get("pe")
        roe = fundamentals.get("roe")
        revenue_growth = fundamentals.get("revenue_growth")
        debt_to_equity = fundamentals.get("debt_to_equity")
        lines.append(f"- P/E: {f'{float(pe):.2f}' if pe is not None else 'N/A'}")
        lines.append(f"- ROE: {f'{float(roe):.2%}' if roe is not None else 'N/A'}")
        lines.append(
            f"- Revenue growth: {f'{float(revenue_growth):.2%}' if revenue_growth is not None else 'N/A'}"
        )
        lines.append(
            f"- Debt-to-equity: {f'{float(debt_to_equity):.2f}' if debt_to_equity is not None else 'N/A'}"
        )
    if fundamental_score is not None:
        lines.append(
            f"Fundamental score: {float(fundamental_score):.2f}"
            + (
                f" (raw {float(fundamental_raw_score):.2f})"
                if isinstance(fundamental_raw_score, (int, float))
                else ""
            )
            + "."
        )
    if fundamental_bias:
        lines.append(f"Fundamental bias: {fundamental_bias}.")
    if isinstance(fundamental_factor_scores, dict) and fundamental_factor_scores:
        lines.append("Fundamental factor breakdown:")
        for name, value in fundamental_factor_scores.items():
            lines.append(f"- {str(name).capitalize()} score: {float(value):.2f}")
        lines.append(
            "Fundamental factor scores: "
            + ", ".join(f"{name}={float(value):+.2f}" for name, value in fundamental_factor_scores.items())
            + "."
        )
        if float(fundamental_factor_scores.get("growth", 0) or 0) > 0.7:
            lines.append("Strong growth profile.")
        if float(fundamental_factor_scores.get("valuation", 1) or 1) < 0.4:
            lines.append("Valuation appears elevated.")
        if float(fundamental_factor_scores.get("risk", 1) or 1) < 0.4:
            lines.append("Higher financial risk detected.")
        if float(fundamental_factor_scores.get("growth", 0) or 0) > 0.7 and float(
            fundamental_factor_scores.get("risk", 1) or 1
        ) < 0.3:
            lines.append("High growth is offset by elevated financial risk.")
        if float(fundamental_factor_scores.get("valuation", 0) or 0) > 0.7 and float(
            fundamental_factor_scores.get("quality", 1) or 1
        ) < 0.3:
            lines.append("Low valuation may indicate a potential value trap.")
        if float(fundamental_factor_scores.get("growth", 0) or 0) > 0.7 and float(
            fundamental_factor_scores.get("quality", 0) or 0
        ) > 0.7:
            lines.append("Strong growth combined with high quality fundamentals.")
    if isinstance(fundamental_completeness, (int, float)):
        lines.append(f"Fundamental completeness: {float(fundamental_completeness):.0%}.")
    if isinstance(fundamental_interaction_penalty, (int, float)) and float(fundamental_interaction_penalty) != 0:
        lines.append(f"Interaction adjustment: {float(fundamental_interaction_penalty):+.2f}.")
        if float(fundamental_interaction_penalty) < 0:
            lines.append("Factor interaction penalty applied due to conflicting signals.")
    if isinstance(missing_fundamentals_ratio, (int, float)):
        lines.append(f"Missing fundamentals ratio: {float(missing_fundamentals_ratio):.0%}.")
    if missing_fundamentals_fields:
        lines.append("Missing fundamentals fields: " + ", ".join(str(field) for field in missing_fundamentals_fields))
    if fundamental_reasons:
        lines.append("Fundamental factors: " + " ".join(str(reason) for reason in fundamental_reasons))

    if signal in {"BUY", "STRONG BUY"} and fundamental_bias == "bearish":
        lines.append("Note: Strong technical signal but weak fundamentals (possible short-term trade).")
    elif signal in {"BUY", "STRONG BUY"} and fundamental_bias == "bullish":
        lines.append("Note: Technical momentum and fundamentals are aligned (high-conviction setup).")
    elif signal in {"SELL", "STRONG SELL"} and fundamental_bias == "bullish":
        lines.append("Note: Bearish technical signal but strong fundamentals (possible long-term opportunity).")
    elif signal in {"SELL", "STRONG SELL"} and fundamental_bias == "bearish":
        lines.append("Note: Technical and fundamental weakness are aligned (higher downside conviction).")
    elif fundamental_bias == "bullish":
        lines.append("Fundamentals support the current setup.")
    elif fundamental_bias == "bearish":
        lines.append("Fundamentals weaken the current setup.")
    elif fundamental_bias == "neutral" and (market == "energy" or sector == "energy"):
        lines.append("This is a stable/defensive stock where growth metrics are less relevant.")

    return "\n".join(lines) if lines else "No clear signal was generated."
