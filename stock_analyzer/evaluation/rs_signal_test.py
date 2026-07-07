"""Cycle #2 Step 1: Relative Strength signal lab, per ChatGPT's Research Protocol
v1.2 Phase 4 guidance.

Tests RS1 (stock vs SPY), RS3 (RS slope), RS4 (RS acceleration) as INDIVIDUAL
signals (not a composite RS score) against the triple-barrier target, on the dev
sample (seed=42 - same 300-symbol universe as Cycle #1's exploratory work; this is
a NEW hypothesis being explored for the first time, so re-using the dev data for
exploration is appropriate - the Locked Test, seed=123, remains reserved for a
future confirmatory pass, once/if RS shows an independent effect worth confirming).

Pre-registered before running:
    Primary lookback: 20 trading days (RS1's window)
    Primary horizon: 20 trading days (matches the triple-barrier's primary horizon)
    Secondary horizons: 10 and 40 trading days (NOT 5 - ChatGPT specifically asked
      for 10/40 as secondary, to avoid re-opening the wider horizon sweep and
      picking whichever looks best after the fact)
    Primary target: R-multiple (Spearman IC)
    Broken out by: Bull vs Bear regime (SPY vs SMA200), since Cycle #1 established
      regime-conditioning as the default expectation, not an afterthought

RS2 (vs sector) is NOT tested here - deferred, see signals/relative_strength.py's
docstring for why.

Usage:
    python -m stock_analyzer.evaluation.rs_signal_test
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.signals.relative_strength import calculate_relative_strength
from stock_analyzer.validation.ic_test import run_walk_forward_ic, spearman_ic, split_train_holdout
from stock_analyzer.validation.labeling import LabelingConfig, label_at
from stock_analyzer.validation.regime import build_market_regime, tag_observations

DEV_SAMPLE_SIZE = 300
DEV_SEED = 42  # SAME dev sample as Cycle #1 - RS is a new hypothesis, not yet explored here

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210
STEP_DAYS = 5

RS_LOOKBACK = 20  # primary, pre-registered
RS_SLOPE_WINDOW = 5
LABELING_CONFIG = LabelingConfig(horizons=(10, 20, 40))  # NOT 5d - per pre-registration
PRIMARY_HORIZON = 20

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "rs_signal_test_obs.csv"


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


def _fetch_spy_and_regime() -> tuple[pd.Series, pd.DataFrame]:
    print("fetching SPY (+ attempting ^VIX) for RS benchmark and regime...", flush=True)
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

    regime_df = build_market_regime(spy_enriched, vix_close=vix_close)
    return spy_enriched["Close"], regime_df


def main() -> None:
    spy_close, regime_df = _fetch_spy_and_regime()

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
    print(f"\ncomputing RS features + labels (step={STEP_DAYS} bars)...", flush=True)
    for i, (symbol, frame) in enumerate(frames.items()):
        rs_df = calculate_relative_strength(frame["Close"], spy_close, lookback=RS_LOOKBACK, slope_window=RS_SLOPE_WINDOW)
        max_t = len(frame) - max(LABELING_CONFIG.horizons) - 1
        for t_pos in range(TEST_START_POS, max_t, STEP_DAYS):
            date = frame.index[t_pos]
            rs_row = rs_df.iloc[t_pos]
            if rs_row.isna().any():
                continue
            for horizon in LABELING_CONFIG.horizons:
                label = label_at(frame, t_pos, horizon, LABELING_CONFIG)
                if label is None:
                    continue
                rows.append({
                    "symbol": symbol, "date": date, "horizon": horizon, **label,
                    "rs1_vs_spy": rs_row["rs"], "rs_slope": rs_row["rs_slope"], "rs_accel": rs_row["rs_accel"],
                })
        if (i + 1) % 30 == 0:
            print(f"  processed {i + 1}/{len(frames)} symbols", flush=True)

    _ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
    obs = pd.DataFrame(rows)
    obs.to_csv(_OBS_PATH, index=False)
    print(f"\ntotal observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

    obs = tag_observations(obs, regime_df)

    signal_cols = ["rs1_vs_spy", "rs_slope", "rs_accel"]

    print("\n" + "=" * 90)
    print("RELATIVE STRENGTH SIGNAL LAB - walk-forward IC, train vs hold-out (80/20)")
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
    print("confirmation based on this run alone.")
    print("=" * 90)


if __name__ == "__main__":
    main()
