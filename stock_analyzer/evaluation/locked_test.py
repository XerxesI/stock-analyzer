"""LOCKED TEST: one-time confirmatory test of three pre-registered hypotheses on a
fresh, never-before-used symbol sample.

PRE-REGISTERED HYPOTHESES (frozen before this script was run against Locked Test data):

    M1 - Conditional Momentum
        Momentum v1 has positive predictive power in Bear regime, and its IC there
        is higher than in Bull regime.
        Regime: Bear = SPY Close < SPY SMA200, Bull = SPY Close >= SPY SMA200
          (2-way split only, per ChatGPT's simplified pre-registration - the
          volatility dimension is NOT used to define Bear/Bull for this test)
        Signal: momentum_signal (Trade Score v2 component, unchanged)
        Primary horizon: 20 trading days
        Primary target: triple-barrier R-multiple
        Primary test: Spearman IC
        Expected result: IC_bear > 0 AND IC_bear > IC_bull

    S1 - Support Replication
        Support v1 has small, positive predictive power regardless of market regime.
        Signal: support_signal
        Primary horizon: 20 trading days
        Primary target: R-multiple
        Expected result: IC > 0 overall, and not sign-flipping across every regime

    C1 - Incremental Information (frozen combination formula)
        The FROZEN Support + Bear-Conditional-Momentum combination
        (see freeze_c1_params.py / frozen_c1_params.json) has higher IC than
        Support alone.
        Expected result: IC(C1_score) > IC(support_signal alone)

PROVENANCE / INDEPENDENCE CAVEAT (documented per ChatGPT's insistence - read before
trusting any "confirmed" result): this Locked Test uses a NEW, never-touched sample of
300 symbols (seed=123) but covers the SAME calendar period as all prior experiments,
because no genuinely future data exists yet. This gives CROSS-SECTIONAL independence
only (different companies), NOT temporal independence (same macro regime, same rate
environment, same sector rotations as everything tested before). A "confirmed" result
here is evidence the effect isn't specific to the original 300-symbol sample - it is
NOT yet evidence the effect will hold in a different market era.

RULE: results are evaluated ONLY against the pre-stated primary criteria above. Do not
change horizons, metrics, or success thresholds after seeing output. Secondary/
diagnostic numbers are reported for context, not for post-hoc criterion selection.

Usage:
    python -m stock_analyzer.evaluation.freeze_c1_params   # run ONCE, first
    python -m stock_analyzer.evaluation.locked_test
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.swing.trade_score import calculate_trade_score
from stock_analyzer.validation.ic_test import spearman_ic
from stock_analyzer.validation.labeling import LabelingConfig, label_at
from stock_analyzer.validation.regime import build_market_regime, tag_observations

LOCKED_SAMPLE_SIZE = 300
LOCKED_SEED = 123  # DIFFERENT from the seed=42 dev sample - never touched before

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210
STEP_DAYS = 5

LABELING_CONFIG = LabelingConfig(horizons=(5, 10, 20, 40))
MIN_HISTORY_BARS = 210
PRIMARY_HORIZON = 20  # FROZEN - do not change after seeing results

BEAR_REGIMES = {"Bear_High", "Bear_Normal"}  # for the SIMPLE Bear/Bull split (M1),
# we additionally collapse to a strict 2-way SPY-vs-SMA200 split below, per the
# pre-registration ("Bear = SPY Close < SPY SMA200"), independent of volatility.

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "locked_test_obs.csv"
_FROZEN_PARAMS_PATH = _ARTIFACTS_REPORTS / "frozen_c1_params.json"


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

    regime_df = build_market_regime(spy_enriched, vix_close=vix_close)
    # Strict 2-way Bear/Bull split for M1, independent of the volatility dimension.
    regime_df["bear_bull_v1"] = regime_df["trend"]  # "Bull" | "Bear" | NaN, already computed
    return regime_df


def main() -> None:
    if not _FROZEN_PARAMS_PATH.exists():
        raise FileNotFoundError(
            f"{_FROZEN_PARAMS_PATH} not found. Run "
            "`python -m stock_analyzer.evaluation.freeze_c1_params` FIRST, on the dev "
            "data, before running this Locked Test."
        )
    with open(_FROZEN_PARAMS_PATH) as f:
        frozen = json.load(f)
    print("loaded FROZEN C1 parameters (fit on dev data, not recomputed here):", flush=True)
    for k, v in frozen.items():
        print(f"  {k}: {v}")

    regime_df = _fetch_regime()

    print(f"\nsampling {LOCKED_SAMPLE_SIZE} LOCKED symbols (seed={LOCKED_SEED}, never used before)...", flush=True)
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
    print(f"\nscoring + labeling (step={STEP_DAYS} bars)...", flush=True)
    for i, (symbol, frame) in enumerate(frames.items()):
        max_t = len(frame) - max(LABELING_CONFIG.horizons) - 1
        for t_pos in range(TEST_START_POS, max_t, STEP_DAYS):
            hist = frame.iloc[: t_pos + 1]
            try:
                score_result = calculate_trade_score(hist)
            except (ValueError, RuntimeError):
                continue
            signals = {
                "momentum_signal": score_result["components"]["momentum"]["points"],
                "support_signal": score_result["components"]["support"]["points"],
            }
            date = frame.index[t_pos]
            for horizon in LABELING_CONFIG.horizons:
                label = label_at(frame, t_pos, horizon, LABELING_CONFIG)
                if label is None:
                    continue
                rows.append({"symbol": symbol, "date": date, "horizon": horizon, **label, **signals})
        if (i + 1) % 30 == 0:
            print(f"  processed {i + 1}/{len(frames)} symbols", flush=True)

    _ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
    obs = pd.DataFrame(rows)
    obs.to_csv(_OBS_PATH, index=False)
    print(f"\ntotal LOCKED observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

    obs = tag_observations(obs, regime_df)

    # Frozen C1 score, using ONLY the frozen dev-fit parameters - never refit here.
    obs["z_support"] = (obs["support_signal"] - frozen["mu_support"]) / frozen["sigma_support"]
    obs["z_momentum"] = (obs["momentum_signal"] - frozen["mu_momentum"]) / frozen["sigma_momentum"]
    obs["is_bear"] = obs["regime"].isin(frozen["bear_regimes"]).astype(float)
    obs["c1_score"] = obs["z_support"] + obs["z_momentum"] * obs["is_bear"]

    obs_h = obs[obs["horizon"] == PRIMARY_HORIZON].copy()

    print("\n" + "=" * 78)
    print(f"LOCKED TEST RESULTS - primary horizon={PRIMARY_HORIZON}d, primary target=r_multiple")
    print("=" * 78)

    # --- M1 ---
    print("\n--- M1: Conditional Momentum (Bear vs Bull, strict SPY-vs-SMA200 split) ---")
    bear_mask = obs_h["bear_bull_v1"] == "Bear" if "bear_bull_v1" in obs_h.columns else obs_h["trend"] == "Bear"
    bull_mask = obs_h["trend"] == "Bull"
    ic_bear = spearman_ic(obs_h.loc[bear_mask, "momentum_signal"], obs_h.loc[bear_mask, "r_multiple"])
    ic_bull = spearman_ic(obs_h.loc[bull_mask, "momentum_signal"], obs_h.loc[bull_mask, "r_multiple"])
    n_bear, n_bull = bear_mask.sum(), bull_mask.sum()
    print(f"  IC_bear = {ic_bear:+.4f} (n={n_bear})")
    print(f"  IC_bull = {ic_bull:+.4f} (n={n_bull})")
    m1_pass = (ic_bear > 0) and (ic_bear > ic_bull)
    print(f"  Expected: IC_bear > 0 AND IC_bear > IC_bull")
    print(f"  M1 RESULT: {'CONFIRMED' if m1_pass else 'NOT CONFIRMED'}")

    # --- S1 ---
    print("\n--- S1: Support Replication (regime-independent small positive effect) ---")
    ic_overall = spearman_ic(obs_h["support_signal"], obs_h["r_multiple"])
    print(f"  IC_overall = {ic_overall:+.4f} (n={len(obs_h)})")
    per_regime_signs = []
    for regime_val, group in obs_h.dropna(subset=["regime"]).groupby("regime"):
        if len(group) < 50:
            continue
        ic_r = spearman_ic(group["support_signal"], group["r_multiple"])
        per_regime_signs.append((regime_val, ic_r, len(group)))
        print(f"    {regime_val:<15} IC={ic_r:+.4f} (n={len(group)})")
    sign_flips = sum(1 for _, ic_r, _ in per_regime_signs if ic_r < 0)
    s1_pass = (ic_overall > 0) and (sign_flips <= len(per_regime_signs) // 2)
    print(f"  Expected: IC_overall > 0, not sign-flipping in most regimes")
    print(f"  S1 RESULT: {'CONFIRMED' if s1_pass else 'NOT CONFIRMED'}")

    # --- C1 ---
    print("\n--- C1: Incremental Information (frozen Support + Bear-Conditional Momentum) ---")
    ic_support_alone = spearman_ic(obs_h["support_signal"], obs_h["r_multiple"])
    ic_c1 = spearman_ic(obs_h["c1_score"], obs_h["r_multiple"])
    print(f"  IC(support alone) = {ic_support_alone:+.4f}")
    print(f"  IC(C1 frozen combo) = {ic_c1:+.4f}")
    c1_pass = abs(ic_c1) > abs(ic_support_alone)
    print(f"  Expected: IC(C1) > IC(support alone)")
    print(f"  C1 RESULT: {'CONFIRMED' if c1_pass else 'NOT CONFIRMED'}")

    # --- Secondary / diagnostic metrics (context only, not for criterion switching) ---
    print("\n" + "=" * 78)
    print("SECONDARY METRICS (context only - do not use to redefine primary criteria)")
    print("=" * 78)
    for label_name, sig_col in [("momentum_signal", "momentum_signal"), ("support_signal", "support_signal"), ("c1_score", "c1_score")]:
        print(f"\n  {label_name}:")
        for h in sorted(obs["horizon"].unique()):
            sub = obs[obs["horizon"] == h]
            ic_h = spearman_ic(sub[sig_col], sub["r_multiple"])
            print(f"    horizon={h:>3}d  IC={ic_h:+.4f}")
        win_rate = obs_h["success"].mean()
        mfe_mean = obs_h["mfe"].mean()
        mae_mean = obs_h["mae"].mean()
        n_symbols = obs_h["symbol"].nunique()
        n_dates = obs_h["date"].nunique()
        trades_per_year_proxy = len(obs_h) / max(n_symbols, 1) / 3  # ~3 years of data
        print(f"    win_rate(success)={win_rate:.3f}  mean_MFE={mfe_mean:+.4f}  mean_MAE={mae_mean:+.4f}")
        print(f"    coverage: ~{trades_per_year_proxy:.1f} scoring opportunities/symbol/year (n_symbols={n_symbols})")

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  M1 (Conditional Momentum): {'CONFIRMED' if m1_pass else 'NOT CONFIRMED'}")
    print(f"  S1 (Support Replication):  {'CONFIRMED' if s1_pass else 'NOT CONFIRMED'}")
    print(f"  C1 (Incremental Info):     {'CONFIRMED' if c1_pass else 'NOT CONFIRMED'}")
    print("\n  Reminder: this Locked Test gives CROSS-SECTIONAL independence only")
    print("  (new tickers, same calendar period as all prior experiments) - not")
    print("  temporal independence. Treat 'CONFIRMED' as 'holds beyond the original")
    print("  300-symbol sample', not as 'will hold in a different market era.'")
    print("=" * 78)


if __name__ == "__main__":
    main()
