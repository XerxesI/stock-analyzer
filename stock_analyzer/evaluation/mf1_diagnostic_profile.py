"""MF1 (RVOL, Bull regime) practical profile: monotonicity check + ticker
concentration, same methodology as c1_diagnostic_profile.py.

DIAGNOSTIC ONLY - does not re-open the MF1a/MF1b confirmatory decision made in
locked_test_mf1.py. Does not tune the RVOL window or threshold.

Usage:
    python -m stock_analyzer.evaluation.mf1_diagnostic_profile
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_analyzer.evaluation.practical_metrics import deduplicate_events, top_quantile_lift
from stock_analyzer.evaluation.locked_test_mf1 import _fetch_regime
from stock_analyzer.validation.regime import tag_observations

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "locked_test_mf1_obs.csv"

PRIMARY_HORIZON = 20
QUANTILES = [0.30, 0.20, 0.10, 0.05]


def main() -> None:
    print(f"loading Locked Test (MF1) observations from {_OBS_PATH} ...", flush=True)
    obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])
    print(f"  {len(obs)} rows loaded, {obs['symbol'].nunique()} symbols", flush=True)

    regime_df = _fetch_regime()
    obs = tag_observations(obs, regime_df)

    obs_h = obs[obs["horizon"] == PRIMARY_HORIZON].copy()
    bull_h = obs_h[obs_h["trend"] == "Bull"].copy()
    print(f"\nBull-regime observations at horizon={PRIMARY_HORIZON}d: n={len(bull_h)}, "
          f"{bull_h['symbol'].nunique()} unique tickers", flush=True)

    baseline_rate = bull_h["success"].mean()

    print("\n" + "=" * 100)
    print("1. RVOL MONOTONICITY CHECK (within Bull, horizon=20d)")
    print("=" * 100)
    print(
        f"{'Selection':<12}{'Success rate':>14}{'Lift':>9}{'Median MFE':>13}"
        f"{'Median MAE':>13}{'Median R':>11}{'Setups (raw)':>14}{'Setups (dedup)':>16}"
    )
    print(
        f"{'All Bull':<12}{baseline_rate:>14.3f}{'1.000x':>9}"
        f"{bull_h['mfe'].median():>+13.3f}{bull_h['mae'].median():>+13.3f}"
        f"{bull_h['r_multiple'].median():>+11.3f}{len(bull_h):>14}{'-':>16}"
    )

    for q in QUANTILES:
        threshold = bull_h["rvol"].quantile(1 - q)
        top = bull_h[bull_h["rvol"] >= threshold]
        rate = top["success"].mean()
        lift = rate / baseline_rate if baseline_rate > 0 else float("nan")
        deduped = deduplicate_events(bull_h, "rvol", threshold, PRIMARY_HORIZON)
        label = f"Top {int(q * 100)}%"
        print(
            f"{label:<12}{rate:>14.3f}{lift:>8.3f}x{top['mfe'].median():>+13.3f}"
            f"{top['mae'].median():>+13.3f}{top['r_multiple'].median():>+11.3f}"
            f"{len(top):>14}{len(deduped):>16}"
        )

    print("\n" + "=" * 100)
    print("2. TICKER CONCENTRATION - RVOL top-20% within Bull (de-duplicated events)")
    print("=" * 100)
    threshold_20 = bull_h["rvol"].quantile(0.80)
    deduped_20 = deduplicate_events(bull_h, "rvol", threshold_20, PRIMARY_HORIZON)

    if len(deduped_20) == 0:
        print("  No de-duplicated events found at this threshold.")
        return

    n_unique = deduped_20["symbol"].nunique()
    setups_per_ticker = deduped_20.groupby("symbol").size().sort_values(ascending=False)
    top10_share = setups_per_ticker.head(10).sum() / len(deduped_20)
    years_covered = (bull_h["date"].max() - bull_h["date"].min()).days / 365.25

    print(f"  total de-duplicated setups: {len(deduped_20)}")
    print(f"  unique tickers contributing: {n_unique} (out of {bull_h['symbol'].nunique()} tickers that ever entered Bull)")
    print(f"  median setups per contributing ticker: {setups_per_ticker.median():.1f}")
    print(f"  max setups from a single ticker: {setups_per_ticker.max()}")
    print(f"  top-10 tickers' share of all de-duplicated setups: {top10_share:.1%}")
    print(f"  de-duplicated events per year (whole universe): {len(deduped_20) / years_covered:.1f}")
    print(f"\n  top 10 contributing tickers:")
    for sym, cnt in setups_per_ticker.head(10).items():
        print(f"    {sym:<8} {cnt} setup(s)")
    print("=" * 100)


if __name__ == "__main__":
    main()