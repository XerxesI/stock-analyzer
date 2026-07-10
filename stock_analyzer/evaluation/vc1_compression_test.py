"""Cycle #2: VC1 - Volatility Compression State diagnostic, per ChatGPT's guidance.

Pure STATE test (not yet an activation/breakout trigger - that would be VC2, a
separate future step): does being in a compressed-bands state predict the outcome
DISTRIBUTION (direction via success rate / median R, AND amplitude via MFE/MAE)
over the next 10/20 days?

Pre-registered before running:
    Compression definition: bottom quintile of compression_pct (Bollinger Band
        Width's position within its own trailing 100-day min/max range) - i.e.
        "tightest 20% of bands seen in the last ~100 days".
    Primary horizon: 20 trading days (matches the rest of Cycle #1/#2)
    Secondary horizon: 10 trading days
    Metrics (both direction AND amplitude, per ChatGPT's explicit instruction -
        compression may predict amplitude only, similar to what happened with
        RVOL/RSI's extreme-state findings):
            - success rate delta (compressed vs not)
            - median R delta
            - mean MFE, mean MAE for both groups
    Broken out by Bull vs Bear regime.
    No activation trigger tested here (no breakout, no RVOL combination) - that
    is reserved for VC2, only if VC1 shows useful information.

Runs on the dev sample (seed=42).

Usage:
    python -m stock_analyzer.evaluation.vc1_compression_test
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.signals.volatility_compression import calculate_compression_state
from stock_analyzer.validation.labeling import LabelingConfig, label_at
from stock_analyzer.validation.regime import build_market_regime, tag_observations

DEV_SAMPLE_SIZE = 300
DEV_SEED = 42

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210
STEP_DAYS = 5

COMPRESSION_LOOKBACK = 100
COMPRESSION_QUANTILE = 0.20  # bottom 20% of compression_pct = "compressed"
LABELING_CONFIG = LabelingConfig(horizons=(10, 20))
PRIMARY_HORIZON = 20

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "vc1_compression_test_obs.csv"


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
    compressed = sub[sub["is_compressed"]]
    not_compressed = sub[~sub["is_compressed"]]
    return {
        "label": label,
        "n_compressed": len(compressed),
        "n_not_compressed": len(not_compressed),
        "success_compressed": compressed["success"].mean() if len(compressed) > 0 else float("nan"),
        "success_not_compressed": not_compressed["success"].mean() if len(not_compressed) > 0 else float("nan"),
        "median_r_compressed": compressed["r_multiple"].median() if len(compressed) > 0 else float("nan"),
        "median_r_not_compressed": not_compressed["r_multiple"].median() if len(not_compressed) > 0 else float("nan"),
        "mean_mfe_compressed": compressed["mfe"].mean() if len(compressed) > 0 else float("nan"),
        "mean_mfe_not_compressed": not_compressed["mfe"].mean() if len(not_compressed) > 0 else float("nan"),
        "mean_mae_compressed": compressed["mae"].mean() if len(compressed) > 0 else float("nan"),
        "mean_mae_not_compressed": not_compressed["mae"].mean() if len(not_compressed) > 0 else float("nan"),
    }


def _print_report(r: dict) -> None:
    print(f"\n  {r['label']}:")
    print(f"    n: compressed={r['n_compressed']:>6}  not_compressed={r['n_not_compressed']:>6}")
    print(
        f"    success rate: compressed={r['success_compressed']:.3f}  "
        f"not_compressed={r['success_not_compressed']:.3f}  "
        f"delta={r['success_compressed'] - r['success_not_compressed']:+.3f}"
    )
    print(
        f"    median R:     compressed={r['median_r_compressed']:+.3f}  "
        f"not_compressed={r['median_r_not_compressed']:+.3f}  "
        f"delta={r['median_r_compressed'] - r['median_r_not_compressed']:+.3f}"
    )
    print(
        f"    mean MFE:     compressed={r['mean_mfe_compressed']:+.4f}  "
        f"not_compressed={r['mean_mfe_not_compressed']:+.4f}"
    )
    print(
        f"    mean MAE:     compressed={r['mean_mae_compressed']:+.4f}  "
        f"not_compressed={r['mean_mae_not_compressed']:+.4f}"
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
    print(f"\ncomputing compression state + labels (step={STEP_DAYS} bars)...", flush=True)
    for i, (symbol, frame) in enumerate(frames.items()):
        comp_df = calculate_compression_state(frame, lookback=COMPRESSION_LOOKBACK)
        max_t = len(frame) - max(LABELING_CONFIG.horizons) - 1
        for t_pos in range(TEST_START_POS, max_t, STEP_DAYS):
            date = frame.index[t_pos]
            comp_val = comp_df["compression_pct"].iloc[t_pos]
            if pd.isna(comp_val):
                continue
            for horizon in LABELING_CONFIG.horizons:
                label = label_at(frame, t_pos, horizon, LABELING_CONFIG)
                if label is None:
                    continue
                rows.append({
                    "symbol": symbol, "date": date, "horizon": horizon, **label,
                    "compression_pct": comp_val,
                })
        if (i + 1) % 30 == 0:
            print(f"  processed {i + 1}/{len(frames)} symbols", flush=True)

    _ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
    obs = pd.DataFrame(rows)
    obs.to_csv(_OBS_PATH, index=False)
    print(f"\ntotal observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

    obs = tag_observations(obs, regime_df)

    print("\n" + "=" * 90)
    print(f"VC1: bottom {int(COMPRESSION_QUANTILE*100)}% compression_pct (compressed) vs rest")
    print("=" * 90)

    for horizon in sorted(obs["horizon"].unique()):
        tag = "PRIMARY" if horizon == PRIMARY_HORIZON else "secondary"
        print(f"\n{'#' * 90}")
        print(f"# HORIZON = {horizon}d ({tag})")
        print(f"{'#' * 90}")

        obs_h = obs[obs["horizon"] == horizon].copy()
        threshold = obs_h["compression_pct"].quantile(COMPRESSION_QUANTILE)
        obs_h["is_compressed"] = obs_h["compression_pct"] <= threshold

        overall = _report(obs_h, "Overall (Bull+Bear)")
        _print_report(overall)

        for trend_val in ["Bull", "Bear"]:
            sub = obs_h[obs_h["trend"] == trend_val]
            if len(sub) < 60:
                print(f"\n  {trend_val}: insufficient data (n={len(sub)})")
                continue
            _print_report(_report(sub, trend_val))

    print("\n" + "=" * 90)
    print("Reminder: this is EXPLORATORY (dev sample, seed=42). This tests the STATE")
    print("only - do not test breakout/activation triggers (VC2) unless this shows")
    print("useful information (direction OR amplitude). Do not combine with MF1/C1 yet.")
    print("=" * 90)


if __name__ == "__main__":
    main()
