"""Rolling-window diagnostic for the Money Flow signals (RVOL, OBV_SLOPE, AD_SLOPE).
Same methodology as rs_rolling_window.py.

Two specific things to check:
    1. Is RVOL's striking train/holdout stability (Bull consistently positive,
       Bear consistently negative, across ALL horizons) also stable across time
       bins, or could it still be an artifact of just two large aggregate windows?
    2. Is obv_slope's Bull-regime sign flip (train -0.019 -> holdout +0.074) a
       gradual drift or a narrow episode, same question as we asked for RS.

DIAGNOSTIC ONLY - operates on the already-saved money_flow_signal_test_obs.csv (dev
sample, seed=42). Does NOT touch the Locked Test set.

Usage:
    python -m stock_analyzer.evaluation.money_flow_rolling_window
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.validation.ic_test import spearman_ic
from stock_analyzer.validation.regime import build_market_regime, tag_observations

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "money_flow_signal_test_obs.csv"

PRIMARY_HORIZON = 20
SIGNAL_COLS = ["rvol", "obv_slope", "ad_slope"]


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
    print("=" * 90)

    for signal_col in SIGNAL_COLS:
        print(f"\n{'#' * 90}")
        print(f"# SIGNAL: {signal_col}")
        print(f"{'#' * 90}")

        print("\n-- Rolling IC, Bull-regime rows only --")
        rw_bull = rolling_window_ic(bull_h, signal_col, "r_multiple")
        for _, row in rw_bull.iterrows():
            print(f"    {row['period_end']}: IC={row['ic']:+.4f} (n={row['n']})")

        print("\n-- Rolling IC, Bear-regime rows only --")
        rw_bear = rolling_window_ic(bear_h, signal_col, "r_multiple")
        for _, row in rw_bear.iterrows():
            print(f"    {row['period_end']}: IC={row['ic']:+.4f} (n={row['n']})")

    print("\n" + "=" * 90)
    print("Interpretation guide:")
    print("  - RVOL: if Bull bins are consistently positive and Bear bins consistently")
    print("    negative across MOST windows (not just the aggregate train/holdout split),")
    print("    this is strong evidence of a genuine, stable, regime-conditional signal -")
    print("    the best candidate yet for a Locked Test hypothesis.")
    print("  - obv_slope: check whether the Bull sign flip is gradual or concentrated")
    print("    in one recent bin (same caution as the RS diagnostic).")
    print("  - Still DIAGNOSTIC ONLY - a clean rolling pattern justifies WRITING a precise")
    print("    pre-registered hypothesis next, not skipping straight to Locked Test.")
    print("=" * 90)


if __name__ == "__main__":
    main()