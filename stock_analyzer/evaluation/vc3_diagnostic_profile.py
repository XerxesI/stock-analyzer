"""VC3-RVOL (D cell = Compression + RVOL activation) practical profile: does the
D-cell's advantage hold up across narrower selections, and is it broadly
distributed across tickers? Same methodology as c1_diagnostic_profile.py /
mf1_diagnostic_profile.py.

DIAGNOSTIC ONLY - does not re-open the REPLICATED verdict from locked_test_vc3.py.
Does not tune the compression/RVOL thresholds.

Usage:
    python -m stock_analyzer.evaluation.vc3_diagnostic_profile
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_analyzer.evaluation.locked_test_vc3 import (
    COMPRESSION_QUANTILE,
    PRIMARY_HORIZON,
    PRIMARY_REGIME,
    RVOL_ACTIVATION_THRESHOLD,
    _fetch_regime,
)
from stock_analyzer.evaluation.practical_metrics import deduplicate_events
from stock_analyzer.validation.regime import tag_observations

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "locked_test_vc3_obs.csv"


def main() -> None:
    print(f"loading Locked Test (VC3) observations from {_OBS_PATH} ...", flush=True)
    obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])
    print(f"  {len(obs)} rows loaded, {obs['symbol'].nunique()} symbols", flush=True)

    regime_df = _fetch_regime()
    obs = tag_observations(obs, regime_df)

    obs_h = obs[(obs["horizon"] == PRIMARY_HORIZON) & (obs["trend"] == PRIMARY_REGIME)].copy()
    threshold = obs_h["compression_pct"].quantile(COMPRESSION_QUANTILE)
    obs_h["is_compressed"] = obs_h["compression_pct"] <= threshold
    obs_h["is_rvol_active"] = obs_h["rvol"] > RVOL_ACTIVATION_THRESHOLD

    d_cell = obs_h[obs_h["is_compressed"] & obs_h["is_rvol_active"]].copy()
    c_cell = obs_h[obs_h["is_compressed"] & ~obs_h["is_rvol_active"]].copy()

    print(f"\nD cell (compression+RVOL>1) n={len(d_cell)}, "
          f"C cell (compression, RVOL<=1) n={len(c_cell)}", flush=True)

    print("\n" + "=" * 100)
    print("1. D-CELL RVOL MONOTONICITY (within compression+RVOL>1, does higher RVOL help more?)")
    print("=" * 100)
    baseline_rate = c_cell["success"].mean()
    print(f"  C cell (compression, no activation) baseline success: {baseline_rate:.3f} (n={len(c_cell)})")
    print(f"\n{'Selection':<14}{'Success rate':>14}{'Median R':>12}{'n':>8}")
    print(f"{'All D':<14}{d_cell['success'].mean():>14.3f}{d_cell['r_multiple'].median():>+12.3f}{len(d_cell):>8}")
    for q in [0.5, 0.3, 0.2]:
        rvol_threshold = d_cell["rvol"].quantile(1 - q)
        top = d_cell[d_cell["rvol"] >= rvol_threshold]
        label = f"Top {int(q*100)}% RVOL"
        print(f"{label:<14}{top['success'].mean():>14.3f}{top['r_multiple'].median():>+12.3f}{len(top):>8}")

    print("\n" + "=" * 100)
    print("2. TICKER CONCENTRATION - D cell (de-duplicated events)")
    print("=" * 100)
    deduped = deduplicate_events(d_cell, "rvol", d_cell["rvol"].min(), PRIMARY_HORIZON)

    if len(deduped) == 0:
        print("  No de-duplicated events found.")
        return

    n_unique = deduped["symbol"].nunique()
    setups_per_ticker = deduped.groupby("symbol").size().sort_values(ascending=False)
    top10_share = setups_per_ticker.head(10).sum() / len(deduped)
    years_covered = (d_cell["date"].max() - d_cell["date"].min()).days / 365.25

    print(f"  total de-duplicated D-cell setups: {len(deduped)}")
    print(f"  unique tickers contributing: {n_unique} (out of {obs_h['symbol'].nunique()} Bull-regime tickers)")
    print(f"  median setups per contributing ticker: {setups_per_ticker.median():.1f}")
    print(f"  max setups from a single ticker: {setups_per_ticker.max()}")
    print(f"  top-10 tickers' share of all de-duplicated setups: {top10_share:.1%}")
    print(f"  de-duplicated events per year (whole universe): {len(deduped) / years_covered:.1f}")
    print(f"\n  top 10 contributing tickers:")
    for sym, cnt in setups_per_ticker.head(10).items():
        print(f"    {sym:<8} {cnt} setup(s)")
    print("=" * 100)


if __name__ == "__main__":
    main()