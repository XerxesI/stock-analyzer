"""MF1 decile breakdown: full-range view of RVOL vs outcomes, to resolve the
apparent contradiction between:
    - the practical profile (§12): within the TOP quantiles (30/20/10/5%), median
      MFE and MAE both rise as the cut narrows
    - the outcome diagnostic: across the FULL range, IC(rvol, mfe) and IC(rvol,
      |mae|) are both NEGATIVE

Hypothesis to check: the full-range negative IC may be driven by the LOW end of
RVOL (illiquid/thin-trading days where a single order can move price a lot,
producing noisy large excursions), while the HIGH end of RVOL shows the mild,
genuine uptick in MFE/MAE that the practical profile captured. A full-range
Spearman IC cannot distinguish "monotonic decreasing" from "U-shaped" - this
decile table can.

DIAGNOSTIC ONLY - operates on the already-saved locked_test_mf1_obs.csv, only a
small SPY/VIX fetch needed for the Bull/Bear regime filter.

Usage:
    python -m stock_analyzer.evaluation.mf1_decile_breakdown
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_analyzer.evaluation.locked_test_mf1 import _fetch_regime
from stock_analyzer.validation.regime import tag_observations

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "locked_test_mf1_obs.csv"

PRIMARY_HORIZON = 20
N_DECILES = 10


def main() -> None:
    print(f"loading Locked Test (MF1) observations from {_OBS_PATH} ...", flush=True)
    obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])

    regime_df = _fetch_regime()
    obs = tag_observations(obs, regime_df)

    obs_h = obs[obs["horizon"] == PRIMARY_HORIZON].copy()
    bull_h = obs_h[obs_h["trend"] == "Bull"].copy()
    bull_h["mae_abs"] = bull_h["mae"].abs()

    print(f"\nBull-regime observations (horizon={PRIMARY_HORIZON}d): n={len(bull_h)}", flush=True)

    bull_h["decile"] = pd.qcut(bull_h["rvol"], N_DECILES, labels=False, duplicates="drop") + 1

    print("\n" + "=" * 100)
    print(f"RVOL DECILE BREAKDOWN (D1=lowest RVOL, D{N_DECILES}=highest), horizon={PRIMARY_HORIZON}d, Bull only")
    print("=" * 100)
    print(
        f"{'Decile':<8}{'RVOL range':>18}{'n':>8}{'Mean MFE':>11}{'Mean |MAE|':>12}"
        f"{'Mean R':>9}{'Success rate':>14}"
    )

    summary = bull_h.groupby("decile").agg(
        rvol_min=("rvol", "min"),
        rvol_max=("rvol", "max"),
        n=("rvol", "size"),
        mean_mfe=("mfe", "mean"),
        mean_mae_abs=("mae_abs", "mean"),
        mean_r=("r_multiple", "mean"),
        success_rate=("success", "mean"),
    )

    for decile, row in summary.iterrows():
        rvol_range = f"{row['rvol_min']:.2f}-{row['rvol_max']:.2f}"
        print(
            f"D{int(decile):<7}{rvol_range:>18}{int(row['n']):>8}"
            f"{row['mean_mfe']:>+11.4f}{row['mean_mae_abs']:>12.4f}"
            f"{row['mean_r']:>+9.4f}{row['success_rate']:>14.3f}"
        )

    print("\n" + "-" * 100)
    print("Interpretation guide:")
    print("  - If D1 (lowest RVOL, likely thin/illiquid trading) shows unusually HIGH")
    print("    mean MFE/MAE relative to D2-D5, that's consistent with noisy, illiquid-")
    print("    day excursions dominating the full-range negative IC - not a genuine")
    print("    'low volume predicts big moves' effect.")
    print("  - If mean MFE/MAE instead decline steadily from D1 to D10: the negative")
    print("    full-range IC is a real monotonic pattern, and the mild uptick seen only")
    print("    within the top quantiles (§12) would need a separate explanation.")
    print("  - Success rate and mean R should be read alongside this - the practically")
    print("    relevant number is still where success rate / R peaks, not MFE/MAE alone.")
    print("=" * 100)


if __name__ == "__main__":
    main()