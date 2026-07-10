"""Cycle #2: VC3-RVOL - Compression + RVOL Activation, per ChatGPT's precise
pre-registration. This is the STOPPING-RULE test for the current Volatility
Compression research track: if positive, profile practically; if negative,
defer/archive the VC track rather than testing further activation variants.

Economic question: does volatility compression become directionally useful when
relative market participation expands, without requiring a fully confirmed price
breakout (which VC2-PB showed to underperform / lag the move)?

Pre-registered before running:
    Activation rule (FROZEN): RVOL_activation = today's RVOL > 1.0 (today's volume
        exceeds its own trailing 20-day average) - NOT an extreme threshold, since
        the earlier RVOL decile analysis showed the highest RVOL is not
        monotonically best.
    Compression definition: same as VC1/VC2 - bottom 20% of compression_pct.
    Primary regime: Bull (VC1's most interesting asymmetry was there; MF1 is
        Bull-specific).
    Primary horizon: 20 trading days.
    Secondary horizon: 10 trading days.
    Primary metrics: success-rate delta, median R delta.

    ASYMMETRIC pre-registration (lesson from VC2-PB - two comparisons answer two
    different economic questions, do not require both to pass):
        PRIMARY CLAIM: D > C
            "RVOL activation improves compression state's directional outcome"
        SECONDARY (incremental) CLAIM: D > B
            "Compression adds value beyond RVOL activation alone"

2x2 design:
                    RVOL <= 1       RVOL > 1
    No compression       A               B
    Compression          C               D

Runs on the dev sample (seed=42).

Usage:
    python -m stock_analyzer.evaluation.vc3_rvol_test
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.signals.money_flow import calculate_money_flow_features
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
RVOL_WINDOW = 20
RVOL_ACTIVATION_THRESHOLD = 1.0  # FROZEN - simple, non-extreme
LABELING_CONFIG = LabelingConfig(horizons=(10, 20))
PRIMARY_HORIZON = 20
PRIMARY_REGIME = "Bull"

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "vc3_rvol_test_obs.csv"


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


def _print_2x2(obs_h: pd.DataFrame, label: str) -> tuple[dict, dict, dict, dict]:
    print(f"\n  {label}:")
    cells = {}
    for comp_label, comp_mask in [("no_compression", ~obs_h["is_compressed"]), ("compression", obs_h["is_compressed"])]:
        for rv_label, rv_mask in [("rvol<=1", ~obs_h["is_rvol_active"]), ("rvol>1", obs_h["is_rvol_active"])]:
            sub = obs_h[comp_mask & rv_mask]
            cells[(comp_label, rv_label)] = _cell_stats(sub)

    header = f"    {'':<16}{'rvol<=1':>16}{'rvol>1':>16}"
    print(header)
    for comp_label in ["no_compression", "compression"]:
        row_n = f"    {comp_label:<16}"
        row_success = f"    {'  success:':<16}"
        row_r = f"    {'  median R:':<16}"
        for rv_label in ["rvol<=1", "rvol>1"]:
            c = cells[(comp_label, rv_label)]
            row_n += f"{'n=' + str(c['n']):>16}"
            row_success += f"{c['success']:>16.3f}" if c["n"] > 0 else f"{'n/a':>16}"
            row_r += f"{c['median_r']:>+16.3f}" if c["n"] > 0 else f"{'n/a':>16}"
        print(row_n)
        print(row_success)
        print(row_r)

    A = cells[("no_compression", "rvol<=1")]
    B = cells[("no_compression", "rvol>1")]
    C = cells[("compression", "rvol<=1")]
    D = cells[("compression", "rvol>1")]

    print("\n    PRIMARY CLAIM  D > C (RVOL activation improves compression outcome)?")
    if D["n"] > 0 and C["n"] > 0:
        print(f"      success: {D['success']-C['success']:+.3f}  median_R: {D['median_r']-C['median_r']:+.3f}")
    print("    SECONDARY CLAIM  D > B (compression adds value beyond RVOL activation)?")
    if D["n"] > 0 and B["n"] > 0:
        print(f"      success: {D['success']-B['success']:+.3f}  median_R: {D['median_r']-B['median_r']:+.3f}")
    if D["n"] > 0 and A["n"] > 0:
        print(f"    (context) D vs A (baseline): success: {D['success']-A['success']:+.3f}  median_R: {D['median_r']-A['median_r']:+.3f}")

    print("\n    Secondary (MFE / |MAE|) by cell:")
    for comp_label in ["no_compression", "compression"]:
        for rv_label in ["rvol<=1", "rvol>1"]:
            c = cells[(comp_label, rv_label)]
            if c["n"] > 0:
                print(f"      {comp_label}/{rv_label}: MFE={c['mfe']:+.4f}  |MAE|={c['mae_abs']:.4f}  n={c['n']}")

    return A, B, C, D


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
    print(f"\ncomputing compression + RVOL state + labels (step={STEP_DAYS} bars)...", flush=True)
    for i, (symbol, frame) in enumerate(frames.items()):
        comp_df = calculate_compression_state(frame, lookback=COMPRESSION_LOOKBACK)
        mf_df = calculate_money_flow_features(frame, rvol_window=RVOL_WINDOW)

        max_t = len(frame) - max(LABELING_CONFIG.horizons) - 1
        for t_pos in range(TEST_START_POS, max_t, STEP_DAYS):
            date = frame.index[t_pos]
            comp_val = comp_df["compression_pct"].iloc[t_pos]
            rvol_val = mf_df["rvol"].iloc[t_pos]
            if pd.isna(comp_val) or pd.isna(rvol_val):
                continue
            for horizon in LABELING_CONFIG.horizons:
                label = label_at(frame, t_pos, horizon, LABELING_CONFIG)
                if label is None:
                    continue
                rows.append({
                    "symbol": symbol, "date": date, "horizon": horizon, **label,
                    "compression_pct": comp_val, "rvol": rvol_val,
                })
        if (i + 1) % 30 == 0:
            print(f"  processed {i + 1}/{len(frames)} symbols", flush=True)

    _ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
    obs = pd.DataFrame(rows)
    obs.to_csv(_OBS_PATH, index=False)
    print(f"\ntotal observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

    obs = tag_observations(obs, regime_df)

    print("\n" + "=" * 100)
    print("VC3-RVOL: 2x2 (Compression x RVOL activation) state comparison")
    print(f"Primary regime: {PRIMARY_REGIME}, Primary horizon: {PRIMARY_HORIZON}d")
    print("=" * 100)

    verdicts = {}
    for horizon in sorted(obs["horizon"].unique()):
        tag = "PRIMARY" if horizon == PRIMARY_HORIZON else "secondary"
        print(f"\n{'#' * 100}")
        print(f"# HORIZON = {horizon}d ({tag})")
        print(f"{'#' * 100}")

        obs_h = obs[obs["horizon"] == horizon].copy()
        threshold = obs_h["compression_pct"].quantile(COMPRESSION_QUANTILE)
        obs_h["is_compressed"] = obs_h["compression_pct"] <= threshold
        obs_h["is_rvol_active"] = obs_h["rvol"] > RVOL_ACTIVATION_THRESHOLD

        for trend_val in ["Bull", "Bear"]:
            sub = obs_h[obs_h["trend"] == trend_val]
            if len(sub) < 200:
                print(f"\n  {trend_val}: insufficient data (n={len(sub)})")
                continue
            A, B, C, D = _print_2x2(sub, trend_val)
            if horizon == PRIMARY_HORIZON and trend_val == PRIMARY_REGIME:
                verdicts["primary_D_gt_C_success"] = D["success"] - C["success"]
                verdicts["primary_D_gt_C_medianR"] = D["median_r"] - C["median_r"]
                verdicts["secondary_D_gt_B_success"] = D["success"] - B["success"]
                verdicts["secondary_D_gt_B_medianR"] = D["median_r"] - B["median_r"]

    print("\n" + "=" * 100)
    print("STOPPING-RULE SUMMARY (primary regime/horizon only)")
    print("=" * 100)
    if verdicts:
        primary_pass = verdicts["primary_D_gt_C_success"] > 0 and verdicts["primary_D_gt_C_medianR"] > 0
        secondary_pass = verdicts["secondary_D_gt_B_success"] > 0 and verdicts["secondary_D_gt_B_medianR"] > 0
        print(f"  PRIMARY (D>C):   success_delta={verdicts['primary_D_gt_C_success']:+.3f}  "
              f"median_R_delta={verdicts['primary_D_gt_C_medianR']:+.3f}  -> {'PASS' if primary_pass else 'FAIL'}")
        print(f"  SECONDARY (D>B): success_delta={verdicts['secondary_D_gt_B_success']:+.3f}  "
              f"median_R_delta={verdicts['secondary_D_gt_B_medianR']:+.3f}  -> {'PASS' if secondary_pass else 'FAIL'}")
        print()
        if primary_pass:
            print("  VC3-RVOL PRIMARY CLAIM PASSES on dev sample -> profile practically,")
            print("  consider rolling-window stability check before Locked Test.")
        else:
            print("  VC3-RVOL PRIMARY CLAIM FAILS on dev sample -> per the pre-registered")
            print("  stopping rule, defer/archive the VC research track.")
    else:
        print("  Insufficient data in primary regime/horizon to render a verdict.")
    print("=" * 100)


if __name__ == "__main__":
    main()
