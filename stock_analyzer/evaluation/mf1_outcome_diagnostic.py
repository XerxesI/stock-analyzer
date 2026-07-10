"""MF1 outcome diagnostic: does RVOL predict DIRECTION (success/r_multiple) or
AMPLITUDE (how big the move is, in either direction)? Per ChatGPT's sharp catch:
in the practical profile (§12), both median MFE and median MAE INCREASE as RVOL's
selection narrows, while success rate DECREASES - a pattern consistent with RVOL
being an "activity/attention/expansion" feature rather than a directional one.

DIAGNOSTIC ONLY - does not re-open the MF1a/MF1b confirmatory decision, does not
tune the RVOL window/threshold. Operates entirely on the already-saved
locked_test_mf1_obs.csv (which already contains mfe, mae, atr_at_entry, entry_price
from label_at's output - no need to re-fetch price data, only a small SPY/VIX
fetch for the Bull/Bear regime filter).

Two checks:
    1. IC(rvol, mfe), IC(rvol, |mae|), IC(rvol, excursion_magnitude=mfe+|mae|)
       - if RVOL correlates positively with BOTH mfe and |mae|, it's predicting
       amplitude, not direction.
    2. RVOL x ATR% bucket: does RVOL's IC vs r_multiple hold up across low/medium/
       high ATR% terciles, or is it just a proxy for "this stock is already volatile"?

Usage:
    python -m stock_analyzer.evaluation.mf1_outcome_diagnostic
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_analyzer.evaluation.locked_test_mf1 import _fetch_regime
from stock_analyzer.validation.ic_test import spearman_ic
from stock_analyzer.validation.regime import tag_observations

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "locked_test_mf1_obs.csv"

PRIMARY_HORIZON = 20


def main() -> None:
    print(f"loading Locked Test (MF1) observations from {_OBS_PATH} ...", flush=True)
    obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])

    regime_df = _fetch_regime()
    obs = tag_observations(obs, regime_df)

    obs_h = obs[obs["horizon"] == PRIMARY_HORIZON].copy()
    bull_h = obs_h[obs_h["trend"] == "Bull"].copy()
    bull_h["atr_pct"] = bull_h["atr_at_entry"] / bull_h["entry_price"]
    bull_h["mae_abs"] = bull_h["mae"].abs()
    bull_h["excursion_magnitude"] = bull_h["mfe"] + bull_h["mae_abs"]

    print(f"\nBull-regime observations (horizon={PRIMARY_HORIZON}d): n={len(bull_h)}", flush=True)

    print("\n" + "=" * 90)
    print("1. DIRECTION vs AMPLITUDE: what does RVOL actually correlate with?")
    print("=" * 90)
    ic_mfe = spearman_ic(bull_h["rvol"], bull_h["mfe"])
    ic_mae_abs = spearman_ic(bull_h["rvol"], bull_h["mae_abs"])
    ic_excursion = spearman_ic(bull_h["rvol"], bull_h["excursion_magnitude"])
    ic_r_multiple = spearman_ic(bull_h["rvol"], bull_h["r_multiple"])
    ic_success = spearman_ic(bull_h["rvol"], bull_h["success"].astype(float))

    print(f"  IC(rvol, mfe)                 = {ic_mfe:+.4f}   (higher RVOL -> bigger upside excursion?)")
    print(f"  IC(rvol, |mae|)                = {ic_mae_abs:+.4f}   (higher RVOL -> bigger downside excursion?)")
    print(f"  IC(rvol, mfe + |mae|)          = {ic_excursion:+.4f}   (higher RVOL -> bigger total movement?)")
    print(f"  IC(rvol, r_multiple)           = {ic_r_multiple:+.4f}   (reference - the MF1 primary result)")
    print(f"  IC(rvol, success 0/1)          = {ic_success:+.4f}   (reference - directional outcome only)")

    print("\n  Interpretation:")
    if ic_mfe > 0.02 and ic_mae_abs > 0.02:
        print("    Both MFE and |MAE| rise with RVOL -> consistent with RVOL predicting")
        print("    AMPLITUDE (how much the stock moves), not direction. RVOL's positive")
        print("    IC on r_multiple may come from the take-profit barrier being reached")
        print("    faster/further when it succeeds, not from a higher WIN probability.")
    else:
        print("    Pattern is less clear-cut than the practical-profile table suggested -")
        print("    review the numbers above directly rather than trusting this auto-note.")

    print("\n" + "=" * 90)
    print("2. RVOL x ATR% interaction: is RVOL just a volatility proxy?")
    print("=" * 90)
    try:
        bull_h["atr_bucket"] = pd.qcut(bull_h["atr_pct"], 3, labels=["Low", "Medium", "High"])
    except ValueError as exc:
        print(f"  could not form ATR% terciles: {exc}")
        return

    for bucket in ["Low", "Medium", "High"]:
        sub = bull_h[bull_h["atr_bucket"] == bucket]
        if len(sub) < 50:
            print(f"  ATR%={bucket:<8} insufficient data (n={len(sub)})")
            continue
        ic_within = spearman_ic(sub["rvol"], sub["r_multiple"])
        baseline_rate = sub["success"].mean()
        threshold = sub["rvol"].quantile(0.80)
        top = sub[sub["rvol"] >= threshold]
        top_rate = top["success"].mean()
        lift = top_rate / baseline_rate if baseline_rate > 0 else float("nan")
        print(
            f"  ATR%={bucket:<8} n={len(sub):>6}  IC(rvol,r_multiple)={ic_within:+.4f}  "
            f"baseline_success={baseline_rate:.3f}  top20%_success={top_rate:.3f}  lift={lift:.3f}x"
        )

    print("\n  Interpretation:")
    print("    If IC and lift are similar across all three ATR% buckets: RVOL carries")
    print("    information BEYOND raw volatility level (a genuinely separate signal).")
    print("    If IC/lift is much stronger only in the High-ATR bucket: RVOL's apparent")
    print("    effect may be substantially explained by volatility level itself.")
    print("=" * 90)


if __name__ == "__main__":
    main()