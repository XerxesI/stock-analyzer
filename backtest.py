"""Historical backtest engine for portfolio strategy validation."""

from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf

from analysis_service import (
    DEFAULT_SCORING_MODE,
    adjusted_confidence,
    apply_completeness_penalty,
    apply_fundamental_bias_adjustment,
    classify_investment_type,
    combine_hybrid_rank,
    distribute_confidence,
    normalize_rank,
    resolve_scoring_mode,
    stretch_rank_distribution,
)
from indicators import calculate_indicators
from opportunities import BUY_SIGNALS, classify_opportunity, is_buy_opportunity
from portfolio import build_portfolio, summarize_portfolio
from strategy import generate_signal
from fundamentals import classify_fundamental_bias, get_fundamentals, score_fundamental_factors


REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
LOOKBACK_BUFFER_DAYS = 260
DEFAULT_INITIAL_CAPITAL = 10_000.0
DEFAULT_MAX_POSITIONS = 10


def _normalize_download_frame(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        raise ValueError("No data returned.")

    frame = data.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        if all(column in frame.columns.get_level_values(0) for column in REQUIRED_COLUMNS):
            frame.columns = frame.columns.get_level_values(0)
        elif all(column in frame.columns.get_level_values(-1) for column in REQUIRED_COLUMNS):
            frame.columns = frame.columns.get_level_values(-1)
        else:
            raise ValueError("Fetched data has an unexpected column layout and cannot be normalized.")

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"Fetched data is missing required columns: {', '.join(missing_columns)}.")

    cleaned = frame.loc[:, REQUIRED_COLUMNS].copy()
    cleaned.index = pd.to_datetime(cleaned.index).tz_localize(None)
    return cleaned.sort_index()


