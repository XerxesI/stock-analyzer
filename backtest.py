"""Historical validation for stock signals."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from data_fetcher import get_stock_data
from indicators import calculate_indicators
from strategy import generate_signal


DEFAULT_PERIOD = "2y"
DEFAULT_HORIZON = 5
WARMUP = 200
RETURN_THRESHOLD = 0.01


@dataclass
class BacktestResult:
    symbol: str
    samples: int
    prediction_accuracy: float
    coverage: float
    avg_forward_return: float
    correct_predictions: int
    directional_predictions: int


def _signal_direction(signal: str) -> int:
    if signal in {"BUY", "STRONG BUY"}:
        return 1
    if signal in {"SELL", "STRONG SELL"}:
        return -1
    return 0


def _evaluate_prediction(signal: str, forward_return: float) -> bool:
    direction = _signal_direction(signal)
    if direction == 0:
        return False
    if direction > 0:
        return forward_return > RETURN_THRESHOLD
    return forward_return < -RETURN_THRESHOLD


def run_backtest(df: pd.DataFrame, symbol: str, horizon: int = DEFAULT_HORIZON) -> BacktestResult:
    """Run a simple forward-return validation over historical data."""

    if df.empty:
        raise ValueError("Input data is empty.")
    if len(df) <= WARMUP + horizon:
        raise ValueError("Not enough data for backtesting.")

    hits = 0
    samples = 0
    forward_returns: list[float] = []
    directional_predictions = 0

    for idx in range(WARMUP, len(df) - horizon):
        window = df.iloc[: idx + 1]
        signal_data = generate_signal(window)
        current_close = float(window.iloc[-1]["Close"])
        future_close = float(df.iloc[idx + horizon]["Close"])
        forward_return = (future_close - current_close) / current_close
        forward_returns.append(forward_return)
        samples += 1

        signal = str(signal_data.get("signal", "HOLD"))
        if _signal_direction(signal) != 0:
            directional_predictions += 1
            if _evaluate_prediction(signal, forward_return):
                hits += 1

    accuracy = hits / directional_predictions if directional_predictions else 0.0
    avg_forward_return = sum(forward_returns) / samples if samples else 0.0
    coverage = directional_predictions / samples if samples else 0.0

    return BacktestResult(
        symbol=symbol.upper(),
        samples=samples,
        prediction_accuracy=round(accuracy, 2),
        coverage=round(coverage, 2),
        avg_forward_return=round(avg_forward_return, 4),
        correct_predictions=hits,
        directional_predictions=directional_predictions,
    )


def analyze_symbol(symbol: str, period: str = DEFAULT_PERIOD, horizon: int = DEFAULT_HORIZON) -> BacktestResult:
    """Fetch data and run a backtest for one symbol."""

    raw_data = get_stock_data(symbol, period)
    enriched_data = calculate_indicators(raw_data)
    return run_backtest(enriched_data, symbol, horizon)


def _print_result(result: BacktestResult) -> None:
    print("===================================")
    print(f"Symbol: {result.symbol}")
    print(f"Samples: {result.samples}")
    print(f"Prediction accuracy: {result.prediction_accuracy:.2f}")
    print(f"Coverage: {result.coverage:.2f}")
    print(f"Average forward return: {result.avg_forward_return:.4f}")
    print(f"Correct predictions: {result.correct_predictions}")
    print(f"Directional predictions: {result.directional_predictions}")
    print("===================================")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and print backtest results."""

    parser = argparse.ArgumentParser(description="Backtest the stock analysis engine.")
    parser.add_argument("symbol", help="Stock ticker symbol, for example AAPL.")
    parser.add_argument(
        "--period",
        default=DEFAULT_PERIOD,
        help="Yahoo Finance history period (default: 2y).",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=DEFAULT_HORIZON,
        help="Forward days to evaluate after each signal (default: 5).",
    )
    args = parser.parse_args(argv)

    try:
        result = analyze_symbol(args.symbol, args.period, args.horizon)
        _print_result(result)
        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
