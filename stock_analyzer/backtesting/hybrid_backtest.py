"""Clean walk-forward backtest for the hybrid core-satellite strategy.

Design principles (fixing the flaws found in the old engine):
  - auto_adjust=True prices (reuses backtest._fetch_history, already fixed).
  - Strict no-lookahead: every decision at date T uses only rows <= T.
  - NO fundamentals (momentum needs none -> sidesteps the current-fundamentals
    look-ahead that inflated the old rank's IC).
  - Explicit transaction costs charged on turnover at every rebalance.
  - Monthly rebalance (momentum is a monthly-horizon factor).

Data is fetched once over a wide span and sliced per window, so run_hybrid.py
can evaluate many windows x configs against one in-memory cache.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Mapping, Sequence

import pandas as pd

from stock_analyzer.backtesting.backtest import _fetch_history, _is_spy_above_sma200
from stock_analyzer.core.factors import price_asof, top_n_by_momentum
from stock_analyzer.portfolio.hybrid_portfolio import CASH, build_core_satellite
from stock_analyzer.core.indicators import calculate_indicators
TRADING_DAYS_PER_YEAR = 252
DEFAULT_REBALANCE_DAYS = 21
DEFAULT_COST_RATE = 0.0010  # 0.10% per side, applied to turnover


def fetch_frames(
    symbols: Sequence[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    max_workers: int = 8,
) -> dict[str, pd.DataFrame]:
    """Threaded download of normalized OHLCV frames (auto_adjust=True)."""

    unique = sorted({s.strip().upper() for s in symbols if s.strip()})
    frames: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_history, s, start, end): s for s in unique}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                frames[symbol] = future.result()
            except Exception:  # noqa: BLE001 - tolerate dead/illiquid tickers
                pass
    return frames


def _trading_days(spy_frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    idx = spy_frame.index
    mask = (idx >= start) & (idx <= end)
    return list(idx[mask])


def _metrics(values: list[float]) -> dict[str, float]:
    series = pd.Series(values, dtype="float64")
    returns = series.pct_change().dropna()
    total_return = float(series.iloc[-1] / series.iloc[0] - 1.0) if series.iloc[0] > 0 else 0.0
    vol = float(returns.std()) if not returns.empty else 0.0
    sharpe = float(returns.mean() / vol * (TRADING_DAYS_PER_YEAR ** 0.5)) if vol > 0 else 0.0
    max_drawdown = float((series / series.cummax() - 1.0).min()) if len(series) else 0.0
    return {
        "total_return": round(total_return, 4),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(max_drawdown, 4),
        "volatility": round(vol, 4),
    }


def spy_buy_hold(spy_frame: pd.DataFrame, days: list[pd.Timestamp], initial: float) -> list[float]:
    """Benchmark: buy-and-hold SPY valued on the same trading days."""

    p0 = price_asof(spy_frame, days[0])
    return [initial * (price_asof(spy_frame, d) / p0) if p0 > 0 else initial for d in days]


def run_hybrid_backtest(
    frames: Mapping[str, pd.DataFrame],
    spy_indicators: pd.DataFrame,
    core_assets: Sequence[str],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    *,
    core_weight: float = 0.70,
    satellite_n: int = 10,
    rebalance_days: int = DEFAULT_REBALANCE_DAYS,
    cost_rate: float = DEFAULT_COST_RATE,
    use_overlay: bool = True,
    use_satellite: bool = True,
    initial_capital: float = 10_000.0,
) -> dict[str, Any]:
    """Run one config and return {metrics, values, dates}.

    Ablation is controlled by (core_weight, use_satellite, use_overlay):
      core-only          -> use_satellite=False (core_weight forced to 1.0)
      core + satellite    -> use_satellite=True, use_overlay=False
      core + sat + overlay-> use_satellite=True, use_overlay=True
    """

    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    core_assets = [c.upper() for c in core_assets]
    exclude = set(core_assets)

    if not use_satellite:
        core_weight = 1.0

    days = _trading_days(spy_indicators, start, end)
    if not days:
        raise ValueError("No trading days in the requested window.")

    def price(symbol: str, t: pd.Timestamp) -> float:
        frame = frames.get(symbol)
        return price_asof(frame, t) if frame is not None else 0.0

    shares: dict[str, float] = {}
    cash = float(initial_capital)
    values: list[float] = []

    for i, t in enumerate(days):
        if i % rebalance_days == 0:
            regime_ok = True if not use_overlay else _is_spy_above_sma200(spy_indicators, t)
            satellite = (
                top_n_by_momentum(frames, t, satellite_n, exclude=exclude)
                if use_satellite and core_weight < 1.0
                else []
            )
            target = build_core_satellite(core_assets, satellite, core_weight, regime_ok)

            portfolio_value = cash + sum(sh * price(s, t) for s, sh in shares.items())
            target_dollars: dict[str, float] = {}
            target_cash = 0.0
            for pos in target:
                if pos["symbol"] == CASH:
                    target_cash += pos["weight"] * portfolio_value
                else:
                    target_dollars[pos["symbol"]] = pos["weight"] * portfolio_value

            current_dollars = {s: sh * price(s, t) for s, sh in shares.items()}
            all_symbols = set(current_dollars) | set(target_dollars)
            turnover = sum(abs(target_dollars.get(s, 0.0) - current_dollars.get(s, 0.0)) for s in all_symbols)
            cost = turnover * cost_rate

            shares = {s: d / price(s, t) for s, d in target_dollars.items() if price(s, t) > 0}
            cash = target_cash - cost

        values.append(cash + sum(sh * price(s, t) for s, sh in shares.items()))

    result = {"metrics": _metrics(values), "values": values, "dates": days}
    return result


def prepare_spy_indicators(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """SPY frame enriched with SMA200 etc. for the regime check."""

    if "SPY" not in frames:
        raise ValueError("SPY frame is required for the regime overlay and benchmark.")
    return calculate_indicators(frames["SPY"])
