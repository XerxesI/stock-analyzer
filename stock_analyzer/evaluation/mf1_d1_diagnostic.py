"""D1 diagnostic follow-up, per ChatGPT's precise feedback on §13:

    1. Does D1's low success rate persist WITHIN average-dollar-volume (ADV20)
       liquidity terciles, or is it confounded with genuinely illiquid (small-cap)
       stocks specifically? RVOL measures RELATIVE activity (vs a stock's own
       history), not absolute liquidity - a large-cap at RVOL=0.4 and a micro-cap
       at RVOL=0.4 are very different in absolute liquidity terms.

    2. D1's mean R (1.617) is surprisingly HIGH despite the LOWEST success rate
       (0.272) - this smells like a fat-tailed distribution (many small losses,
       a few very large wins). Check the full R-multiple distribution (percentiles,
       trimmed/winsorized mean) before treating D1 as simply "bad" - a naive
       exclusion filter could remove rare, high-value setups the project actually
       wants to find.

Naming per ChatGPT: this is a "Low Relative Participation" hypothesis, NOT a
"Liquidity Filter" - the distinction matters and is preserved in this script's
naming and output.

DIAGNOSTIC ONLY - does not change MF1's status, does not tune the RVOL window/
threshold, does not decide anything about D1's practical treatment. Requires
re-fetching the Locked Test's 300 symbols (seed=123) since ADV20 needs raw
Close/Volume, which the saved observations file does not contain.

Usage:
    python -m stock_analyzer.evaluation.mf1_d1_diagnostic
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.signals.money_flow import calculate_money_flow_features
from stock_analyzer.validation.labeling import LabelingConfig, label_at
from stock_analyzer.validation.regime import build_market_regime, tag_observations

LOCKED_SAMPLE_SIZE = 300
LOCKED_SEED = 123  # same Locked Test sample as M1/S1/C1/MF1

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210
STEP_DAYS = 5

RVOL_WINDOW = 20
ADV_WINDOW = 20
LABELING_CONFIG = LabelingConfig(horizons=(20,))  # only need the primary horizon here
PRIMARY_HORIZON = 20

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_OBS_PATH = _ARTIFACTS_REPORTS / "mf1_d1_diagnostic_obs.csv"


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


def trimmed_mean(series: pd.Series, proportion: float = 0.1) -> float:
    """Mean after dropping `proportion` from each tail (no scipy dependency)."""
    sorted_vals = series.sort_values().to_numpy()
    n = len(sorted_vals)
    k = int(n * proportion)
    trimmed = sorted_vals[k: n - k] if n - 2 * k > 0 else sorted_vals
    return float(np.mean(trimmed))


def winsorized_mean(series: pd.Series, limit: float = 0.05) -> float:
    """Mean after clipping extreme values to the `limit`/`1-limit` quantiles."""
    lower = series.quantile(limit)
    upper = series.quantile(1 - limit)
    return float(series.clip(lower, upper).mean())


def main() -> None:
    regime_df = _fetch_regime()

    print(f"\nre-fetching {LOCKED_SAMPLE_SIZE} LOCKED symbols (seed={LOCKED_SEED}) "
          f"to compute ADV20 (needs raw Close/Volume, not in saved obs)...", flush=True)
    symbols = sample_universe(LOCKED_SAMPLE_SIZE, seed=LOCKED_SEED)

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
    print(f"\ncomputing RVOL + ADV20 + labels (step={STEP_DAYS} bars)...", flush=True)
    for i, (symbol, frame) in enumerate(frames.items()):
        mf_df = calculate_money_flow_features(frame, rvol_window=RVOL_WINDOW)
        adv20 = (frame["Close"] * frame["Volume"]).rolling(ADV_WINDOW).mean()

        max_t = len(frame) - max(LABELING_CONFIG.horizons) - 1
        for t_pos in range(TEST_START_POS, max_t, STEP_DAYS):
            date = frame.index[t_pos]
            rvol_val = mf_df["rvol"].iloc[t_pos]
            adv_val = adv20.iloc[t_pos]
            if pd.isna(rvol_val) or pd.isna(adv_val):
                continue
            label = label_at(frame, t_pos, PRIMARY_HORIZON, LABELING_CONFIG)
            if label is None:
                continue
            rows.append({"symbol": symbol, "date": date, "rvol": rvol_val, "adv20": adv_val, **label})
        if (i + 1) % 30 == 0:
            print(f"  processed {i + 1}/{len(frames)} symbols", flush=True)

    _ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
    obs = pd.DataFrame(rows)
    obs.to_csv(_OBS_PATH, index=False)
    print(f"\ntotal observations: {len(obs)} (saved to {_OBS_PATH})", flush=True)

    obs = tag_observations(obs, regime_df)
    bull_h = obs[obs["trend"] == "Bull"].copy()
    print(f"Bull-regime observations: n={len(bull_h)}", flush=True)

    bull_h["rvol_decile"] = pd.qcut(bull_h["rvol"], 10, labels=False, duplicates="drop") + 1
    bull_h["is_d1"] = bull_h["rvol_decile"] == 1

    print("\n" + "=" * 100)
    print("1. D1 vs non-D1 SUCCESS RATE, WITHIN ADV20 (liquidity) TERCILES")
    print("   (Low Relative Participation hypothesis - is D1 confounded with absolute illiquidity?)")
    print("=" * 100)
    bull_h["adv_tercile"] = pd.qcut(bull_h["adv20"], 3, labels=["Low_ADV", "Medium_ADV", "High_ADV"])

    for tercile in ["Low_ADV", "Medium_ADV", "High_ADV"]:
        sub = bull_h[bull_h["adv_tercile"] == tercile]
        d1_sub = sub[sub["is_d1"]]
        non_d1_sub = sub[~sub["is_d1"]]
        if len(d1_sub) < 30 or len(non_d1_sub) < 30:
            print(f"  {tercile:<12} insufficient data (D1 n={len(d1_sub)}, non-D1 n={len(non_d1_sub)})")
            continue
        print(
            f"  {tercile:<12} D1 success={d1_sub['success'].mean():.3f} (n={len(d1_sub)})   "
            f"non-D1 success={non_d1_sub['success'].mean():.3f} (n={len(non_d1_sub)})   "
            f"gap={d1_sub['success'].mean() - non_d1_sub['success'].mean():+.3f}"
        )

    print("\n  Interpretation:")
    print("    If the D1 gap (D1 success - non-D1 success) is consistently negative")
    print("    across ALL THREE liquidity terciles (including High_ADV, i.e. genuinely")
    print("    liquid large-caps): this is a 'low relative participation' effect, not")
    print("    simply 'D1 = illiquid small-caps'. If the gap vanishes in High_ADV: D1's")
    print("    effect is likely confounded with absolute illiquidity.")

    print("\n" + "=" * 100)
    print("2. D1 R-MULTIPLE DISTRIBUTION (is the high mean R driven by rare big winners?)")
    print("=" * 100)
    d1 = bull_h[bull_h["is_d1"]]["r_multiple"].dropna()
    rest = bull_h[~bull_h["is_d1"]]["r_multiple"].dropna()

    for label, series in [("D1", d1), ("D2-D10 (rest)", rest)]:
        print(f"\n  {label} (n={len(series)}):")
        print(f"    mean            = {series.mean():+.4f}")
        print(f"    median (p50)    = {series.median():+.4f}")
        print(f"    p25             = {series.quantile(0.25):+.4f}")
        print(f"    p75             = {series.quantile(0.75):+.4f}")
        print(f"    p90             = {series.quantile(0.90):+.4f}")
        print(f"    p95             = {series.quantile(0.95):+.4f}")
        print(f"    trimmed_mean(10%) = {trimmed_mean(series, 0.10):+.4f}")
        print(f"    winsorized_mean(5%) = {winsorized_mean(series, 0.05):+.4f}")

    print("\n  Interpretation:")
    print("    If D1's mean is much higher than its median/trimmed/winsorized mean,")
    print("    while D2-D10's mean and median are close together: D1's high mean R is")
    print("    driven by a small number of extreme winners (fat right tail), not a")
    print("    generally better distribution. A blanket D1 exclusion rule would then")
    print("    risk discarding rare, high-value setups along with the many losers.")
    print("=" * 100)


if __name__ == "__main__":
    main()