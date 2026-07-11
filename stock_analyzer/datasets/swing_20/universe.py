"""Date-specific eligibility logic for the SWING_20 universe."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from stock_analyzer.datasets.swing_20.config import UniverseConfig


@dataclass(frozen=True)
class SymbolMetadata:
    """Minimal instrument metadata used by the audit."""

    symbol: str
    security_name: str | None = None
    exchange: str | None = None
    instrument_type: str = "COMMON_STOCK"


def eligibility_frame(
    symbol: str,
    df: pd.DataFrame,
    config: UniverseConfig = UniverseConfig(),
    metadata: SymbolMetadata | None = None,
) -> pd.DataFrame:
    """Build per-date eligibility diagnostics for one symbol."""

    if df.empty:
        return pd.DataFrame()
    required = {"Close", "Volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"OHLCV frame is missing required columns: {sorted(missing)}")

    ordered = df.sort_index().copy()
    close = ordered["Close"]
    adv20 = (ordered["Close"] * ordered["Volume"]).rolling(20).mean()
    history_days = pd.Series(range(1, len(ordered) + 1), index=ordered.index)

    meta = metadata or SymbolMetadata(symbol=symbol.upper())
    result = pd.DataFrame(
        {
            "symbol": symbol.upper(),
            "date": pd.to_datetime(ordered.index),
            "history_days": history_days.values,
            "price": close.values,
            "adv20": adv20.values,
            "security_name": meta.security_name,
            "exchange": meta.exchange,
            "instrument_type": meta.instrument_type,
        }
    )
    result["eligible"] = True
    result["exclusion_reason"] = ""

    _reject(result, result["history_days"] < config.minimum_history_days, "INSUFFICIENT_HISTORY")
    _reject(result, result["price"].isna() | (result["price"] < config.minimum_price), "LOW_PRICE")
    _reject(result, result["adv20"].isna() | (result["adv20"] < config.minimum_adv20), "LOW_ADV20")
    _reject(result, result["instrument_type"] != "COMMON_STOCK", "NOT_COMMON_STOCK")
    return result


def _reject(frame: pd.DataFrame, mask: pd.Series, reason: str) -> None:
    active = frame["eligible"] & mask
    frame.loc[active, "eligible"] = False
    frame.loc[active, "exclusion_reason"] = reason


def universe_summary(eligibility: pd.DataFrame) -> dict[str, object]:
    """Summarize date-specific eligibility."""

    if eligibility.empty:
        return {
            "observations": 0,
            "average_eligible_tickers_per_date": 0,
            "exclusion_reasons": {},
        }
    by_date = eligibility.groupby("date")["eligible"].sum()
    exclusions = (
        eligibility.loc[~eligibility["eligible"], "exclusion_reason"].value_counts().to_dict()
        if "eligible" in eligibility
        else {}
    )
    return {
        "observations": int(len(eligibility)),
        "average_eligible_tickers_per_date": float(by_date.mean()) if not by_date.empty else 0.0,
        "minimum_eligible_tickers_per_date": int(by_date.min()) if not by_date.empty else 0,
        "maximum_eligible_tickers_per_date": int(by_date.max()) if not by_date.empty else 0,
        "exclusion_reasons": {str(k): int(v) for k, v in exclusions.items()},
        "survivorship_bias_limitations": [
            "Current symbol directories and curated input lists are not point-in-time universes."
        ],
    }