def _fetch_history(symbol: str, start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.DataFrame:
    raw = yf.download(
        symbol,
        start=start_date.to_pydatetime(),
        end=(end_date + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
        timeout=15,
    )
    return _normalize_download_frame(raw)


def _latest_close(frame: pd.DataFrame, current_date: pd.Timestamp) -> float:
    available = frame.loc[:current_date]
    if available.empty:
        raise ValueError("No price available up to the requested date.")
    return float(available.iloc[-1]["Close"])


def is_rebalance_day(current_date: pd.Timestamp, start_date: pd.Timestamp, rebalance_days: int) -> bool:
    if rebalance_days <= 0:
        raise ValueError("rebalance_days must be greater than zero.")
    elapsed_days = (current_date.normalize() - start_date.normalize()).days
    return elapsed_days % rebalance_days == 0


def _cash_only_portfolio(capital: float) -> list[dict[str, Any]]:
    return [
        {
            "symbol": "CASH",
            "weight": 1.0,
            "rank": 0.0,
            "confidence": 0.0,
            "fundamental_score": 0.0,
            "investment_type": "cash",
            "sector": "cash",
            "fundamental_factors": {"risk": 0.0},
            "value": capital,
            "shares": 0.0,
            "entry_price": 0.0,
        }
    ]


def _build_opportunity(
    symbol: str,
    frame: pd.DataFrame,
    current_date: pd.Timestamp,
    mode: str,
    market_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    history = frame.loc[:current_date]
    if history.empty:
        return None

    signal_data = generate_signal(history, market_context=market_context)
    if str(signal_data.get("signal", "HOLD")) not in BUY_SIGNALS:
        return None

    fundamentals = get_fundamentals(symbol)
    fundamental_sector = str(fundamentals.get("sector") or "").lower().strip() or None
    effective_mode = resolve_scoring_mode(mode)
    fundamental_details = score_fundamental_factors(
        fundamentals,
        mode=effective_mode,
        sector=fundamental_sector,
    )

    technical_rank = normalize_rank(signal_data)
    fundamental_score_raw = fundamental_details.get("fundamental_score")
    fundamental_score = float(fundamental_score_raw) if isinstance(fundamental_score_raw, (int, float)) else None
    completeness_raw = fundamental_details.get("fundamental_completeness")
    completeness = float(completeness_raw) if isinstance(completeness_raw, (int, float)) else None

    base_hybrid_rank = combine_hybrid_rank(technical_rank=technical_rank, fundamental_score=fundamental_score)
    bias_adjusted_rank = apply_fundamental_bias_adjustment(base_hybrid_rank, fundamental_score)
    stretched_rank = stretch_rank_distribution(bias_adjusted_rank)
    final_rank = apply_completeness_penalty(stretched_rank, completeness)
    final_rank = round(min(1.0, max(0.0, final_rank)), 2)

    technical_score = signal_data.get("technical_score", signal_data.get("score"))
    technical_confidence = distribute_confidence(float(technical_score or 0))
    technical_confidence = min(1.0, round(technical_confidence + (0.05 * technical_rank), 2))
    conviction_confidence = adjusted_confidence(technical_confidence, fundamental_score, completeness)
    factors = fundamental_details.get("factors", {})

    opportunity = {
        "symbol": symbol.upper(),
        "signal": signal_data.get("signal"),
        "score": technical_score,
        "technical_score": technical_score,
        "confidence": technical_confidence,
        "adjusted_confidence": conviction_confidence,
        "rank": final_rank,
        "technical_rank": technical_rank,
        "fundamental_score": fundamental_score,
        "fundamental_bias": classify_fundamental_bias(fundamental_score),
        "fundamental_mode": effective_mode,
        "fundamental_sector": fundamental_sector,
        "fundamental_factors": factors,
        "fundamental_weights": fundamental_details.get("weights", {}),
        "fundamental_completeness": completeness,
        "fundamental_risk_scale": fundamental_details.get("risk_scale"),
        "fundamental_reasons": fundamental_details.get("reasons", []),
        "fundamental_interaction_penalty": fundamental_details.get("interaction_penalty"),
        "investment_type": classify_investment_type(technical_rank, fundamental_score),
        "opportunity_type": classify_opportunity({**signal_data, "rank": final_rank}),
        "trend_strength": signal_data.get("trend_strength"),
        "market_bias": signal_data.get("market_bias"),
        "rsi": signal_data.get("rsi"),
        "macd": signal_data.get("macd"),
        "macd_signal": signal_data.get("macd_signal"),
        "market_context": market_context,
    }

    if not is_buy_opportunity(opportunity, min_confidence=0.5, min_rank=0.45):
        return None
    return opportunity


def scan_market_at_date(
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
    current_date: pd.Timestamp,
    mode: str,
    spy_frame: pd.DataFrame,
    debug: bool = False,
) -> list[dict[str, Any]]:
    spy_slice = spy_frame.loc[:current_date]
    market_context = None
    if not spy_slice.empty:
        spy_signal = generate_signal(spy_slice)
        spy_decision = str(spy_signal.get("signal", "HOLD"))
        if spy_decision in {"BUY", "STRONG BUY"}:
            bias = "bullish"
        elif spy_decision in {"SELL", "STRONG SELL"}:
            bias = "bearish"
        else:
            bias = "neutral"
        market_context = {"bias": bias}

    opportunities: list[dict[str, Any]] = []
    for symbol in symbols:
        frame = frames.get(symbol.upper())
        if frame is None:
            continue
        opportunity = _build_opportunity(symbol, frame, current_date, mode, market_context)
        if opportunity is None:
            continue
        if debug:
            factors = opportunity.get("fundamental_factors", {})
            print(
                symbol.upper(),
                f"rank={float(opportunity.get('rank', 0) or 0):.2f}",
                f"tech={float(opportunity.get('technical_rank', 0) or 0):.2f}",
                f"risk={float((factors or {}).get('risk', 0) or 0):.2f}",
                f"sector={opportunity.get('fundamental_sector') or 'unknown'}",
            )
        opportunities.append(opportunity)

    return opportunities


def allocate_capital(
    portfolio: list[dict[str, Any]],
    capital: float,
    current_date: pd.Timestamp,
    frames: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    allocated: list[dict[str, Any]] = []
    for position in portfolio:
        item = dict(position)
        symbol = str(item.get("symbol", "")).upper()
        weight = float(item.get("weight", 0) or 0)
        value = capital * weight
        item["value"] = round(value, 2)
        if symbol == "CASH":
            item["shares"] = 0.0
            item["entry_price"] = 0.0
        else:
            frame = frames.get(symbol)
            if frame is None:
                raise ValueError(f"Missing price frame for {symbol}.")
            entry_price = _latest_close(frame, current_date)
            item["entry_price"] = round(entry_price, 4)
            item["shares"] = value / entry_price if entry_price > 0 else 0.0
        allocated.append(item)
    return allocated


def compute_portfolio_value(
    portfolio: list[dict[str, Any]],
    current_date: pd.Timestamp,
    frames: dict[str, pd.DataFrame],
) -> float:
    total = 0.0
    for position in portfolio:
        symbol = str(position.get("symbol", "")).upper()
        weight = float(position.get("weight", 0) or 0)
        if symbol == "CASH":
            total += float(position.get("value", 0) or 0)
            continue
        frame = frames.get(symbol)
        if frame is None:
            raise ValueError(f"Missing price frame for {symbol}.")
        current_price = _latest_close(frame, current_date)
        shares = float(position.get("shares", 0) or 0)
        if shares == 0 and weight > 0:
            entry_price = float(position.get("entry_price", 0) or 0)
            if entry_price > 0:
                shares = (float(position.get("value", 0) or 0)) / entry_price
        total += shares * current_price
    return round(total, 2)


def add_benchmark(
    history: list[dict[str, Any]],
    spy_frame: pd.DataFrame,
    initial_capital: float,
    start_date: pd.Timestamp,
) -> list[dict[str, Any]]:
    if spy_frame.empty:
        raise ValueError("Benchmark data is empty.")

    start_price = _latest_close(spy_frame, start_date)
    if start_price <= 0:
        raise ValueError("Benchmark start price must be greater than zero.")

    for row in history:
        current_date = pd.Timestamp(row["date"])
        close = _latest_close(spy_frame, current_date)
        row["benchmark_price"] = round(close, 2)
        row["benchmark_value"] = round(initial_capital * (close / start_price), 2)
    return history


def calculate_metrics(history: list[dict[str, Any]]) -> dict[str, float]:
    if not history:
        return {
            "total_return": 0.0,
            "volatility": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "benchmark_total_return": 0.0,
            "excess_return": 0.0,
        }

    df = pd.DataFrame(history)
    df["returns"] = df["portfolio_value"].pct_change()
    df["benchmark_returns"] = df["benchmark_value"].pct_change()
    start_value = float(df["portfolio_value"].iloc[0])
    end_value = float(df["portfolio_value"].iloc[-1])
    benchmark_start = float(df["benchmark_value"].iloc[0])
    benchmark_end = float(df["benchmark_value"].iloc[-1])
    returns = df["returns"].dropna()
    volatility = float(returns.std()) if not returns.empty else 0.0
    sharpe = float(returns.mean() / volatility) if volatility > 0 else 0.0
    max_drawdown = float((df["portfolio_value"] / df["portfolio_value"].cummax() - 1).min())
    total_return = (end_value / start_value - 1) if start_value > 0 else 0.0
    benchmark_total_return = (benchmark_end / benchmark_start - 1) if benchmark_start > 0 else 0.0
    excess_return = total_return - benchmark_total_return
    return {
        "total_return": round(total_return, 4),
        "volatility": round(volatility, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_drawdown, 4),
        "benchmark_total_return": round(benchmark_total_return, 4),
        "excess_return": round(excess_return, 4),
    }


def run_backtest(
    symbols: list[str],
    start_date: str,
    end_date: str,
    rebalance_days: int = 7,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    mode: str = DEFAULT_SCORING_MODE,
    max_positions: int = DEFAULT_MAX_POSITIONS,
    debug: bool = False,
) -> dict[str, Any]:
    """Run portfolio backtest with periodic rebalancing."""

    if not symbols:
        raise ValueError("At least one symbol is required.")
    if rebalance_days <= 0:
        raise ValueError("rebalance_days must be greater than zero.")

    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must be on or after start_date.")

    lookback_start = start - pd.Timedelta(days=LOOKBACK_BUFFER_DAYS)
    cleaned_symbols = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    frames: dict[str, pd.DataFrame] = {}
    skipped: dict[str, str] = {}

    for symbol in cleaned_symbols:
        try:
            frames[symbol] = calculate_indicators(_fetch_history(symbol, lookback_start, end))
        except (ConnectionError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            skipped[symbol] = str(exc)

    if not frames:
        raise RuntimeError("No symbols could be loaded for backtesting.")

    try:
        spy_frame = calculate_indicators(_fetch_history("SPY", lookback_start, end))
    except (ConnectionError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        raise RuntimeError(f"Benchmark data could not be loaded: {exc}") from exc

    history: list[dict[str, Any]] = []
    current_portfolio = _cash_only_portfolio(initial_capital)
    current_date = start

    while current_date <= end:
        if is_rebalance_day(current_date, start, rebalance_days):
            capital = compute_portfolio_value(current_portfolio, current_date, frames)
            opportunities = scan_market_at_date(
                cleaned_symbols,
                frames,
                current_date,
                mode,
                spy_frame,
                debug=debug,
            )
            rebuilt = build_portfolio(opportunities, max_positions=max_positions, debug=debug)
            if rebuilt:
                current_portfolio = allocate_capital(rebuilt, capital, current_date, frames)
            else:
                current_portfolio = _cash_only_portfolio(capital)

        portfolio_value = compute_portfolio_value(current_portfolio, current_date, frames)
        active_positions = len([pos for pos in current_portfolio if str(pos.get("symbol", "")).upper() != "CASH"])
        if debug:
            print(f"{current_date.date()} | positions={active_positions} | value={portfolio_value:.2f}")
        history.append(
            {
                "date": current_date,
                "value": portfolio_value,
                "portfolio_value": portfolio_value,
                "rebalance": is_rebalance_day(current_date, start, rebalance_days),
                "positions": active_positions,
            }
        )
        current_date += pd.Timedelta(days=1)

    add_benchmark(history, spy_frame, initial_capital, start)
    metrics = calculate_metrics(history)
    summary = summarize_portfolio(current_portfolio)
    return {
        "history": history,
        "metrics": metrics,
        "portfolio": current_portfolio,
        "summary": summary,
        "skipped_symbols": skipped,
        "symbols": cleaned_symbols,
        "start_date": str(start.date()),
        "end_date": str(end.date()),
        "rebalance_days": rebalance_days,
        "initial_capital": initial_capital,
        "mode": mode,
    }
