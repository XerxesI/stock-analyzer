"""ATR-scaled triple-barrier labeling, High/Low-based MFE/MAE, and R-multiple.

Implements Research Protocol v1.2, section 2. Replaces the fixed-date forward-return
target used in the earlier swing_rank_ic_test.py / component_ic_test.py experiments,
which could not distinguish "hit +12% on day 3, gave it back by day 30" (a successful
swing trade) from "never moved" (a genuine miss) - see Protocol Appendix A for why that
mattered.

Key design decisions (see Protocol for full rationale):
    - Barriers are ATR-scaled, not fixed percentages, so a 7%-ATR name and a 1.2%-ATR
      name are judged on a comparable basis.
    - MFE/MAE use High/Low, not Close, since a trader can exit intrabar.
    - Barrier multiples and horizons are CONFIGURATION (LabelingConfig), never
      hardcoded in the logic itself - per Protocol section 2.1.
    - All three (triple-barrier outcome, MFE, MAE) are kept as SEPARATE outputs.
      They are deliberately not combined into a single "quality score" here - see
      Protocol section 5 on why Trade Score v1's mistake (combining components before
      knowing any of them worked) should not be repeated for labels either.

Causality: label_at/label_frame only ever read bars STRICTLY AFTER the entry bar `t`
for the outcome itself. ATR at `t` must already be computed causally (a rolling/backward
window) by the feature pipeline (see core/indicators.py) - this module does not
recompute ATR, it only consumes it, so it is the caller's responsibility to ensure the
ATR column was computed correctly. See Protocol section 6 (look-ahead checklist).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

TripleBarrierOutcome = Literal["UPPER_HIT", "LOWER_HIT", "TIME_HIT"]

ATR_COLUMN = "ATR14"


@dataclass(frozen=True)
class LabelingConfig:
    """Triple-barrier configuration. Mirrors Research Protocol v1.2 section 2.1's YAML.

    Attributes:
        take_profit_atr_multiple: Upper barrier = entry_price + this * ATR(14).
        stop_loss_atr_multiple: Lower barrier = entry_price - this * ATR(14).
        horizons: Time barriers (in trading days) to evaluate. All are tested in
            parallel by ``label_frame`` - see Protocol section 2.4.
    """

    take_profit_atr_multiple: float = 2.0
    stop_loss_atr_multiple: float = 1.0
    horizons: tuple[int, ...] = (5, 10, 20, 40)


DEFAULT_LABELING_CONFIG = LabelingConfig()


def label_at(
    df: pd.DataFrame,
    t_pos: int,
    horizon: int,
    config: LabelingConfig = DEFAULT_LABELING_CONFIG,
) -> dict[str, Any] | None:
    """Compute the triple-barrier label + MFE/MAE + R-multiple for one (t, horizon).

    Args:
        df: OHLCV DataFrame (needs High, Low, Close, and an ATR column - see
            ``ATR_COLUMN``), indexed chronologically, integer-positionable.
        t_pos: Integer position (0-based, matching ``df.iloc``) of the entry bar.
        horizon: Maximum holding period in trading days (the time barrier).
        config: Barrier configuration.

    Returns:
        None if there isn't enough forward data or ATR is missing/invalid at `t_pos`.
        Otherwise a dict with:
            outcome: "UPPER_HIT" | "LOWER_HIT" | "TIME_HIT"
            success: True iff outcome == "UPPER_HIT"
            exit_day: number of trading days after t_pos when the outcome resolved
                (equals horizon for TIME_HIT)
            mfe: Maximum Favorable Excursion, as a fraction of entry price, using High
            mae: Maximum Adverse Excursion, as a fraction of entry price (negative or
                zero), using Low
            r_multiple: mfe expressed in units of ATR (mfe_price / atr_price)
            entry_price: Close at t_pos
            atr_at_entry: ATR(14) value at t_pos (already causal - computed upstream)

    Note on same-bar ambiguity: if a single forward bar's High/Low range spans BOTH
    barriers (touches both take-profit and stop-loss within the same day), we cannot
    tell from daily OHLC which was hit first. We conservatively assume the stop-loss
    was hit first (LOWER_HIT) in that case, since assuming the better outcome would
    bias results optimistically.
    """

    if t_pos + horizon >= len(df):
        return None

    atr = df[ATR_COLUMN].iloc[t_pos] if ATR_COLUMN in df.columns else None
    if atr is None or pd.isna(atr) or atr <= 0:
        return None

    entry_price = float(df["Close"].iloc[t_pos])
    if entry_price <= 0:
        return None

    upper_barrier = entry_price + config.take_profit_atr_multiple * float(atr)
    lower_barrier = entry_price - config.stop_loss_atr_multiple * float(atr)

    outcome: TripleBarrierOutcome = "TIME_HIT"
    exit_day = horizon
    mfe = 0.0
    mae = 0.0

    for step in range(1, horizon + 1):
        bar = df.iloc[t_pos + step]
        high = float(bar["High"])
        low = float(bar["Low"])

        mfe = max(mfe, (high - entry_price) / entry_price)
        mae = min(mae, (low - entry_price) / entry_price)

        hit_upper = high >= upper_barrier
        hit_lower = low <= lower_barrier

        if hit_upper and hit_lower:
            outcome = "LOWER_HIT"  # conservative tie-break, see docstring
            exit_day = step
            break
        if hit_upper:
            outcome = "UPPER_HIT"
            exit_day = step
            break
        if hit_lower:
            outcome = "LOWER_HIT"
            exit_day = step
            break

    atr_fraction = float(atr) / entry_price
    r_multiple = (mfe / atr_fraction) if atr_fraction > 0 else None

    return {
        "outcome": outcome,
        "success": outcome == "UPPER_HIT",
        "exit_day": exit_day,
        "mfe": mfe,
        "mae": mae,
        "r_multiple": r_multiple,
        "entry_price": entry_price,
        "atr_at_entry": float(atr),
    }


def label_frame(
    df: pd.DataFrame,
    config: LabelingConfig = DEFAULT_LABELING_CONFIG,
) -> pd.DataFrame:
    """Compute triple-barrier labels for every bar and every configured horizon.

    Long format: one row per (date, horizon) combination, so downstream IC testing
    (validation/ic_test.py) can group by horizon directly.

    Args:
        df: OHLCV DataFrame with an ATR column already computed causally (see
            core/indicators.py's ATR14).
        config: Barrier configuration.

    Returns:
        DataFrame with columns: date, horizon, outcome, success, exit_day, mfe, mae,
        r_multiple, entry_price, atr_at_entry. Rows near the end of ``df`` (where a
        given horizon doesn't have enough forward data) are simply omitted for that
        horizon, not padded with NaN.

    Raises:
        ValueError: If ``df`` is empty or missing required columns.
    """

    if df.empty:
        raise ValueError("Input data is empty.")
    required = {"High", "Low", "Close", ATR_COLUMN}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input data is missing required columns: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    for t_pos in range(len(df)):
        date = df.index[t_pos]
        for horizon in config.horizons:
            result = label_at(df, t_pos, horizon, config)
            if result is None:
                continue
            rows.append({"date": date, "horizon": horizon, **result})

    return pd.DataFrame(rows)
