"""Phase 2 deep-dive: resolves the "why is holdout IC bigger than train IC?" anomaly
from phase2_retest.py, per ChatGPT's three-part suggestion:

    1. Train vs hold-out REGIME DISTRIBUTION - is Bear/High-vol over-represented in
       the hold-out window purely by calendar accident?
    2. BLOCK BOOTSTRAP confidence intervals on IC - resampled at the SYMBOL level
       (not row level), since rows within a symbol are autocorrelated (overlapping
       windows) - a naive row-level bootstrap would understate uncertainty.
    3. ROLLING WINDOW IC (6-month bins) - is a signal's predictive power stable over
       time, or concentrated in one episode?

Also adds (cheap, same data already loaded):
    4. Signal correlation matrix (deduped to one row per symbol/date, since each
       signal value is currently repeated once per horizon in the observations table)
    5. Information decay table (IC by horizon, compact side-by-side view)

Runs entirely on the ALREADY-SAVED phase2_retest_obs.csv - no need to re-fetch the
300 symbol price histories. Only SPY/VIX are re-fetched (small, fast) for regime
tagging, since the regime columns were not saved in the original CSV.

Usage:
    python -m stock_analyzer.evaluation.phase2_deep_dive
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.validation.ic_test import spearman_ic, time_split
from stock_analyzer.validation.regime import build_market_regime, tag_observations

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "phase2_retest_obs.csv"

SIGNAL_COLS = ["trend_signal", "momentum_signal", "rsi_signal", "support_signal"]
PRIMARY_HORIZON = 20  # "classic swing" horizon, per Protocol section 2.4

N_BOOTSTRAP = 1000
BOOTSTRAP_SEED = 42


def _fetch_regime_for_range(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    print("re-fetching SPY (+ attempting ^VIX) for regime tagging...", flush=True)
    spy_raw = yf.download(
        "SPY", start=start.to_pydatetime(), end=(end + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20,
    )
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    spy_raw.index = pd.to_datetime(spy_raw.index).tz_localize(None)
    spy_enriched = calculate_indicators(spy_raw.sort_index())

    vix_close = None
    try:
        vix_raw = yf.download(
            "^VIX", start=start.to_pydatetime(), end=(end + pd.Timedelta(days=1)).to_pydatetime(),
            interval="1d", auto_adjust=True, progress=False, threads=False, timeout=20,
        )
        if isinstance(vix_raw.columns, pd.MultiIndex):
            vix_raw.columns = vix_raw.columns.get_level_values(0)
        if not vix_raw.empty:
            vix_raw.index = pd.to_datetime(vix_raw.index).tz_localize(None)
            vix_close = vix_raw["Close"]
    except Exception:  # noqa: BLE001
        vix_close = None

    regime_df = build_market_regime(spy_enriched, vix_close=vix_close)
    print(f"  volatility source used: {regime_df['volatility_source'].iloc[-1]}", flush=True)
    return regime_df


def block_bootstrap_ic(
    df: pd.DataFrame,
    signal_col: str,
    target_col: str,
    group_col: str = "symbol",
    n_iterations: int = N_BOOTSTRAP,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Bootstrap CI for the IC, resampling whole SYMBOLS (not rows) with replacement.

    This respects the autocorrelation between overlapping observation windows within
    the same symbol - a row-level bootstrap would treat those as independent and
    understate the true uncertainty.
    """

    point_estimate = spearman_ic(df[signal_col], df[target_col])

    groups = {s: g[[signal_col, target_col]].dropna().to_numpy() for s, g in df.groupby(group_col)}
    groups = {s: arr for s, arr in groups.items() if len(arr) > 0}
    symbols = list(groups.keys())
    if len(symbols) < 10:
        return {"point_estimate": point_estimate, "ci_low": float("nan"), "ci_high": float("nan"), "n_iterations": 0}

    rng = np.random.default_rng(seed)
    ics = []
    for _ in range(n_iterations):
        sampled_symbols = rng.choice(symbols, size=len(symbols), replace=True)
        stacked = np.concatenate([groups[s] for s in sampled_symbols], axis=0)
        sig = pd.Series(stacked[:, 0])
        tgt = pd.Series(stacked[:, 1])
        ic = sig.rank().corr(tgt.rank())
        if not np.isnan(ic):
            ics.append(ic)

    ics_arr = np.array(ics)
    return {
        "point_estimate": point_estimate,
        "bootstrap_mean": float(ics_arr.mean()) if len(ics_arr) else float("nan"),
        "ci_low": float(np.percentile(ics_arr, 2.5)) if len(ics_arr) else float("nan"),
        "ci_high": float(np.percentile(ics_arr, 97.5)) if len(ics_arr) else float("nan"),
        "n_iterations": len(ics_arr),
    }


