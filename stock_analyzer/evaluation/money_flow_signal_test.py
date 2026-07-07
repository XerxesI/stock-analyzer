"""Cycle #2 Step 3: Money Flow signal lab, per ChatGPT's Research Protocol v1.2
Phase 4 guidance.

Tests RVOL, OBV_SLOPE, AD_SLOPE as INDIVIDUAL signals (not a composite) against
the triple-barrier target, on the dev sample (seed=42 - same 300-symbol universe
as the rest of Cycle #1/#2's exploratory work).

Pre-registered before running:
    Primary lookback: RVOL window=20d, OBV/AD slope window=10d
    Primary horizon: 20 trading days (matches all prior Cycle #1/#2 tests)
    Secondary horizons: 10 and 40 trading days (NOT 5 - same discipline as the RS test)
    Primary target: R-multiple (Spearman IC)
    Broken out by: Bull vs Bear regime (SPY vs SMA200)

Usage:
    python -m stock_analyzer.evaluation.money_flow_signal_test
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.signals.money_flow import calculate_money_flow_features
from stock_analyzer.validation.ic_test import run_walk_forward_ic, spearman_ic, split_train_holdout
from stock_analyzer.validation.labeling import LabelingConfig, label_at
from stock_analyzer.validation.regime import build_market_regime, tag_observations

DEV_SAMPLE_SIZE = 300
DEV_SEED = 42  # SAME dev sample as Cycle #1/#2 RS test

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210
STEP_DAYS = 5

RVOL_WINDOW = 20
SLOPE_WINDOW = 10
LABELING_CONFIG = LabelingConfig(horizons=(10, 20, 40))  # NOT 5d - matches RS test's discipline
PRIMARY_HORIZON = 20

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "money_flow_signal_test_obs.csv"


def _fetch(symbol: str) -> pd.DataFrame:
    raw = yf.download(
        symbol, start=FETCH_START.to_pydatetime(), end=(FETCH_END + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20,
    )
    if raw.empty:
        raise ValueError("empty")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.loc[:, ["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    return calculate_indicators(raw.sort_index())


def _fetch_regime() -> pd.DataFrame:
    print("fetching SPY (+ attempting ^VIX) for regime...", flush=True)
    spy_raw = yf.download(
        "SPY", start=FETCH_START.to_pydatetime(), end=(FETCH_END + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20,
    )
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    spy_raw.index = pd.to_datetime(spy_raw.index).tz_localize(None)
    spy_enriched = calculate_indicators(spy_raw.sort_index())

    vix_close = None
    try:
        vix_raw = yf.download(
            "^VIX", start=FETCH_START.to_pydatetime(), end=(FETCH_END + pd.Timedelta(days=1)).to_pydatetime(),
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


def main() -> None:
    regime_df = _fetch_regime()

    print(f"\nsampling {DEV_SAMPLE_SIZE} symbols (seed={DEV_SEED}, dev sample)...", flush=True)
    symbols = sample_universe(DEV_SAMPLE_SIZE, seed=DEV_SEED)

    print(f"fetching {len(symbols)} symbols...", flush=True)
    frames: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch, s): s for s in symbols}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                frames[s] = fut.result()
            except Exception:  # noqa: BLE001
                pass
    print(f"  loaded {len(frames)}/{len(symbols)} price frames", flush=True)

    rows: list[dict] = []
    print(f"\ncomputing Money Flow features + labels (step={STEP_DAYS} bars)...", flush=True)
    for i, (symbol, frame) in enumerate(frames.items()):
        mf_df = calculate_money_flow_features(frame, rvol_window=RVOL_WINDOW, slope_window=SLOPE_WINDOW)
        max_t = len(frame) - max(LABELING_CONFIG.horizons) - 1
        for t_pos in range(TEST_START_POS, max_t, STEP_DAYS):
            date = frame.index[t_pos]
            mf_row = mf_df.iloc[t_pos]
            if mf_row.isna().any():
                continue
            for horizon in LABELING_CONFIG.horizons:
                label = label_at(frame, t_pos, horizon, LABELING_CONFIG)
                if label is None:
                    continue
                rows.append({
                    "symbol": symbol, "date": date, "horizon": horizon, **label,
                    "rvol": mf_row["rvol"], "obv_slope": mf_row["obv_slope"], "ad_slope": mf_row["ad_slope"],
                })
        if (i + 1) % 30 == 0:
            print(f"  processed {i + 1}/{len(frames)} symbols", flush=True)

    _ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
    obs = pd.DataFrame(rows)
    obs.to_csv(_OBS_PATH, index=False)
    print(f"\ntotal observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

    obs = tag_observations(obs, regime_df)

    signal_cols = ["rvol", "obv_slope", "ad_slope"]

    print("\n" + "=" * 90)
    print("MONEY FLOW SIGNAL LAB - walk-forward IC, train vs hold-out (80/20)")
    print("=" * 90)
    for signal_col in signal_cols:
        print(f"\n{'#' * 90}")
        print(f"# SIGNAL: {signal_col}")
        print(f"{'#' * 90}")

        print("\n-- IC vs r_multiple, train / hold-out, per horizon (primary=20d, secondary=10d/40d) --")
        results = run_walk_forward_ic(obs, signal_col=signal_col, target_col="r_multiple")
        for r in results:
            tag = " (PRIMARY)" if r.horizon == PRIMARY_HORIZON else " (secondary)"
            print(
                f"  horizon={r.horizon:>3}d{tag:<12} train_ic={r.train_ic:+.4f} (n={r.train_n:>6})"
                f"   holdout_ic={r.holdout_ic:+.4f} (n={r.holdout_n:>6})"
            )

        print("\n-- IC vs r_multiple by regime, horizon=20d (diagnostic, pre-registered: Bull vs Bear) --")
        obs_h = obs[obs["horizon"] == PRIMARY_HORIZON]
        train_h, holdout_h = split_train_holdout(obs_h)
        for period_name, period_df in [("train", train_h), ("holdout", holdout_h)]:
            print(f"  {period_name}:")
            for trend_val in ["Bull", "Bear"]:
                sub = period_df[period_df["trend"] == trend_val]
                if len(sub) < 30:
                    print(f"    {trend_val:<6} insufficient data (n={len(sub)})")
                    continue
                ic = spearman_ic(sub[signal_col], sub["r_multiple"])
                print(f"    {trend_val:<6} IC={ic:+.4f} (n={len(sub)})")

    print("\n" + "=" * 90)
    print("Reminder: this is EXPLORATORY (dev sample, seed=42). Any signal showing a")
    print("consistent, pre-registerable pattern here should get its OWN precise")
    print("hypothesis statement before any Locked Test confirmation - do not skip to")
    print("confirmation based on this run alone. Also run a rolling-window check")
    print("(same pattern as rs_rolling_window.py) before trusting any train/holdout")
    print("regime-conditional pattern that emerges here.")
    print("=" * 90)


if __name__ == "__main__":
    main()
