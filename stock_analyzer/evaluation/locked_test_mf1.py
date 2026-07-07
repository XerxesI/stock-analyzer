"""LOCKED TEST: MF1 (RVOL, Bull-regime) - one-time confirmatory test on the same
Locked Test symbol sample (seed=123) used for M1/S1/C1. Legitimate re-use: RVOL has
never been examined on this specific sample before - only M1/S1/C1-specific
quantities (momentum_signal, support_signal) were computed on it previously.

PRE-REGISTERED HYPOTHESES (frozen before running against Locked Test data):

    MF1a - PRIMARY confirmatory condition
        RVOL has positive predictive power in Bull regime.
        Expected result: IC_bull > 0

    MF1b - SECONDARY regime-difference condition (informative, not required for
        "MF1 confirmed" - per ChatGPT: the project's real goal is finding Bull-regime
        upside candidates, not proving RVOL is bad in Bear)
        RVOL's predictive power is higher in Bull than in Bear.
        Expected result: IC_bull > IC_bear

    Regime: Bull = SPY Close >= SPY SMA200, Bear = SPY Close < SPY SMA200 (same
      strict 2-way split as M1)
    Signal: rvol (Volume / 20-day rolling average Volume), unchanged
    Primary horizon: 20 trading days
    Primary target: triple-barrier R-multiple
    Primary test: Spearman IC

No generic "RVOL replication" (S1-style) hypothesis is tested here - per ChatGPT,
a broader unconditional RVOL hypothesis would unnecessarily widen the test; MF1a/b
already ask the practically relevant question directly.

PROVENANCE CAVEAT: same as the original Locked Test - cross-sectional independence
only (new-to-this-hypothesis tickers, but same calendar period as all prior work).

RULE: results are evaluated ONLY against the pre-stated criteria above. Do not
change horizons, metrics, or thresholds after seeing output.

Usage:
    python -m stock_analyzer.evaluation.locked_test_mf1
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.signals.money_flow import calculate_money_flow_features
from stock_analyzer.validation.ic_test import spearman_ic
from stock_analyzer.validation.labeling import LabelingConfig, label_at
from stock_analyzer.validation.regime import build_market_regime, tag_observations

LOCKED_SAMPLE_SIZE = 300
LOCKED_SEED = 123  # SAME Locked Test sample as M1/S1/C1 - legitimate first use for RVOL

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210
STEP_DAYS = 5

RVOL_WINDOW = 20
LABELING_CONFIG = LabelingConfig(horizons=(10, 20, 40))
PRIMARY_HORIZON = 20  # FROZEN - do not change after seeing results

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "locked_test_mf1_obs.csv"


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
    print("fetching SPY (+ attempting ^VIX) for market regime...", flush=True)
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


def main() -> None:
    regime_df = _fetch_regime()

    print(f"\nsampling {LOCKED_SAMPLE_SIZE} LOCKED symbols (seed={LOCKED_SEED}, "
          f"same sample as M1/S1/C1, first use for RVOL)...", flush=True)
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
    print(f"\ncomputing RVOL + labels (step={STEP_DAYS} bars)...", flush=True)
    for i, (symbol, frame) in enumerate(frames.items()):
        mf_df = calculate_money_flow_features(frame, rvol_window=RVOL_WINDOW)
        max_t = len(frame) - max(LABELING_CONFIG.horizons) - 1
        for t_pos in range(TEST_START_POS, max_t, STEP_DAYS):
            date = frame.index[t_pos]
            rvol_val = mf_df["rvol"].iloc[t_pos]
            if pd.isna(rvol_val):
                continue
            for horizon in LABELING_CONFIG.horizons:
                label = label_at(frame, t_pos, horizon, LABELING_CONFIG)
                if label is None:
                    continue
                rows.append({"symbol": symbol, "date": date, "horizon": horizon, **label, "rvol": rvol_val})
        if (i + 1) % 30 == 0:
            print(f"  processed {i + 1}/{len(frames)} symbols", flush=True)

    _ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
    obs = pd.DataFrame(rows)
    obs.to_csv(_OBS_PATH, index=False)
    print(f"\ntotal LOCKED observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

    obs = tag_observations(obs, regime_df)
    obs_h = obs[obs["horizon"] == PRIMARY_HORIZON].copy()

    print("\n" + "=" * 78)
    print(f"LOCKED TEST RESULTS - MF1, primary horizon={PRIMARY_HORIZON}d, target=r_multiple")
    print("=" * 78)

    bull_mask = obs_h["trend"] == "Bull"
    bear_mask = obs_h["trend"] == "Bear"
    ic_bull = spearman_ic(obs_h.loc[bull_mask, "rvol"], obs_h.loc[bull_mask, "r_multiple"])
    ic_bear = spearman_ic(obs_h.loc[bear_mask, "rvol"], obs_h.loc[bear_mask, "r_multiple"])
    n_bull, n_bear = bull_mask.sum(), bear_mask.sum()

    print(f"\n  IC_bull = {ic_bull:+.4f} (n={n_bull})")
    print(f"  IC_bear = {ic_bear:+.4f} (n={n_bear})")

    print("\n--- MF1a (PRIMARY): RVOL has positive predictive power in Bull regime ---")
    mf1a_pass = ic_bull > 0
    print(f"  Expected: IC_bull > 0")
    print(f"  MF1a RESULT: {'CONFIRMED' if mf1a_pass else 'NOT CONFIRMED'}")

    print("\n--- MF1b (SECONDARY, informative): IC_bull > IC_bear ---")
    mf1b_pass = ic_bull > ic_bear
    print(f"  Expected: IC_bull > IC_bear")
    print(f"  MF1b RESULT: {'CONFIRMED' if mf1b_pass else 'NOT CONFIRMED'}")
    print("  (MF1b is NOT required for MF1 to be considered confirmed - see docstring)")

    print("\n" + "-" * 78)
    print("SECONDARY METRICS (context only)")
    print("-" * 78)
    for h in sorted(obs["horizon"].unique()):
        sub = obs[obs["horizon"] == h]
        sub_bull = sub[sub["trend"] == "Bull"] if "trend" in sub.columns else sub
        ic_h_bull = spearman_ic(sub_bull["rvol"], sub_bull["r_multiple"])
        print(f"  horizon={h:>3}d  IC_bull={ic_h_bull:+.4f}")

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  MF1a (RVOL positive in Bull) - PRIMARY:   {'CONFIRMED' if mf1a_pass else 'NOT CONFIRMED'}")
    print(f"  MF1b (Bull IC > Bear IC) - secondary:     {'CONFIRMED' if mf1b_pass else 'NOT CONFIRMED'}")
    if mf1a_pass:
        print("\n  MF1 is considered CONFIRMED for the project's practical purpose")
        print("  (finding Bull-regime upside candidates), regardless of MF1b's outcome.")
    else:
        print("\n  MF1 is NOT confirmed - the primary condition (IC_bull > 0) failed.")
    print("\n  Reminder: cross-sectional independence only (same calendar period as")
    print("  all prior experiments) - not temporal independence.")
    print("=" * 78)


if __name__ == "__main__":
    main()