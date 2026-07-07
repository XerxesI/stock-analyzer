"""C1 practical profile completion: monotonicity check across quantile thresholds
+ ticker concentration diagnostic, per ChatGPT's Research Cycle #2 step 1.

DIAGNOSTIC ONLY:
    - Does NOT re-open the confirmatory decision made in locked_test.py.
    - Does NOT tune/optimize the frozen C1 formula (frozen_c1_params.json is loaded
      as-is, never refit here).
    - Answers two questions: (1) does the edge increase monotonically as the
      selection narrows (Top 30% -> 20% -> 10% -> 5%), which is evidence the C1
      ranking itself is informative rather than a threshold artifact; and (2) is
      the edge concentrated in a handful of tickers (which would mean "C1 works"
      is really "a few volatile names dominate", not a general Bear-regime effect).

Usage:
    python -m stock_analyzer.evaluation.c1_diagnostic_profile
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from stock_analyzer.evaluation.practical_metrics import _fetch_regime, deduplicate_events
from stock_analyzer.validation.regime import tag_observations

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "locked_test_obs.csv"
_FROZEN_PARAMS_PATH = _ARTIFACTS_REPORTS / "frozen_c1_params.json"

PRIMARY_HORIZON = 20
QUANTILES = [0.30, 0.20, 0.10, 0.05]


def main() -> None:
    print(f"loading Locked Test observations from {_OBS_PATH} ...", flush=True)
    obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])

    with open(_FROZEN_PARAMS_PATH) as f:
        frozen = json.load(f)
    print("loaded FROZEN C1 parameters (not refit here):", flush=True)
    for k in ("mu_support", "sigma_support", "mu_momentum", "sigma_momentum", "bear_regimes"):
        print(f"  {k}: {frozen[k]}")

    regime_df = _fetch_regime()
    obs = tag_observations(obs, regime_df)

    obs["z_support"] = (obs["support_signal"] - frozen["mu_support"]) / frozen["sigma_support"]
    obs["z_momentum"] = (obs["momentum_signal"] - frozen["mu_momentum"]) / frozen["sigma_momentum"]
    obs["is_bear"] = obs["regime"].isin(frozen["bear_regimes"]).astype(float)
    obs["c1_score"] = obs["z_support"] + obs["z_momentum"] * obs["is_bear"]

    obs_h = obs[obs["horizon"] == PRIMARY_HORIZON].copy()
    bear_h = obs_h[obs_h["trend"] == "Bear"].copy()
    print(f"\nBear-regime observations at horizon={PRIMARY_HORIZON}d: n={len(bear_h)}, "
          f"{bear_h['symbol'].nunique()} unique tickers", flush=True)

    baseline_rate = bear_h["success"].mean()

    print("\n" + "=" * 100)
    print("1. C1 MONOTONICITY CHECK (within Bear, horizon=20d)")
    print("   Question: does the edge increase monotonically as the selection narrows?")
    print("   (If Top20% beats Top10% beats Top5% out of order, C1's ranking is suspect.)")
    print("=" * 100)
    print(
        f"{'Selection':<12}{'Success rate':>14}{'Lift':>9}{'Median MFE':>13}"
        f"{'Median MAE':>13}{'Median R':>11}{'Setups (raw)':>14}{'Setups (dedup)':>16}"
    )
    print(
        f"{'All Bear':<12}{baseline_rate:>14.3f}{'1.000x':>9}"
        f"{bear_h['mfe'].median():>+13.3f}{bear_h['mae'].median():>+13.3f}"
        f"{bear_h['r_multiple'].median():>+11.3f}{len(bear_h):>14}{'-':>16}"
    )

    prev_rate = baseline_rate
    monotonic = True
    for q in QUANTILES:
        threshold = bear_h["c1_score"].quantile(1 - q)
        top = bear_h[bear_h["c1_score"] >= threshold]
        rate = top["success"].mean()
        lift = rate / baseline_rate if baseline_rate > 0 else float("nan")
        deduped = deduplicate_events(bear_h, "c1_score", threshold, PRIMARY_HORIZON)
        label = f"Top {int(q * 100)}%"
        print(
            f"{label:<12}{rate:>14.3f}{lift:>8.3f}x{top['mfe'].median():>+13.3f}"
            f"{top['mae'].median():>+13.3f}{top['r_multiple'].median():>+11.3f}"
            f"{len(top):>14}{len(deduped):>16}"
        )
        if rate < prev_rate - 0.005:  # small tolerance for noise
            monotonic = False
        prev_rate = rate

    print(f"\n  Monotonic (each narrower cut >= previous, within noise tolerance): {monotonic}")
    print("  (Diagnostic only - do not use this table to pick a 'better' threshold and")
    print("   call it the new frozen C1; that would be undisclosed post-hoc tuning.)")

    print("\n" + "=" * 100)
    print("2. TICKER CONCENTRATION - C1 top-20% within Bear (de-duplicated events)")
    print("=" * 100)
    threshold_20 = bear_h["c1_score"].quantile(0.80)
    deduped_20 = deduplicate_events(bear_h, "c1_score", threshold_20, PRIMARY_HORIZON)

    if len(deduped_20) == 0:
        print("  No de-duplicated events found at this threshold.")
        return

    n_unique = deduped_20["symbol"].nunique()
    setups_per_ticker = deduped_20.groupby("symbol").size().sort_values(ascending=False)
    top10_share = setups_per_ticker.head(10).sum() / len(deduped_20)

    print(f"  total de-duplicated setups: {len(deduped_20)}")
    print(f"  unique tickers contributing: {n_unique} (out of {bear_h['symbol'].nunique()} tickers that ever entered Bear)")
    print(f"  median setups per contributing ticker: {setups_per_ticker.median():.1f}")
    print(f"  max setups from a single ticker: {setups_per_ticker.max()}")
    print(f"  top-10 tickers' share of all de-duplicated setups: {top10_share:.1%}")
    print(f"\n  top 10 contributing tickers:")
    for sym, cnt in setups_per_ticker.head(10).items():
        print(f"    {sym:<8} {cnt} setup(s)")

    print("\n  Interpretation guide:")
    print("    - If top-10 share is small (e.g. <30%) and most tickers contribute 1-2")
    print("      setups: the edge looks like a general Bear-regime effect, not a few")
    print("      idiosyncratic names driving everything.")
    print("    - If top-10 share is large (e.g. >50%): the 'edge' may be concentrated")
    print("      in a handful of volatile names - worth checking sector/market-cap")
    print("      overlap before trusting this as a general Bear signal (would need an")
    print("      additional metadata fetch, not done here).")
    print("=" * 100)


if __name__ == "__main__":
    main()
