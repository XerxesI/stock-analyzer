"""Cycle #2: RSI-O1 oversold reversal Signal Lab test, per ChatGPT's precise
pre-registration.

Hypothesis RSI-O1: RSI(14) < 30 predicts a better subsequent swing-trade outcome
than RSI >= 30, measured by 20-day triple-barrier R-multiple and success rate.

This tests a STATE (RSI<30 vs RSI>=30), not the existing continuous rsi_signal
(Trade Score v2 component - which rewards HIGHER RSI, testing a "strength"
hypothesis, the OPPOSITE economic idea from mean-reversion/oversold).

Pre-registered before running:
    Primary horizon: 20 trading days (matches C1/MF1 for comparability)
    Secondary horizons: 10 and 40 trading days
    Primary metrics: success rate DELTA (oversold vs not) and median R-multiple
        DELTA - NOT Spearman IC, since the signal is binary, not continuous.
    Secondary: MFE/MAE profile for both groups (oversold-reversal setups may have
        larger upside excursions but also larger downside excursions).
    Broken out by: Bull vs Bear regime, analyzed separately.
    No RSI threshold sweep (20/25/30/35...) - only 30, fixed before testing.
    No combination with MF1/C1 yet - RSI-O1's independent value must be
        established first.

Runs on the dev sample (seed=42), matching Cycle #2's discipline for new hypotheses.

Usage:
    python -m stock_analyzer.evaluation.rsi_oversold_test
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.validation.labeling import LabelingConfig, label_at
from stock_analyzer.validation.regime import build_market_regime, tag_observations

DEV_SAMPLE_SIZE = 300
DEV_SEED = 42

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210
STEP_DAYS = 5

RSI_OVERSOLD_THRESHOLD = 30  # FIXED - no sweep
LABELING_CONFIG = LabelingConfig(horizons=(10, 20, 40))
PRIMARY_HORIZON = 20

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "rsi_oversold_test_obs.csv"


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
    print("fetching SPY (+ attempting ^VIX) for regime...", flush=True)
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


def _report(sub: pd.DataFrame, label: str) -> dict:
    oversold = sub[sub["rsi_oversold"]]
    not_oversold = sub[~sub["rsi_oversold"]]
    return {
        "label": label,
        "n_oversold": len(oversold),
        "n_not_oversold": len(not_oversold),
        "success_oversold": oversold["success"].mean() if len(oversold) > 0 else float("nan"),
        "success_not_oversold": not_oversold["success"].mean() if len(not_oversold) > 0 else float("nan"),
        "median_r_oversold": oversold["r_multiple"].median() if len(oversold) > 0 else float("nan"),
        "median_r_not_oversold": not_oversold["r_multiple"].median() if len(not_oversold) > 0 else float("nan"),
        "mean_mfe_oversold": oversold["mfe"].mean() if len(oversold) > 0 else float("nan"),
        "mean_mfe_not_oversold": not_oversold["mfe"].mean() if len(not_oversold) > 0 else float("nan"),
        "mean_mae_oversold": oversold["mae"].mean() if len(oversold) > 0 else float("nan"),
        "mean_mae_not_oversold": not_oversold["mae"].mean() if len(not_oversold) > 0 else float("nan"),
    }


def _print_report(r: dict) -> None:
    print(f"\n  {r['label']}:")
    print(f"    n: oversold={r['n_oversold']:>6}  not_oversold={r['n_not_oversold']:>6}")
    print(
        f"    success rate: oversold={r['success_oversold']:.3f}  "
        f"not_oversold={r['success_not_oversold']:.3f}  "
        f"delta={r['success_oversold'] - r['success_not_oversold']:+.3f}"
    )
    print(
        f"    median R:     oversold={r['median_r_oversold']:+.3f}  "
        f"not_oversold={r['median_r_not_oversold']:+.3f}  "
        f"delta={r['median_r_oversold'] - r['median_r_not_oversold']:+.3f}"
    )
    print(
        f"    mean MFE:     oversold={r['mean_mfe_oversold']:+.4f}  "
        f"not_oversold={r['mean_mfe_not_oversold']:+.4f}"
    )
    print(
        f"    mean MAE:     oversold={r['mean_mae_oversold']:+.4f}  "
        f"not_oversold={r['mean_mae_not_oversold']:+.4f}"
    )


def main() -> None:
    regime_df = _fetch_regime()

    print(f"\nsampling {DEV_SAMPLE_SIZE} symbols (seed={DEV_SEED}, dev sample)...", flush=True)
    symbols = sample_universe(DEV_SAMPLE_SIZE, seed=DEV_SEED)

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
    print(f"\ncomputing RSI state + labels (step={STEP_DAYS} bars)...", flush=True)
    for i, (symbol, frame) in enumerate(frames.items()):
        max_t = len(frame) - max(LABELING_CONFIG.horizons) - 1
        for t_pos in range(TEST_START_POS, max_t, STEP_DAYS):
            date = frame.index[t_pos]
            rsi_val = frame["RSI"].iloc[t_pos]
            if pd.isna(rsi_val):
                continue
            is_oversold = rsi_val < RSI_OVERSOLD_THRESHOLD
            for horizon in LABELING_CONFIG.horizons:
                label = label_at(frame, t_pos, horizon, LABELING_CONFIG)
                if label is None:
                    continue
                rows.append({
                    "symbol": symbol, "date": date, "horizon": horizon, **label,
                    "rsi": rsi_val, "rsi_oversold": is_oversold,
                })
        if (i + 1) % 30 == 0:
            print(f"  processed {i + 1}/{len(frames)} symbols", flush=True)

    _ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
    obs = pd.DataFrame(rows)
    obs.to_csv(_OBS_PATH, index=False)
    print(f"\ntotal observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

    obs = tag_observations(obs, regime_df)

    print("\n" + "=" * 90)
    print("RSI-O1: RSI(14) < 30 vs >= 30 - success rate / median R / MFE-MAE profile")
    print("=" * 90)

    for horizon in sorted(obs["horizon"].unique()):
        tag = "PRIMARY" if horizon == PRIMARY_HORIZON else "secondary"
        print(f"\n{'#' * 90}")
        print(f"# HORIZON = {horizon}d ({tag})")
        print(f"{'#' * 90}")

        obs_h = obs[obs["horizon"] == horizon]
        overall = _report(obs_h, "Overall (Bull+Bear)")
        _print_report(overall)

        for trend_val in ["Bull", "Bear"]:
            sub = obs_h[obs_h["trend"] == trend_val]
            if len(sub) < 60:
                print(f"\n  {trend_val}: insufficient data (n={len(sub)})")
                continue
            _print_report(_report(sub, trend_val))

    print("\n" + "=" * 90)
    print("Reminder: this is EXPLORATORY (dev sample, seed=42). Do not sweep other RSI")
    print("thresholds, and do not combine with MF1/C1 yet - RSI-O1's independent value")
    print("must be established first, ideally with a rolling-window stability check")
    print("(same pattern as rs_rolling_window.py / money_flow_rolling_window.py)")
    print("before any Locked Test confirmation.")
    print("=" * 90)


if __name__ == "__main__":
    main()
