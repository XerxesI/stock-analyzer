"""LOCKED TEST: VC3-RVOL (Compression + RVOL Activation) - independent confirmation
on the Locked Test symbol sample (seed=123), per ChatGPT's precise guidance.

Legitimate re-use of the Locked Test sample: VC3's feature/hypothesis has never
been examined on seed=123 before (only M1/S1/C1/MF1-specific quantities were
computed on it). Contamination would only occur if VC-track results had already
been peeked at on this sample, or if VC3's design had been changed based on it -
neither has happened.

FROZEN specification (unchanged from the dev-sample test):
    Compression = bottom 20% compression_pct
    RVOL activation = RVOL > 1.0
    Primary regime = Bull
    Primary horizon = 20 trading days
    Primary comparison: D - C
    Secondary comparison: D - B (not required for primary success)
    Primary metrics: success-rate delta, median R delta
    Secondary profile: MFE, |MAE|, de-duplicated coverage

PRE-TEST EXPECTATION (recorded before running, to avoid post-hoc rationalizing):
    The success-rate effect is expected to replicate weakly (dev: +0.022).
    The median R effect (dev: +0.154) is NOT expected to fully replicate, since
    the dev rolling-window check showed it was heavily concentrated in one
    6-month window (2025-11) rather than distributed evenly over time.

INTERPRETATION FRAMEWORK (fixed before seeing results):
    D-C success rate delta > 0  AND  D-C median R delta > 0:
        -> REPLICATED. Proceed to practical profiling.
    D-C success rate delta > 0  AND  median R delta <= 0:
        -> CONDITIONAL/INCONCLUSIVE (possible hit-rate effect without payoff-quality).
    D-C success rate delta <= 0  AND  median R delta > 0:
        -> CONDITIONAL/INCONCLUSIVE (possible payoff-quality effect without hit-rate).
    Both <= 0:
        -> REJECTED. Close/defer the VC research track per the pre-registered
           stopping rule. Do NOT mine the Locked Test data afterward for a
           rescuing conditional rule.

Usage:
    python -m stock_analyzer.evaluation.locked_test_vc3
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

LOCKED_SAMPLE_SIZE = 300
LOCKED_SEED = 123  # same Locked Test sample as M1/S1/C1/MF1 - first use for VC3

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210
STEP_DAYS = 5

COMPRESSION_LOOKBACK = 100
COMPRESSION_QUANTILE = 0.20
RVOL_WINDOW = 20
RVOL_ACTIVATION_THRESHOLD = 1.0
LABELING_CONFIG = LabelingConfig(horizons=(10, 20))
PRIMARY_HORIZON = 20  # FROZEN
PRIMARY_REGIME = "Bull"  # FROZEN

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "locked_test_vc3_obs.csv"


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


def main() -> None:
    regime_df = _fetch_regime()

    print(f"\nsampling {LOCKED_SAMPLE_SIZE} LOCKED symbols (seed={LOCKED_SEED}, "
          f"same sample as M1/S1/C1/MF1, first use for VC3)...", flush=True)
    symbols = sample_universe(LOCKED_SAMPLE_SIZE, seed=LOCKED_SEED)

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
        try:
            comp_df = calculate_compression_state(frame, lookback=COMPRESSION_LOOKBACK)
            mf_df = calculate_money_flow_features(frame, rvol_window=RVOL_WINDOW)
        except ValueError as exc:
            print(f"  skipping {symbol}: {exc}", flush=True)
            continue

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
    print(f"\ntotal LOCKED observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

    obs = tag_observations(obs, regime_df)

    print("\n" + "=" * 90)
    print(f"LOCKED TEST: VC3-RVOL, primary regime={PRIMARY_REGIME}, primary horizon={PRIMARY_HORIZON}d")
    print("=" * 90)

    obs_h = obs[(obs["horizon"] == PRIMARY_HORIZON) & (obs["trend"] == PRIMARY_REGIME)].copy()
    threshold = obs_h["compression_pct"].quantile(COMPRESSION_QUANTILE)
    obs_h["is_compressed"] = obs_h["compression_pct"] <= threshold
    obs_h["is_rvol_active"] = obs_h["rvol"] > RVOL_ACTIVATION_THRESHOLD

    A = _cell_stats(obs_h[~obs_h["is_compressed"] & ~obs_h["is_rvol_active"]])
    B = _cell_stats(obs_h[~obs_h["is_compressed"] & obs_h["is_rvol_active"]])
    C = _cell_stats(obs_h[obs_h["is_compressed"] & ~obs_h["is_rvol_active"]])
    D = _cell_stats(obs_h[obs_h["is_compressed"] & obs_h["is_rvol_active"]])

    print(f"\n  {'':<16}{'rvol<=1':>16}{'rvol>1':>16}")
    for label, no_act, act in [("no_compression", A, B), ("compression", C, D)]:
        print(f"  {label:<16}{'n=' + str(no_act['n']):>16}{'n=' + str(act['n']):>16}")
        print(f"  {'  success:':<16}{no_act['success']:>16.3f}{act['success']:>16.3f}")
        print(f"  {'  median R:':<16}{no_act['median_r']:>+16.3f}{act['median_r']:>+16.3f}")

    d_minus_c_success = D["success"] - C["success"]
    d_minus_c_medianr = D["median_r"] - C["median_r"]
    d_minus_b_success = D["success"] - B["success"]
    d_minus_b_medianr = D["median_r"] - B["median_r"]

    print(f"\n  PRIMARY (D-C):   success_delta={d_minus_c_success:+.3f}  median_R_delta={d_minus_c_medianr:+.3f}")
    print(f"  SECONDARY (D-B): success_delta={d_minus_b_success:+.3f}  median_R_delta={d_minus_b_medianr:+.3f}")

    print(f"\n  Secondary profile (D cell): MFE={D['mfe']:+.4f}  |MAE|={D['mae_abs']:.4f}  n={D['n']}")

    print("\n" + "-" * 90)
    print("PRE-TEST EXPECTATION (recorded before this run):")
    print("  success-rate effect expected to replicate weakly (dev: +0.022)")
    print("  median R effect (dev: +0.154) NOT expected to fully replicate")
    print("-" * 90)

    print("\n" + "=" * 90)
    print("INTERPRETATION (fixed framework, applied mechanically)")
    print("=" * 90)
    success_positive = d_minus_c_success > 0
    medianr_positive = d_minus_c_medianr > 0

    if success_positive and medianr_positive:
        verdict = "REPLICATED - proceed to practical profiling."
    elif success_positive and not medianr_positive:
        verdict = "CONDITIONAL/INCONCLUSIVE - possible hit-rate effect without payoff-quality improvement."
    elif not success_positive and medianr_positive:
        verdict = "CONDITIONAL/INCONCLUSIVE - possible payoff-quality effect without hit-rate improvement."
    else:
        verdict = "REJECTED - close/defer the VC research track per the pre-registered stopping rule."

    print(f"  D-C success delta: {d_minus_c_success:+.3f} ({'positive' if success_positive else 'not positive'})")
    print(f"  D-C median R delta: {d_minus_c_medianr:+.3f} ({'positive' if medianr_positive else 'not positive'})")
    print(f"\n  VERDICT: {verdict}")
    print("\n  Reminder: cross-sectional independence only (same calendar period as")
    print("  all prior Locked Test runs). Do NOT mine this result for a new")
    print("  conditional rule if REJECTED or CONDITIONAL/INCONCLUSIVE.")
    print("=" * 90)


if __name__ == "__main__":
    main()