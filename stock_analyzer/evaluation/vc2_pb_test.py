"""Cycle #2: VC2-PB - Compression + Bullish Breakout, per ChatGPT's precise
pre-registration.

Hypothesis VC2-PB: after a volatility-compression state, a bullish breakout above
the prior 20-day high identifies setups with better 20-day triple-barrier outcomes
than compressed observations without a breakout.

Pre-registered before running:
    Breakout definition (FROZEN, no alternatives tested): Close > prior 20-day
        High (today's bar excluded from the rolling high calculation - i.e.
        High.shift(1).rolling(20).max())
    Compression definition: same as VC1 - bottom 20% of compression_pct
    Primary horizon: 20 trading days
    Secondary horizon: 10 trading days
    Primary metrics: success-rate delta, median R delta
    Secondary metrics: MFE, |MAE|, de-duplicated setup count, coverage

Test structure: a 2x2 state comparison, NOT just "compression+breakout vs everyone":

                    No breakout    Bullish breakout
    No compression       A               B
    Compression          C               D

Key questions: is D-C > 0 (breakout helps within compression)? Is D-B > 0
(compression improves breakout quality)? This distinguishes a true interaction
from a generic breakout effect.

Runs on the dev sample (seed=42).

Usage:
    python -m stock_analyzer.evaluation.vc2_pb_test
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.signals.volatility_compression import calculate_compression_state
from stock_analyzer.validation.labeling import LabelingConfig, label_at
from stock_analyzer.validation.regime import build_market_regime, tag_observations

DEV_SAMPLE_SIZE = 300
DEV_SEED = 42

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210
STEP_DAYS = 5

COMPRESSION_LOOKBACK = 100
COMPRESSION_QUANTILE = 0.20
BREAKOUT_WINDOW = 20  # FROZEN - prior 20-day high, no alternatives tested
LABELING_CONFIG = LabelingConfig(horizons=(10, 20))
PRIMARY_HORIZON = 20

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "vc2_pb_test_obs.csv"


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


def _cell_stats(sub: pd.DataFrame) -> dict:
    if len(sub) == 0:
        return {"n": 0, "success": float("nan"), "median_r": float("nan"), "mfe": float("nan"), "mae_abs": float("nan")}
    return {
        "n": len(sub),
        "success": sub["success"].mean(),
        "median_r": sub["r_multiple"].median(),
        "mfe": sub["mfe"].mean(),
        "mae_abs": sub["mae"].abs().mean(),
    }


def _print_2x2(obs_h: pd.DataFrame, label: str) -> None:
    print(f"\n  {label}:")
    cells = {}
    for comp_label, comp_mask in [("no_compression", ~obs_h["is_compressed"]), ("compression", obs_h["is_compressed"])]:
        for bo_label, bo_mask in [("no_breakout", ~obs_h["is_breakout"]), ("breakout", obs_h["is_breakout"])]:
            sub = obs_h[comp_mask & bo_mask]
            cells[(comp_label, bo_label)] = _cell_stats(sub)

    header = f"    {'':<16}{'no_breakout':>16}{'breakout':>16}"
    print(header)
    for comp_label in ["no_compression", "compression"]:
        row_n = f"    {comp_label:<16}"
        row_success = f"    {'  success:':<16}"
        row_r = f"    {'  median R:':<16}"
        for bo_label in ["no_breakout", "breakout"]:
            c = cells[(comp_label, bo_label)]
            row_n += f"{'n=' + str(c['n']):>16}"
            row_success += f"{c['success']:>16.3f}" if c["n"] > 0 else f"{'n/a':>16}"
            row_r += f"{c['median_r']:>+16.3f}" if c["n"] > 0 else f"{'n/a':>16}"
        print(row_n)
        print(row_success)
        print(row_r)

    A = cells[("no_compression", "no_breakout")]
    B = cells[("no_compression", "breakout")]
    C = cells[("compression", "no_breakout")]
    D = cells[("compression", "breakout")]

    print("\n    Key incremental-information questions:")
    if D["n"] > 0 and C["n"] > 0:
        print(f"      D-C (breakout helps WITHIN compression)? success: {D['success']-C['success']:+.3f}  median_R: {D['median_r']-C['median_r']:+.3f}")
    if D["n"] > 0 and B["n"] > 0:
        print(f"      D-B (compression improves breakout quality)? success: {D['success']-B['success']:+.3f}  median_R: {D['median_r']-B['median_r']:+.3f}")
    if D["n"] > 0 and A["n"] > 0:
        print(f"      D-A (compression+breakout vs baseline)? success: {D['success']-A['success']:+.3f}  median_R: {D['median_r']-A['median_r']:+.3f}")

    print("\n    Secondary (MFE / |MAE|) by cell:")
    for comp_label in ["no_compression", "compression"]:
        for bo_label in ["no_breakout", "breakout"]:
            c = cells[(comp_label, bo_label)]
            if c["n"] > 0:
                print(f"      {comp_label}/{bo_label}: MFE={c['mfe']:+.4f}  |MAE|={c['mae_abs']:.4f}  n={c['n']}")


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
    print(f"\ncomputing compression + breakout state + labels (step={STEP_DAYS} bars)...", flush=True)
    for i, (symbol, frame) in enumerate(frames.items()):
        comp_df = calculate_compression_state(frame, lookback=COMPRESSION_LOOKBACK)
        prior_high = frame["High"].shift(1).rolling(BREAKOUT_WINDOW).max()
        is_breakout_series = frame["Close"] > prior_high

        max_t = len(frame) - max(LABELING_CONFIG.horizons) - 1
        for t_pos in range(TEST_START_POS, max_t, STEP_DAYS):
            date = frame.index[t_pos]
            comp_val = comp_df["compression_pct"].iloc[t_pos]
            breakout_val = is_breakout_series.iloc[t_pos]
            if pd.isna(comp_val) or pd.isna(breakout_val):
                continue
            for horizon in LABELING_CONFIG.horizons:
                label = label_at(frame, t_pos, horizon, LABELING_CONFIG)
                if label is None:
                    continue
                rows.append({
                    "symbol": symbol, "date": date, "horizon": horizon, **label,
                    "compression_pct": comp_val, "is_breakout": bool(breakout_val),
                })
        if (i + 1) % 30 == 0:
            print(f"  processed {i + 1}/{len(frames)} symbols", flush=True)

    _ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
    obs = pd.DataFrame(rows)
    obs.to_csv(_OBS_PATH, index=False)
    print(f"\ntotal observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

    obs = tag_observations(obs, regime_df)

    print("\n" + "=" * 100)
    print("VC2-PB: 2x2 (Compression x Breakout) state comparison")
    print("=" * 100)

    for horizon in sorted(obs["horizon"].unique()):
        tag = "PRIMARY" if horizon == PRIMARY_HORIZON else "secondary"
        print(f"\n{'#' * 100}")
        print(f"# HORIZON = {horizon}d ({tag})")
        print(f"{'#' * 100}")

        obs_h = obs[obs["horizon"] == horizon].copy()
        threshold = obs_h["compression_pct"].quantile(COMPRESSION_QUANTILE)
        obs_h["is_compressed"] = obs_h["compression_pct"] <= threshold

        _print_2x2(obs_h, "Overall (Bull+Bear)")

        for trend_val in ["Bull", "Bear"]:
            sub = obs_h[obs_h["trend"] == trend_val]
            if len(sub) < 200:
                print(f"\n  {trend_val}: insufficient data (n={len(sub)})")
                continue
            _print_2x2(sub, trend_val)

    print("\n" + "=" * 100)
    print("Reminder: this is EXPLORATORY (dev sample, seed=42). If D-C and D-B are")
    print("both positive and directionally consistent, VC2-PB is a candidate for a")
    print("rolling-window stability check before any Locked Test confirmation.")
    print("=" * 100)


if __name__ == "__main__":
    main()
