"""SWING_20 next-day Open label generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from stock_analyzer.datasets.swing_20.config import LabelConfig

REQUIRED_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


@dataclass(frozen=True)
class LabelFrameResult:
    """Labels plus quality counters gathered during label generation."""

    labels: pd.DataFrame
    quality_counts: dict[str, int]


def _empty_counts() -> dict[str, int]:
    return {
        "missing_entry_open_count": 0,
        "missing_future_high_count": 0,
        "missing_future_low_count": 0,
        "target_already_reached_at_entry_count": 0,
    }


def validate_ohlcv_frame(df: pd.DataFrame) -> None:
    """Raise if the OHLCV frame cannot support SWING_20 labeling."""

    if df.empty:
        raise ValueError("Input OHLCV frame is empty.")
    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Input OHLCV frame is missing required columns: {missing}.")


def label_at(
    df: pd.DataFrame,
    t_pos: int,
    config: LabelConfig = LabelConfig(),
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    """Compute one SWING_20 label.

    The signal date is ``t_pos``. Entry is the next trading day's Open. The signal-day
    High is never considered for target detection.
    """

    counts = _empty_counts()
    entry_pos = t_pos + 1
    horizon_end_exclusive = entry_pos + config.horizon_days
    if entry_pos >= len(df) or horizon_end_exclusive > len(df):
        return None, counts

    signal_bar = df.iloc[t_pos]
    entry_bar = df.iloc[entry_pos]
    entry_open = entry_bar["Open"]
    if pd.isna(entry_open) or float(entry_open) <= 0:
        counts["missing_entry_open_count"] += 1
        return None, counts

    signal_close = signal_bar["Close"]
    entry_price = float(entry_open)
    target_price = entry_price * (1.0 + config.target_return)
    fixed_stop_price = entry_price * (1.0 + config.fixed_stop)

    if pd.notna(signal_close) and float(entry_price) >= float(signal_close) * (1.0 + config.target_return):
        counts["target_already_reached_at_entry_count"] += 1

    future = df.iloc[entry_pos:horizon_end_exclusive]
    if future["High"].isna().any():
        counts["missing_future_high_count"] += int(future["High"].isna().sum())
        return None, counts
    if future["Low"].isna().any():
        counts["missing_future_low_count"] += int(future["Low"].isna().sum())
        return None, counts

    target_20pct_20d = False
    days_to_target: int | None = None
    target_before_fixed_stop = False
    fixed_stop_hit = False
    fixed_stop_day: int | None = None

    for offset, (_, bar) in enumerate(future.iterrows(), start=1):
        high = float(bar["High"])
        low = float(bar["Low"])
        hit_target = high >= target_price
        hit_stop = low <= fixed_stop_price

        if hit_target and days_to_target is None:
            target_20pct_20d = True
            days_to_target = offset
        if hit_stop and fixed_stop_day is None:
            fixed_stop_hit = True
            fixed_stop_day = offset
        if days_to_target is not None or fixed_stop_day is not None:
            break

    if target_20pct_20d:
        target_before_fixed_stop = fixed_stop_day is None or (
            days_to_target is not None and days_to_target <= fixed_stop_day
        )

    max_high = float(future["High"].max())
    min_low = float(future["Low"].min())
    final_close = float(future["Close"].iloc[-1])
    mfe_20d = (max_high - entry_price) / entry_price
    mae_20d = (min_low - entry_price) / entry_price
    close_return_20d = (final_close - entry_price) / entry_price

    result = {
        "date": df.index[t_pos],
        "entry_date": df.index[entry_pos],
        "entry_price": entry_price,
        "target_price": target_price,
        "target_20pct_20d": bool(target_20pct_20d),
        "days_to_target": days_to_target,
        "mfe_20d": mfe_20d,
        "mae_20d": mae_20d,
        "close_return_20d": close_return_20d,
        "target_before_fixed_stop": bool(target_before_fixed_stop),
        "fixed_stop_hit": bool(fixed_stop_hit),
        "fixed_stop_day": fixed_stop_day,
        "large_gap_at_entry": (
            (entry_price - float(signal_close)) / float(signal_close)
            if pd.notna(signal_close) and float(signal_close) > 0
            else None
        ),
    }
    return result, counts


def label_frame(
    symbol: str,
    df: pd.DataFrame,
    config: LabelConfig = LabelConfig(),
) -> LabelFrameResult:
    """Compute SWING_20 labels for every possible signal date in ``df``."""

    validate_ohlcv_frame(df)
    ordered = df.sort_index().copy()
    rows: list[dict[str, Any]] = []
    counts = _empty_counts()

    for t_pos in range(len(ordered)):
        result, local_counts = label_at(ordered, t_pos, config)
        for key, value in local_counts.items():
            counts[key] += value
        if result is None:
            continue
        rows.append({"symbol": symbol.upper(), **result})

    labels = pd.DataFrame(rows)
    if not labels.empty:
        labels["date"] = pd.to_datetime(labels["date"])
        labels["entry_date"] = pd.to_datetime(labels["entry_date"])
    return LabelFrameResult(labels=labels, quality_counts=counts)