def rolling_window_ic(
    df: pd.DataFrame,
    signal_col: str,
    target_col: str,
    date_col: str = "date",
) -> pd.DataFrame:
    """IC computed separately per 6-month calendar bin, to check stability over time."""

    rows = []
    grouped = df.set_index(date_col).groupby(pd.Grouper(freq="6ME"))
    for period_end, group in grouped:
        if group.empty:
            continue
        valid = group[[signal_col, target_col]].dropna()
        ic = spearman_ic(group[signal_col], group[target_col])
        rows.append({"period_end": period_end.date(), "ic": ic, "n": len(valid)})
    return pd.DataFrame(rows)


print(f"loading saved observations from {_OBS_PATH} ...", flush=True)
obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])
print(f"  {len(obs)} rows loaded, {obs['symbol'].nunique()} symbols", flush=True)

regime_df = _fetch_regime_for_range(obs["date"].min(), obs["date"].max())
obs = tag_observations(obs, regime_df)

cutoff = time_split(obs["date"])
train = obs[obs["date"] <= cutoff]
holdout = obs[obs["date"] > cutoff]
print(f"\ntrain/holdout cutoff: {cutoff.date()}  (train n={len(train)}, holdout n={len(holdout)})", flush=True)

print("\n" + "=" * 78)
print("1. REGIME DISTRIBUTION: train vs hold-out (row-share, %)")
print("   (checks whether hold-out over-represents Bear/High-vol by calendar accident)")
print("=" * 78)
train_regime_pct = train["regime"].value_counts(normalize=True).mul(100).round(1)
holdout_regime_pct = holdout["regime"].value_counts(normalize=True).mul(100).round(1)
regime_compare = pd.DataFrame({"train_%": train_regime_pct, "holdout_%": holdout_regime_pct}).fillna(0.0)
print(regime_compare)

print("\n" + "=" * 78)
print(f"2. BLOCK BOOTSTRAP CI (symbol-level resampling, n={N_BOOTSTRAP}), horizon={PRIMARY_HORIZON}d")
print("   Train vs hold-out, IC vs r_multiple")
print("=" * 78)
for signal_col in SIGNAL_COLS:
    train_h = train[train["horizon"] == PRIMARY_HORIZON]
    holdout_h = holdout[holdout["horizon"] == PRIMARY_HORIZON]
    train_boot = block_bootstrap_ic(train_h, signal_col, "r_multiple")
    holdout_boot = block_bootstrap_ic(holdout_h, signal_col, "r_multiple")
    print(f"\n  {signal_col}:")
    print(
        f"    train:   IC={train_boot['point_estimate']:+.4f}  "
        f"95% CI=[{train_boot['ci_low']:+.4f}, {train_boot['ci_high']:+.4f}]"
    )
    print(
        f"    holdout: IC={holdout_boot['point_estimate']:+.4f}  "
        f"95% CI=[{holdout_boot['ci_low']:+.4f}, {holdout_boot['ci_high']:+.4f}]"
    )
    train_excludes_zero = train_boot["ci_low"] > 0 or train_boot["ci_high"] < 0
    holdout_excludes_zero = holdout_boot["ci_low"] > 0 or holdout_boot["ci_high"] < 0
    print(
        f"    CI excludes zero: train={train_excludes_zero}  holdout={holdout_excludes_zero}"
    )

print("\n" + "=" * 78)
print(f"3. ROLLING WINDOW IC (6-month bins), horizon={PRIMARY_HORIZON}d, IC vs r_multiple")
print("   Checks whether a signal's IC is stable over time or concentrated in one episode")
print("=" * 78)
for signal_col in SIGNAL_COLS:
    sub = obs[obs["horizon"] == PRIMARY_HORIZON]
    rw = rolling_window_ic(sub, signal_col, "r_multiple")
    print(f"\n  {signal_col}:")
    for _, row in rw.iterrows():
        print(f"    {row['period_end']}: IC={row['ic']:+.4f} (n={row['n']})")

print("\n" + "=" * 78)
print("4. SIGNAL CORRELATION MATRIX (Spearman, deduped to 1 row per symbol/date)")
print("   Run BEFORE adding new signals - Protocol Phase 3 (feature independence)")
print("=" * 78)
deduped = obs.drop_duplicates(subset=["symbol", "date"])[SIGNAL_COLS]
corr = deduped.corr(method="spearman")
print(corr.round(3))

print("\n" + "=" * 78)
print(f"5. INFORMATION DECAY: IC vs r_multiple by horizon, train vs holdout (compact view)")
print("=" * 78)
header = f"{'signal':<18}" + "".join(f"{'h='+str(h)+'d (train)':>16}{'h='+str(h)+'d (hold)':>16}" for h in sorted(obs['horizon'].unique()))
print(header)
for signal_col in SIGNAL_COLS:
    cells = f"{signal_col:<18}"
    for h in sorted(obs["horizon"].unique()):
        train_h = train[train["horizon"] == h]
        holdout_h = holdout[holdout["horizon"] == h]
        t_ic = spearman_ic(train_h[signal_col], train_h["r_multiple"])
        h_ic = spearman_ic(holdout_h[signal_col], holdout_h["r_multiple"])
        cells += f"{t_ic:>+16.4f}{h_ic:>+16.4f}"
    print(cells)
print("=" * 78)