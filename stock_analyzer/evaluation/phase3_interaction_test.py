"""Phase 3: does combining independent signals (Support + Momentum) add predictive
power beyond either alone? And is there an interaction (does one act as a filter for
the other)? Per ChatGPT's "Incremental Information" and "mutual filter" suggestions.

CRITICAL: runs on TRAIN data ONLY (loaded from the already-saved phase2_retest_obs.csv,
filtered to the pre-cutoff period via validation.ic_test.time_split). The hold-out set
is NOT touched again here - it was already used to discover the volatility-regime
dependence of momentum/RSI (see phase2_deep_dive.py results), so re-using it for this
new question would be the same test-set-contamination problem all over again. Any
confirmatory claim from this script's findings must wait for the Locked Test set
(a fresh, never-touched symbol sample - see Research Protocol v1.3 addendum).

Usage:
    python -m stock_analyzer.evaluation.phase3_interaction_test
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyzer.validation.ic_test import diagnostic_segment_ic, spearman_ic, time_split

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "phase2_retest_obs.csv"

PRIMARY_HORIZON = 20


def _zscore(series: pd.Series) -> pd.Series:
    return (series - series.mean()) / series.std(ddof=0)


print(f"loading saved observations from {_OBS_PATH} ...", flush=True)
obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])
print(f"  {len(obs)} rows loaded, {obs['symbol'].nunique()} symbols", flush=True)

cutoff = time_split(obs["date"])
train = obs[obs["date"] <= cutoff].copy()
print(f"\nusing TRAIN data only (date <= {cutoff.date()}), n={len(train)}", flush=True)
print("(hold-out is NOT re-used here - already spent on regime-dependence discovery)", flush=True)

train_h = train[train["horizon"] == PRIMARY_HORIZON].copy()

print("\n" + "=" * 78)
print(f"1. INCREMENTAL INFORMATION: Support alone vs Momentum alone vs Combined")
print(f"   horizon={PRIMARY_HORIZON}d, target=r_multiple, TRAIN only")
print("=" * 78)

ic_support = spearman_ic(train_h["support_signal"], train_h["r_multiple"])
ic_momentum = spearman_ic(train_h["momentum_signal"], train_h["r_multiple"])

train_h["z_support"] = _zscore(train_h["support_signal"])
train_h["z_momentum"] = _zscore(train_h["momentum_signal"])
train_h["combined_signal"] = train_h["z_support"] + train_h["z_momentum"]
ic_combined = spearman_ic(train_h["combined_signal"], train_h["r_multiple"])

print(f"  Support alone:    IC = {ic_support:+.4f}")
print(f"  Momentum alone:   IC = {ic_momentum:+.4f}")
print(f"  Combined (equal-weight z-score sum): IC = {ic_combined:+.4f}")
best_single = max(abs(ic_support), abs(ic_momentum))
improvement = abs(ic_combined) - best_single
print(f"  Combined vs best single: {improvement:+.4f} ({'IMPROVES' if improvement > 0 else 'no improvement'})")

print("\n" + "=" * 78)
print("2. INTERACTION: does Momentum's level change Support's predictive power (and vice versa)?")
print("   DIAGNOSTIC ONLY - pre-registered question, not a confirmatory test (Protocol section 4.4)")
print("=" * 78)

print(f"\n-- Support's IC vs r_multiple, broken out by Momentum's (discrete) level --")
seg_support_by_momentum = diagnostic_segment_ic(
    train_h, signal_col="support_signal", target_col="r_multiple", segment_col="momentum_signal"
)
for s in seg_support_by_momentum:
    print(f"  momentum_signal={s.segment_value:<6} n={s.n:>6}  support_IC={s.ic:+.4f}  [{s.confidence}]")

print(f"\n-- Momentum's IC vs r_multiple, broken out by Support quartile --")
try:
    train_h["support_quartile"] = pd.qcut(train_h["support_signal"], 4, duplicates="drop")
    seg_momentum_by_support = diagnostic_segment_ic(
        train_h, signal_col="momentum_signal", target_col="r_multiple", segment_col="support_quartile"
    )
    for s in seg_momentum_by_support:
        print(f"  support_quartile={s.segment_value}  n={s.n:>6}  momentum_IC={s.ic:+.4f}  [{s.confidence}]")
except ValueError as exc:
    print(f"  could not form quartiles: {exc}")

print("\n" + "=" * 78)
print("Interpretation guide:")
print("  - If Combined IC clearly beats both singles: signals add independent value together.")
print("  - If Support's IC is much stronger in one Momentum bucket than others (or vice")
print("    versa): one may act as a FILTER for the other (per ChatGPT's suggestion) -")
print("    but this is a NEW hypothesis to pre-register and test on the Locked Test set,")
print("    not a conclusion to act on from this train-only diagnostic alone.")
print("=" * 78)