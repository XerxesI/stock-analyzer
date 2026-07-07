"""Rolling-window diagnostic for the RS signals, specifically to understand the
Bear-regime sign reversal seen between train and holdout (rs_slope: +0.067 -> -0.095,
rs_accel: +0.060 -> -0.152). Same methodology as phase2_deep_dive.py's rolling window
check for Momentum/RSI.

DIAGNOSTIC ONLY - operates on the already-saved rs_signal_test_obs.csv (dev sample,
seed=42). Does NOT touch the Locked Test set. Does NOT constitute a decision to
proceed to confirmation - the purpose is purely to understand whether the observed
instability is a gradual drift (as with Momentum/RSI) or a narrow, possibly noisy,
single episode.

Usage:
    python -m stock_analyzer.evaluation.rs_rolling_window
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.validation.ic_test import spearman_ic
from stock_analyzer.validation.regime import build_market_regime, tag_observations

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "rs_signal_test_obs.csv"

PRIMARY_HORIZON = 20
SIGNAL_COLS = ["rs1_vs_spy", "rs_slope", "rs_accel"]


def _fetch_regime() -> pd.DataFrame:
    print("re-fetching SPY (+ attempting ^VIX) for regime tagging...", flush=True)
    spy_raw = yf.download(
        "SPY",
        start=(pd.Timestamp.today() - pd.Timedelta(days=3 * 365)).to_pydatetime(),
        end=(pd.Timestamp.today() + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20,
    )
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    spy_raw.index = pd.to_datetime(spy_raw.index).tz_localize(None)
    spy_enriched = calculate_indicators(spy_raw.sort_index())

    vix_close = None
    try:
        vix_raw = yf.download(
            "^VIX",
            start=(pd.Timestamp.today() - pd.Timedelta(days=3 * 365)).to_pydatetime(),
            end=(pd.Timestamp.today() + pd.Timedelta(days=1)).to_pydatetime(),
            interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20,
        )
        if isinstance(vix_raw.columns, pd.MultiIndex):
            vix_raw.columns = vix_raw.columns.get_level_values(0)
        if not vix_raw.empty:
            vix_raw.index = pd.to_datetime(vix_raw.index).tz_localize(None)
            vix_close = vix_raw["Close"]
    except Exception:  # noqa: BLE001
        vix_close = None

    return build_market_regime(spy_enriched, vix_close=vix_close)


def rolling_window_ic(df: pd.DataFrame, signal_col: str, target_col: str, date_col: str = "date") -> pd.DataFrame:
    rows = []
    grouped = df.set_index(date_col).groupby(pd.Grouper(freq="6ME"))
    for period_end, group in grouped:
        if group.empty:
            continue
        valid = group[[signal_col, target_col]].dropna()
        ic = spearman_ic(group[signal_col], group[target_col])
        rows.append({"period_end": period_end.date(), "ic": ic, "n": len(valid)})
    return pd.DataFrame(rows)


def main() -> None:
    print(f"loading saved observations from {_OBS_PATH} ...", flush=True)
    obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])
    print(f"  {len(obs)} rows loaded, {obs['symbol'].nunique()} symbols", flush=True)

    regime_df = _fetch_regime()
    obs = tag_observations(obs, regime_df)

    obs_h = obs[obs["horizon"] == PRIMARY_HORIZON]
    bear_h = obs_h[obs_h["trend"] == "Bear"]
    bull_h = obs_h[obs_h["trend"] == "Bull"]

    print("\n" + "=" * 90)
    print(f"ROLLING WINDOW IC (6-month bins), horizon={PRIMARY_HORIZON}d, IC vs r_multiple")
    print("Checks whether the train/holdout Bear sign-flip is gradual drift or one episode")
    print("=" * 90)

    for signal_col in SIGNAL_COLS:
        print(f"\n{'#' * 90}")
        print(f"# SIGNAL: {signal_col}")
        print(f"{'#' * 90}")

        print("\n-- Rolling IC, Bear-regime rows only --")
        rw_bear = rolling_window_ic(bear_h, signal_col, "r_multiple")
        for _, row in rw_bear.iterrows():
            print(f"    {row['period_end']}: IC={row['ic']:+.4f} (n={row['n']})")

        print("\n-- Rolling IC, Bull-regime rows only --")
        rw_bull = rolling_window_ic(bull_h, signal_col, "r_multiple")
        for _, row in rw_bull.iterrows():
            print(f"    {row['period_end']}: IC={row['ic']:+.4f} (n={row['n']})")

    print("\n" + "=" * 90)
    print("Interpretation guide:")
    print("  - Gradual sign change across several consecutive bins (like Momentum/RSI's")
    print("    earlier -0.09 -> -0.03 -> +0.05 -> +0.05 progression): consistent with a")
    print("    genuine, slow regime-driven shift.")
    print("  - A flip concentrated in just the LAST bin, especially with a small n there:")
    print("    consistent with a narrow, possibly noisy episode - weaker basis for any")
    print("    hypothesis.")
    print("  - Still DIAGNOSTIC ONLY. Do not register a new hypothesis from this table")
    print("    without also checking effective sample size / autocorrelation caveats.")
    print("=" * 90)


if __name__ == "__main__":
    main()