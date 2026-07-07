"""Practical usability metrics for the CONFIRMED signals (Support v1, Momentum v1
Bear-conditional, C1 combination), per ChatGPT's post-Locked-Test feedback.

This does NOT re-test or re-confirm anything - the confirmatory decisions were already
made in locked_test.py using pre-registered criteria. This script only DESCRIBES how
usable the confirmed signals are in practice:

    1. Top-quantile success rate vs baseline, with LIFT (not the meaningless
       population-wide "win_rate" printed by locked_test.py's secondary metrics -
       that number was the same for every signal because it wasn't conditioned on
       the signal's value at all).
    2. MFE / MAE distributions (percentiles, not just the mean).
    3. Median holding time (exit_day) for the top-quantile group.
    4. EVENT DEDUPLICATION: with a 5-trading-day re-scoring step and a 20-day primary
       holding horizon, consecutive observations for the same symbol overlap in time
       - they are NOT independent trade setups. This computes a de-duplicated
       "coverage" number (unique, non-overlapping candidate windows per symbol per
       year) alongside the raw observation count, so "how many trades could I
       actually take" isn't overstated by ~4x.

Runs on the already-saved locked_test_obs.csv (re-uses the Locked Test's descriptive
data - this is allowed, since no new confirmatory decision is being made here, only a
practical characterization of signals already confirmed).

Usage:
    python -m stock_analyzer.evaluation.practical_metrics
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.validation.regime import build_market_regime, tag_observations

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "locked_test_obs.csv"
_FROZEN_PARAMS_PATH = _ARTIFACTS_REPORTS / "frozen_c1_params.json"

PRIMARY_HORIZON = 20
TOP_QUANTILE = 0.20  # top 20%, per ChatGPT's illustrative example


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


def top_quantile_lift(
    df: pd.DataFrame,
    signal_col: str,
    quantile: float = TOP_QUANTILE,
) -> dict:
    """Success rate / lift / MFE-MAE / holding-time for the top quantile vs baseline."""

    sub = df.dropna(subset=[signal_col, "success", "mfe", "mae", "exit_day"])
    if len(sub) < 50:
        return {"error": "insufficient_data", "n": len(sub)}

    threshold = sub[signal_col].quantile(1 - quantile)
    top = sub[sub[signal_col] >= threshold]
    baseline_rate = sub["success"].mean()
    top_rate = top["success"].mean()

    return {
        "n_total": len(sub),
        "n_top": len(top),
        "threshold": float(threshold),
        "baseline_success_rate": float(baseline_rate),
        "top_success_rate": float(top_rate),
        "lift": float(top_rate / baseline_rate) if baseline_rate > 0 else float("nan"),
        "top_mfe_p25": float(top["mfe"].quantile(0.25)),
        "top_mfe_p50": float(top["mfe"].quantile(0.50)),
        "top_mfe_p75": float(top["mfe"].quantile(0.75)),
        "top_mae_p25": float(top["mae"].quantile(0.25)),
        "top_mae_p50": float(top["mae"].quantile(0.50)),
        "top_mae_p75": float(top["mae"].quantile(0.75)),
        "top_median_holding_days": float(top["exit_day"].median()),
        "baseline_mfe_p50": float(sub["mfe"].quantile(0.50)),
        "baseline_mae_p50": float(sub["mae"].quantile(0.50)),
    }


def deduplicate_events(
    df: pd.DataFrame,
    signal_col: str,
    threshold: float,
    horizon: int,
    date_col: str = "date",
    symbol_col: str = "symbol",
) -> pd.DataFrame:
    """Greedily select non-overlapping candidate windows per symbol.

    A row qualifies as a "candidate" if signal_col >= threshold. Among a symbol's
    qualifying rows (sorted by date), a new event is only counted if its date is at
    least `horizon` trading days after the previously SELECTED event's date - i.e.
    its holding window doesn't overlap the previous one. This turns "how many rows
    cross the threshold" into "how many genuinely separate trade opportunities".

    Note: this is an approximation (uses calendar-day spacing as a proxy for trading
    days, and treats a full `horizon`-day gap as required even though real trades
    might exit earlier) - good enough for a coverage ESTIMATE, not exact bookkeeping.
    """

    qualifying = df[df[signal_col] >= threshold].sort_values(date_col)
    kept_rows = []
    last_date_by_symbol: dict[str, pd.Timestamp] = {}

    for _, row in qualifying.iterrows():
        symbol = row[symbol_col]
        date = row[date_col]
        last_date = last_date_by_symbol.get(symbol)
        if last_date is None or (date - last_date).days >= horizon * 1.4:  # ~1.4x calendar/trading-day ratio
            kept_rows.append(row)
            last_date_by_symbol[symbol] = date

    return pd.DataFrame(kept_rows)


def main() -> None:
    print(f"loading Locked Test observations from {_OBS_PATH} ...", flush=True)
    obs = pd.read_csv(_OBS_PATH, parse_dates=["date"])
    print(f"  {len(obs)} rows loaded, {obs['symbol'].nunique()} symbols", flush=True)

    with open(_FROZEN_PARAMS_PATH) as f:
        frozen = json.load(f)

    regime_df = _fetch_regime()
    obs = tag_observations(obs, regime_df)

    obs["z_support"] = (obs["support_signal"] - frozen["mu_support"]) / frozen["sigma_support"]
    obs["z_momentum"] = (obs["momentum_signal"] - frozen["mu_momentum"]) / frozen["sigma_momentum"]
    obs["is_bear"] = obs["regime"].isin(frozen["bear_regimes"]).astype(float)
    obs["c1_score"] = obs["z_support"] + obs["z_momentum"] * obs["is_bear"]

    obs_h = obs[obs["horizon"] == PRIMARY_HORIZON].copy()
    bear_h = obs_h[obs_h["trend"] == "Bear"].copy()

    print("\n" + "=" * 78)
    print(f"TOP-{int(TOP_QUANTILE*100)}% SUCCESS RATE / LIFT (horizon={PRIMARY_HORIZON}d)")
    print("=" * 78)

    print("\n-- Support v1 (all regimes) --")
    r = top_quantile_lift(obs_h, "support_signal")
    if "error" not in r:
        print(f"  baseline success rate: {r['baseline_success_rate']:.3f}  (n={r['n_total']})")
        print(f"  top-{int(TOP_QUANTILE*100)}% success rate:  {r['top_success_rate']:.3f}  (n={r['n_top']})")
        print(f"  LIFT: {r['lift']:.3f}x")
        print(f"  top group MFE: p25={r['top_mfe_p25']:+.3f} p50={r['top_mfe_p50']:+.3f} p75={r['top_mfe_p75']:+.3f}")
        print(f"  top group MAE: p25={r['top_mae_p25']:+.3f} p50={r['top_mae_p50']:+.3f} p75={r['top_mae_p75']:+.3f}")
        print(f"  top group median holding days: {r['top_median_holding_days']:.1f}")

    print("\n-- Momentum v1 (BEAR REGIME ONLY - per M1's confirmed conditional nature) --")
    r = top_quantile_lift(bear_h, "momentum_signal")
    if "error" not in r:
        print(f"  baseline success rate (within Bear): {r['baseline_success_rate']:.3f}  (n={r['n_total']})")
        print(f"  top-{int(TOP_QUANTILE*100)}% success rate:  {r['top_success_rate']:.3f}  (n={r['n_top']})")
        print(f"  LIFT: {r['lift']:.3f}x")
        print(f"  top group MFE: p25={r['top_mfe_p25']:+.3f} p50={r['top_mfe_p50']:+.3f} p75={r['top_mfe_p75']:+.3f}")
        print(f"  top group MAE: p25={r['top_mae_p25']:+.3f} p50={r['top_mae_p50']:+.3f} p75={r['top_mae_p75']:+.3f}")
        print(f"  top group median holding days: {r['top_median_holding_days']:.1f}")
    else:
        print(f"  insufficient data (n={r['n']})")

    print("\n-- C1 combination (all regimes) --")
    r_c1 = top_quantile_lift(obs_h, "c1_score")
    if "error" not in r_c1:
        print(f"  baseline success rate: {r_c1['baseline_success_rate']:.3f}  (n={r_c1['n_total']})")
        print(f"  top-{int(TOP_QUANTILE*100)}% success rate:  {r_c1['top_success_rate']:.3f}  (n={r_c1['n_top']})")
        print(f"  LIFT: {r_c1['lift']:.3f}x")
        print(f"  top group MFE: p25={r_c1['top_mfe_p25']:+.3f} p50={r_c1['top_mfe_p50']:+.3f} p75={r_c1['top_mfe_p75']:+.3f}")
        print(f"  top group MAE: p25={r_c1['top_mae_p25']:+.3f} p50={r_c1['top_mae_p50']:+.3f} p75={r_c1['top_mae_p75']:+.3f}")
        print(f"  top group median holding days: {r_c1['top_median_holding_days']:.1f}")

    print("\n" + "=" * 78)
    print("EVENT DEDUPLICATION - raw observations vs genuinely separate trade setups")
    print(f"(non-overlapping: next candidate for a symbol must be >= {PRIMARY_HORIZON} trading")
    print(" days after the previous one, approximated via ~1.4x calendar-day spacing)")
    print("=" * 78)

    if "error" not in r_c1:
        threshold_c1 = r_c1["threshold"]
        raw_candidates = obs_h[obs_h["c1_score"] >= threshold_c1]
        deduped = deduplicate_events(obs_h, "c1_score", threshold_c1, PRIMARY_HORIZON)
        n_symbols = obs_h["symbol"].nunique()
        years_covered = (obs_h["date"].max() - obs_h["date"].min()).days / 365.25

        print(f"\n  C1 top-{int(TOP_QUANTILE*100)}% candidates:")
        print(f"    raw observations crossing threshold: {len(raw_candidates)}")
        print(f"    de-duplicated (non-overlapping) events: {len(deduped)}")
        print(f"    inflation factor (raw/deduped): {len(raw_candidates) / max(len(deduped), 1):.2f}x")
        print(f"    de-duplicated events per year (whole universe, n_symbols={n_symbols}): "
              f"{len(deduped) / years_covered:.1f}")
        print(f"    de-duplicated events per symbol per year: {len(deduped) / years_covered / n_symbols:.3f}")

    print("\n" + "=" * 78)
    print("REGIME-CONDITIONAL CHECK: does C1 beat Support specifically WITHIN Bear?")
    print("(The global top-20% cut above is dominated by the Bull majority - this is")
    print(" the fairer test of C1's own design premise: it should differentiate MOST")
    print(" within the regime where the Momentum boost actually applies.)")
    print("=" * 78)

    if len(bear_h) >= 50:
        print("\n-- Support v1, top-20% WITHIN Bear only --")
        r_support_bear = top_quantile_lift(bear_h, "support_signal")
        if "error" not in r_support_bear:
            print(f"  baseline (Bear): {r_support_bear['baseline_success_rate']:.3f}  (n={r_support_bear['n_total']})")
            print(f"  top-20% (Bear):  {r_support_bear['top_success_rate']:.3f}  (n={r_support_bear['n_top']})")
            print(f"  LIFT: {r_support_bear['lift']:.3f}x")

        print("\n-- C1 combination, top-20% WITHIN Bear only --")
        r_c1_bear = top_quantile_lift(bear_h, "c1_score")
        if "error" not in r_c1_bear:
            print(f"  baseline (Bear): {r_c1_bear['baseline_success_rate']:.3f}  (n={r_c1_bear['n_total']})")
            print(f"  top-20% (Bear):  {r_c1_bear['top_success_rate']:.3f}  (n={r_c1_bear['n_top']})")
            print(f"  LIFT: {r_c1_bear['lift']:.3f}x")

        if "error" not in r_support_bear and "error" not in r_c1_bear:
            delta = r_c1_bear["lift"] - r_support_bear["lift"]
            print(f"\n  C1 lift - Support lift (within Bear): {delta:+.3f}")
            print(f"  {'C1 meaningfully beats Support within Bear' if delta > 0.03 else 'No meaningful difference - C1 combination adds little practical value here'}")
    else:
        print(f"  Not enough Bear observations at this horizon (n={len(bear_h)}) for a reliable quantile cut.")

    print("\n" + "=" * 78)
    print("Reminder: this describes the ALREADY-CONFIRMED signals' practical usability.")
    print("It does not re-open the confirmatory decisions made in locked_test.py.")
    print("=" * 78)


if __name__ == "__main__":
    main()