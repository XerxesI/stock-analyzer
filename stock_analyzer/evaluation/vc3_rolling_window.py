"""Rolling-window stability check for VC3-RVOL's PRIMARY claim (D>C: RVOL
activation improves compression state's directional outcome), per the project's
established discipline (same pattern as rs_rolling_window.py /
money_flow_rolling_window.py) - required before any Locked Test confirmation,
since VC3-RVOL's primary claim passed the pre-registered stopping-rule test.

DIAGNOSTIC ONLY - operates on the already-saved vc3_rvol_test_obs.csv (dev sample,
seed=42, Bull regime, horizon=20d only - the primary cell). Does NOT touch the
Locked Test set. Does NOT tune the compression/RVOL thresholds.

Usage:
    python -m stock_analyzer.evaluation.vc3_rolling_window
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.validation.regime import build_market_regime, tag_observations

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "vc3_rvol_test_obs.csv"

COMPRESSION_QUANTILE = 0.20
RVOL_ACTIVATION_THRESHOLD = 1.0
PRIMARY_HORIZON = 20


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


def main() -> None:
    print(f"loading saved observations from {_OBS_PATH} ...", flush=True)
    obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])
    print(f"  {len(obs)} rows loaded, {obs['symbol'].nunique()} symbols", flush=True)

    regime_df = _fetch_regime()
    obs = tag_observations(obs, regime_df)

    obs_h = obs[(obs["horizon"] == PRIMARY_HORIZON) & (obs["trend"] == "Bull")].copy()
    threshold = obs_h["compression_pct"].quantile(COMPRESSION_QUANTILE)
    obs_h["is_compressed"] = obs_h["compression_pct"] <= threshold
    obs_h["is_rvol_active"] = obs_h["rvol"] > RVOL_ACTIVATION_THRESHOLD

    print(f"\nBull-regime observations (horizon={PRIMARY_HORIZON}d): n={len(obs_h)}", flush=True)

    print("\n" + "=" * 90)
    print("VC3-RVOL PRIMARY CLAIM (D>C) - 6-month rolling window stability check")
    print("=" * 90)

    grouped = obs_h.set_index("date").groupby(pd.Grouper(freq="6ME"))
    for period_end, group in grouped:
        if group.empty:
            continue
        C = group[group["is_compressed"] & ~group["is_rvol_active"]]
        D = group[group["is_compressed"] & group["is_rvol_active"]]
        if len(C) < 20 or len(D) < 20:
            print(f"    {period_end.date()}: insufficient data (C n={len(C)}, D n={len(D)})")
            continue
        success_delta = D["success"].mean() - C["success"].mean()
        median_r_delta = D["r_multiple"].median() - C["r_multiple"].median()
        print(
            f"    {period_end.date()}: D-C success={success_delta:+.3f}  "
            f"D-C median_R={median_r_delta:+.3f}  (n_C={len(C)}, n_D={len(D)})"
        )

    print("\n" + "-" * 90)
    print("Interpretation guide:")
    print("  - Gradual/consistent positive D-C across most windows: supports a real,")
    print("    stable interaction effect - candidate for Locked Test confirmation.")
    print("  - Erratic sign flips (like RS_slope/RS_accel's rolling check): the")
    print("    aggregate PRIMARY PASS may be driven by one or two episodes, not a")
    print("    stable effect - would need caution before Locked Test.")
    print("=" * 90)


if __name__ == "__main__":
    main()