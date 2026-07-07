"""Phase 2 (Research Protocol v1.2): retest the existing 4 signals - trend, momentum,
RSI, support - with the new triple-barrier / MFE-MAE / R-multiple target and market
regime tagging, instead of the fixed-date forward-return target used in the earlier
(swing_rank_ic_test.py / component_ic_test.py) experiments.

Same 300-symbol random sample (seed=42) as the earlier "broad_sample" out-of-sample
check, for direct comparability with those prior results.

Signal values are read directly from Trade Score v2's component breakdown
(swing.trade_score.calculate_trade_score), NOT the combined score - per Protocol
section 5, components are tested separately, never pre-combined.

Usage:
    python -m stock_analyzer.evaluation.phase2_retest
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.swing.trade_score import calculate_trade_score
from stock_analyzer.validation.ic_test import (
    diagnostic_segment_ic,
    quintile_summary,
    run_walk_forward_ic,
)
from stock_analyzer.validation.labeling import LabelingConfig, label_at
from stock_analyzer.validation.regime import build_market_regime, tag_observations

SAMPLE_SIZE = 300
SAMPLE_SEED = 42

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210  # warm-up for SMA200/ATR
STEP_DAYS = 5  # ~weekly re-scoring, matches earlier experiments

LABELING_CONFIG = LabelingConfig(horizons=(5, 10, 20, 40))
MIN_HISTORY_BARS = 210

SIGNAL_COLS = ["trend_signal", "momentum_signal", "rsi_signal", "support_signal"]

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "phase2_retest_obs.csv"


def _fetch(symbol: str) -> pd.DataFrame:
    raw = yf.download(
        symbol,
        start=FETCH_START.to_pydatetime(),
        end=(FETCH_END + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=20,
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
        "SPY",
        start=FETCH_START.to_pydatetime(),
        end=(FETCH_END + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=20,
    )
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    spy_raw.index = pd.to_datetime(spy_raw.index).tz_localize(None)
    spy_enriched = calculate_indicators(spy_raw.sort_index())

    vix_close = None
    try:
        vix_raw = yf.download(
            "^VIX",
            start=FETCH_START.to_pydatetime(),
            end=(FETCH_END + pd.Timedelta(days=1)).to_pydatetime(),
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
            timeout=20,
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
    print(f"  regime distribution:\n{regime_df['regime'].value_counts()}", flush=True)
    return regime_df


regime_df = _fetch_regime()

print(f"\nsampling {SAMPLE_SIZE} symbols (seed={SAMPLE_SEED})...", flush=True)
symbols = sample_universe(SAMPLE_SIZE, seed=SAMPLE_SEED)

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
            "trend_signal": score_result["components"]["trend"]["points"],
            "momentum_signal": score_result["components"]["momentum"]["points"],
            "rsi_signal": score_result["components"]["rsi"]["points"],
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
print(f"\ntotal observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

obs_tagged = tag_observations(obs, regime_df)

print("\n" + "=" * 78)
print("PHASE 2 RETEST: trend / momentum / RSI / support vs triple-barrier labels")
print("=" * 78)

for signal_col in SIGNAL_COLS:
    print(f"\n{'#' * 78}")
    print(f"# SIGNAL: {signal_col}")
    print(f"{'#' * 78}")

    print("\n-- IC vs r_multiple (train / hold-out, per horizon) --")
    results_r = run_walk_forward_ic(obs, signal_col=signal_col, target_col="r_multiple")
    for r in results_r:
        print(
            f"  horizon={r.horizon:>3}d  train_ic={r.train_ic:+.4f} (n={r.train_n:>6})"
            f"   holdout_ic={r.holdout_ic:+.4f} (n={r.holdout_n:>6})"
        )

    print("\n-- IC vs success (UPPER_HIT=1/0), train / hold-out, per horizon --")
    results_s = run_walk_forward_ic(obs, signal_col=signal_col, target_col="success")
    for r in results_s:
        print(
            f"  horizon={r.horizon:>3}d  train_ic={r.train_ic:+.4f} (n={r.train_n:>6})"
            f"   holdout_ic={r.holdout_ic:+.4f} (n={r.holdout_n:>6})"
        )

    print("\n-- Quintile summary, r_multiple, horizon=20d (Q1=lowest signal, Q5=highest) --")
    qs = quintile_summary(obs[obs["horizon"] == 20], signal_col, "r_multiple")
    if qs is not None:
        print(qs)
    else:
        print("  (not enough data to form quintiles)")

    print("\n-- DIAGNOSTIC (exploratory only, not confirmatory - Protocol section 4.4) --")
    print("-- IC vs r_multiple by market regime, horizon=20d --")
    seg = diagnostic_segment_ic(
        obs_tagged[(obs_tagged["horizon"] == 20) & obs_tagged["regime"].notna()],
        signal_col=signal_col,
        target_col="r_multiple",
        segment_col="regime",
    )
    for s in seg:
        print(f"  {s.segment_value:<15} n={s.n:>5}  ic={s.ic:+.4f}  [{s.confidence}]")

print("\n" + "=" * 78)
print("research_log.md snippets (paste in, fill in Conclusion after review)")
print("=" * 78)
for signal_col in SIGNAL_COLS:
    r20 = next((r for r in run_walk_forward_ic(obs, signal_col, "r_multiple") if r.horizon == 20), None)
    if r20 is None:
        continue
    verdict = "possible signal, needs closer look" if abs(r20.holdout_ic) > 0.03 else "no evidence of signal (noise-level IC)"
    print(f"""
### Experiment: phase2_retest_{signal_col}
- **Hypothesis:** {signal_col} predicts triple-barrier R-multiple over a 20-day horizon
- **Dataset:** Random 300-symbol sample (seed={SAMPLE_SEED}), NASDAQ+NYSE+NYSE American
- **Period:** train/hold-out 80/20 time split, ~3 years ending {FETCH_END.date()}
- **Target:** R-multiple (triple-barrier {LABELING_CONFIG.take_profit_atr_multiple}x/{LABELING_CONFIG.stop_loss_atr_multiple}x ATR), horizon=20d
- **Features:** {signal_col} (Trade Score v2 component, not combined score)
- **Result:** train IC={r20.train_ic:+.4f} (n={r20.train_n}), holdout IC={r20.holdout_ic:+.4f} (n={r20.holdout_n})
- **Conclusion:** {verdict} - review regime breakdown above before finalizing
""")