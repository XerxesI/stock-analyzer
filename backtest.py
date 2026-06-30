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
KEEP_RANK_THRESHOLD = 0.45
LOSS_CUT_PCT = 0.08
KEEP_WEIGHT_FLOOR_RATIO = 0.80
MAX_REPLACEMENTS = 2
REPLACEMENT_RATIO = 0.25
WEAK_HOLD_DAYS = 28
WEAK_MIN_GAIN = 0.98
BETTER_RANK_GAP = 0.15
MIN_ENTRY_RANK = 0.60
UNKNOWN_SECTOR_RANK_PENALTY = 0.80
MIN_HOLD_DAYS = 5


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


def should_keep_position(old_position: dict[str, Any], new_position: dict[str, Any]) -> bool:
    """Keep an existing holding only if refreshed signal quality is still acceptable."""

    _ = old_position
    new_rank = float(new_position.get("rank", 0) or 0)
    return new_rank >= 0.50


def _normalize_weights(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weighted = [dict(item) for item in positions]
    total_weight = sum(float(item.get("weight", 0) or 0) for item in weighted)
    if total_weight <= 0:
        return weighted
    for item in weighted:
        item["weight"] = float(item.get("weight", 0) or 0) / total_weight
    return weighted


def _price_snapshot(frame: pd.DataFrame, current_date: pd.Timestamp) -> dict[str, float | None]:
    available = frame.loc[:current_date]
    if available.empty:
        return {"price": None, "sma50": None}
    row = available.iloc[-1]
    price = float(row["Close"]) if pd.notna(row.get("Close")) else None
    sma50 = float(row["SMA50"]) if pd.notna(row.get("SMA50")) else None
    return {"price": price, "sma50": sma50}


def _momentum_component(price: float | None, sma50: float | None) -> float:
    if price is None or sma50 is None or sma50 <= 0:
        return 0.5
    momentum = (price / sma50) - 1.0
    normalized = (max(-0.2, min(0.2, momentum)) + 0.2) / 0.4
    return max(0.0, min(1.0, normalized))


def _is_spy_above_sma200(spy_frame: pd.DataFrame, current_date: pd.Timestamp) -> bool:
    snap = _price_snapshot(spy_frame, current_date)
    available = spy_frame.loc[:current_date]
    if available.empty:
        return False
    row = available.iloc[-1]
    sma200 = float(row["SMA200"]) if pd.notna(row.get("SMA200")) else None
    price = snap.get("price")
    return price is not None and sma200 is not None and price > sma200


def should_exit_on_trend_break(price: float | None, sma50: float | None) -> bool:
    return price is not None and sma50 is not None and price < (sma50 * 0.97)


def should_cut_loss(entry_price: float | None, current_price: float | None) -> bool:
    return (
        entry_price is not None
        and entry_price > 0
        and current_price is not None
        and current_price < (entry_price * (1.0 - LOSS_CUT_PCT))
    )


def should_exit_weak(position: dict[str, Any], current_data: dict[str, float | None]) -> bool:
    holding_days = int(position.get("holding_days", 0) or 0)
    if holding_days <= WEAK_HOLD_DAYS:
        return False
    entry_price = float(position.get("entry_price", 0) or 0)
    current_price = current_data.get("price")
    return entry_price > 0 and current_price is not None and current_price < (entry_price * WEAK_MIN_GAIN)


def should_exit(position: dict[str, Any], current_data: dict[str, float | None]) -> bool:
    if int(position.get("holding_days", 0) or 0) < MIN_HOLD_DAYS:
        return False
    entry_price = float(position.get("entry_price", 0) or 0) or None
    current_price = current_data.get("price")
    sma50 = current_data.get("sma50")
    if should_cut_loss(entry_price, current_price):
        return True
    if should_exit_on_trend_break(current_price, sma50):
        return True
    if should_exit_weak(position, current_data):
        return True
    return False


def is_much_better(new_position: dict[str, Any], old_position: dict[str, Any]) -> bool:
    new_rank = float(new_position.get("rank", 0) or 0)
    old_rank = float(old_position.get("rank", 0) or 0)
    return new_rank > (old_rank + BETTER_RANK_GAP)


def merge_rebalanced_portfolio(
    old_portfolio: list[dict[str, Any]],
    new_portfolio: list[dict[str, Any]],
    frames: dict[str, pd.DataFrame],
    current_date: pd.Timestamp,
    max_positions: int,
    max_replacements: int | None = None,
) -> list[dict[str, Any]]:
    """Merge old and new portfolios to reduce turnover while preserving quality."""

    new_active = [dict(item) for item in new_portfolio if str(item.get("symbol", "")).upper() != "CASH"]
    if not new_active:
        return [dict(item) for item in new_portfolio]
    if max_replacements is None:
        active_old = len([item for item in old_portfolio if str(item.get("symbol", "")).upper() != "CASH"])
        max_replacements = max(MAX_REPLACEMENTS, int(active_old * REPLACEMENT_RATIO))

    new_by_symbol = {str(item.get("symbol", "")).upper(): item for item in new_active}
    updated: list[dict[str, Any]] = []

    for old_position in old_portfolio:
        symbol = str(old_position.get("symbol", "")).upper()
        if not symbol or symbol == "CASH":
            continue
        frame = frames.get(symbol)
        if frame is None:
            continue
        new_position = new_by_symbol.get(symbol)
        current_data = _price_snapshot(frame, current_date)
        if new_position and should_keep_position(old_position, new_position) and not should_exit(old_position, current_data):
            kept = dict(new_position)
            kept["entry_price"] = float(old_position.get("entry_price", 0) or 0)
            previous_peak = float(old_position.get("peak_price", 0) or 0) or kept["entry_price"]
            current_price = current_data.get("price")
            kept["peak_price"] = max(previous_peak, float(current_price or previous_peak))
            kept["holding_days"] = int(old_position.get("holding_days", 0) or 0)
            old_weight = float(old_position.get("weight", 0) or 0)
            kept["weight"] = max(float(kept.get("weight", 0) or 0), old_weight * KEEP_WEIGHT_FLOOR_RATIO)
            updated.append(kept)

    existing_symbols = {str(item.get("symbol", "")).upper() for item in updated}
    replacements_added = 0
    for new_position in new_active:
        symbol = str(new_position.get("symbol", "")).upper()
        if symbol in existing_symbols:
            continue
        can_add_normally = replacements_added < max_replacements and len(updated) < max_positions
        if can_add_normally:
            added = dict(new_position)
            added["holding_days"] = 0
            updated.append(added)
            existing_symbols.add(symbol)
            replacements_added += 1
            continue
        if not updated:
            continue
        weakest = min(updated, key=lambda item: float(item.get("rank", 0) or 0))
        weakest_symbol = str(weakest.get("symbol", "")).upper()
        if weakest_symbol and weakest_symbol != "CASH" and is_much_better(new_position, weakest):
            updated = [item for item in updated if str(item.get("symbol", "")).upper() != weakest_symbol]
            existing_symbols.discard(weakest_symbol)
            added = dict(new_position)
            added["holding_days"] = 0
            updated.append(added)
            existing_symbols.add(symbol)

    if not updated:
        return [dict(item) for item in new_portfolio]

    return _normalize_weights(updated)


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
    momentum_rank = _momentum_component(signal_data.get("price"), signal_data.get("sma50"))
    if momentum_rank < 0.5:
        return None
    if not fundamental_sector or fundamental_sector == "unknown":
        final_rank *= UNKNOWN_SECTOR_RANK_PENALTY
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

    if not is_buy_opportunity(opportunity, min_confidence=0.5, min_rank=MIN_ENTRY_RANK):
        return None
    return opportunity


def _force_open_top_candidates(
    opportunities: list[dict[str, Any]],
    max_positions: int,
    min_rank: float = MIN_ENTRY_RANK,
) -> list[dict[str, Any]]:
    """Fallback entry when strict filters leave the portfolio empty."""

    ranked = sorted(
        (dict(item) for item in opportunities if float(item.get("rank", 0) or 0) >= min_rank),
        key=lambda item: float(item.get("rank", 0) or 0),
        reverse=True,
    )[:max_positions]
    if not ranked:
        return []
    total_rank_sq = sum((float(item.get("rank", 0) or 0) ** 2) for item in ranked)
    if total_rank_sq <= 0:
        return []
    for item in ranked:
        rank = float(item.get("rank", 0) or 0)
        item["weight"] = (rank**2) / total_rank_sq
        item["holding_days"] = int(item.get("holding_days", 0) or 0)
    return ranked


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
            item["peak_price"] = 0.0
            item["holding_days"] = 0
        else:
            frame = frames.get(symbol)
            if frame is None:
                raise ValueError(f"Missing price frame for {symbol}.")
            entry_price = _latest_close(frame, current_date)
            item["entry_price"] = round(entry_price, 4)
            item["peak_price"] = round(max(float(item.get("peak_price", 0) or 0), entry_price), 4)
            item["shares"] = value / entry_price if entry_price > 0 else 0.0
            item["holding_days"] = int(item.get("holding_days", 0) or 0)
        allocated.append(item)
    return allocated


def update_position_peaks(
    portfolio: list[dict[str, Any]],
    current_date: pd.Timestamp,
    frames: dict[str, pd.DataFrame],
) -> None:
    for position in portfolio:
        symbol = str(position.get("symbol", "")).upper()
        if symbol == "CASH":
            continue
        frame = frames.get(symbol)
        if frame is None:
            continue
        current_price = _latest_close(frame, current_date)
        old_peak = float(position.get("peak_price", 0) or 0)
        position["peak_price"] = round(max(old_peak, current_price), 4)
        position["holding_days"] = int(position.get("holding_days", 0) or 0) + 1


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
        update_position_peaks(current_portfolio, current_date, frames)
        capital = compute_portfolio_value(current_portfolio, current_date, frames)
        spy_ok = _is_spy_above_sma200(spy_frame, current_date)
        effective_max_positions = 8 if spy_ok else 4

        # 1) DAILY entry scan (mitte rebalance-gated)
        opportunities = scan_market_at_date(
            cleaned_symbols, frames, current_date, mode, spy_frame, debug=debug
        )
        gated_opportunities = [
            item for item in opportunities if float(item.get("rank", 0) or 0) >= MIN_ENTRY_RANK
        ]
        candidate_portfolio: list[dict[str, Any]] = build_portfolio(
            gated_opportunities, max_positions=effective_max_positions, debug=debug
        )

        active_positions = [p for p in current_portfolio if str(p.get("symbol", "")).upper() != "CASH"]
        if not active_positions and candidate_portfolio:
            # freeze/hold testis peab see avama positsioonid ka siis, kui rebalance_days on suur
            if all(str(item.get("symbol", "")).upper() == "CASH" for item in candidate_portfolio):
                forced = _force_open_top_candidates(
                    opportunities,
                    max_positions=effective_max_positions,
                    min_rank=MIN_ENTRY_RANK,
                )
                if forced:
                    current_portfolio = allocate_capital(forced, capital, current_date, frames)
                else:
                    current_portfolio = allocate_capital(candidate_portfolio, capital, current_date, frames)
            else:
                current_portfolio = allocate_capital(candidate_portfolio, capital, current_date, frames)

        # 2) REBALANCE ainult perioodiliselt
        elif is_rebalance_day(current_date, start, rebalance_days) and candidate_portfolio:
            rebalanced = merge_rebalanced_portfolio(
                current_portfolio,
                candidate_portfolio,
                frames,
                current_date,
                max_positions=effective_max_positions,
            )
            current_portfolio = allocate_capital(rebalanced, capital, current_date, frames)

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
