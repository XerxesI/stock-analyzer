"""Cycle #3: Regime-Aware Model Assembly - baseline portfolio backtest.

Transition from Signal Discovery to System Construction, per ChatGPT's guidance.
Builds the FROZEN regime-aware architecture and simulates it as an actual
portfolio (position limits, transaction costs, overlapping-signal handling),
rather than testing one more isolated feature.

Architecture (frozen, no tuning in this script):
    Bear regime -> candidate = C1 score in its "high-ranking zone" (top 20%
        among Bear-day observations, same convention as prior C1 tests)
        C1 = z_support + z_momentum  (is_bear=1 always true here since we're
        already restricted to Bear days) - using the FROZEN frozen_c1_params.json
    Bull regime -> candidate = compression_pct in bottom 20% AND rvol > 1.0
        (the VC3-RVOL "D cell" definition, unchanged)
        MF1/RVOL is used only as a tie-break ranking variable when there are
        more Bull candidates than open slots - NOT as an optimized weighted score,
        per ChatGPT's explicit instruction (practical profile was non-monotonic).

Trade mechanics: triple-barrier (2.0x/1.0x ATR, 20-day time limit) via the
already-tested validation.labeling.label_at - the realized return at exit is
derived from the ACTUAL outcome (take-profit level, stop-loss level, or the
real Close price if the time barrier was hit), not from MFE (which is a
best-case, not a realized, return).

KNOWN SIMPLIFICATION (documented, not hidden): the equity curve is updated
only when trades CLOSE (a "trade-level" equity curve), not via daily
mark-to-market of open positions' unrealized P&L. This is a common baseline
backtest simplification but understates intra-trade volatility - a limitation
for a future, more realistic version, not for this baseline.

KNOWN LIMITATION: no sector/calendar-time correlation adjustment beyond the
existing per-symbol de-duplication logic used elsewhere in this project -
concurrent signals from correlated names (e.g. same-sector) are not
specifically down-weighted. Reported as a diagnostic (max same-day entries),
not corrected for.

Usage:
    python -m stock_analyzer.backtesting.regime_aware_backtest
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.data.universe_filter import sample_universe
from stock_analyzer.signals.money_flow import calculate_money_flow_features
from stock_analyzer.signals.volatility_compression import calculate_compression_state
from stock_analyzer.swing.trade_score import calculate_trade_score
from stock_analyzer.validation.labeling import LabelingConfig, label_at
from stock_analyzer.validation.regime import build_market_regime

# ---- FROZEN architecture parameters - do not tune based on this run's results ----
SAMPLE_SIZE = 300
SAMPLE_SEED = 42  # dev sample, consistent with the whole Cycle #1/#2 exploration

FETCH_START = pd.Timestamp.today().normalize() - pd.Timedelta(days=3 * 365)
FETCH_END = pd.Timestamp.today().normalize()
TEST_START_POS = 210  # SMA200/ATR warm-up

C1_BEAR_QUANTILE = 0.80  # top 20% within Bear, same convention as locked_test.py
COMPRESSION_LOOKBACK = 100
COMPRESSION_QUANTILE = 0.20
RVOL_WINDOW = 20
RVOL_ACTIVATION_THRESHOLD = 1.0

LABELING_CONFIG = LabelingConfig(horizons=(20,))  # single 20d horizon for this backtest
TRADE_HORIZON = 20

INITIAL_CAPITAL = 100_000.0
MAX_CONCURRENT_POSITIONS = 20
POSITION_SIZE_PCT = 0.05  # 5% of current equity per position (equal-weight)
TRANSACTION_COST_BPS = 10  # each way (entry and exit charged separately)

_ARTIFACTS_REPORTS = Path(__file__).resolve().parents[2] / "artifacts" / "reports"
_CANDIDATES_PATH = _ARTIFACTS_REPORTS / "regime_aware_backtest_candidates.csv"
_FROZEN_C1_PARAMS_PATH = _ARTIFACTS_REPORTS / "frozen_c1_params.json"


# --------------------------------------------------------------------------
# Data fetching
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Realized trade return (NOT MFE - the actual outcome at exit)
# --------------------------------------------------------------------------

def realized_return_from_label(
    frame: pd.DataFrame,
    t_pos: int,
    label: dict,
    tp_mult: float = 2.0,
    sl_mult: float = 1.0,
) -> float:
    """Actual price return realized at exit, given a triple-barrier outcome.

    Unlike `mfe` (best excursion reached, not necessarily where we exited),
    this reflects what a trader following the triple-barrier rule would
    actually have realized: the fixed take-profit/stop-loss level if hit,
    or the real Close price if the time barrier resolved the trade.
    """

    outcome = label["outcome"]
    entry_price = label["entry_price"]
    atr = label["atr_at_entry"]
    if outcome == "UPPER_HIT":
        return tp_mult * atr / entry_price
    if outcome == "LOWER_HIT":
        return -sl_mult * atr / entry_price
    exit_pos = t_pos + label["exit_day"]
    exit_price = float(frame["Close"].iloc[exit_pos])
    return (exit_price - entry_price) / entry_price


# --------------------------------------------------------------------------
# Signal generation (raw values first, thresholds applied after pooling)
# --------------------------------------------------------------------------

def generate_raw_signals(
    frames: dict[str, pd.DataFrame],
    regime_df: pd.DataFrame,
    frozen_c1_params: dict,
) -> pd.DataFrame:
    """Compute raw C1 (Bear days only) and compression/RVOL (all days) signal
    values for every symbol/date, without yet applying candidate thresholds.
    """

    rows: list[dict] = []
    trend_lookup = regime_df["trend"]

    for i, (symbol, frame) in enumerate(frames.items()):
        try:
            comp_df = calculate_compression_state(frame, lookback=COMPRESSION_LOOKBACK)
            mf_df = calculate_money_flow_features(frame, rvol_window=RVOL_WINDOW)
        except ValueError:
            continue

        max_t = len(frame) - TRADE_HORIZON - 1
        for t_pos in range(TEST_START_POS, max_t):
            date = frame.index[t_pos]
            trend = trend_lookup.get(date)
            if trend not in ("Bull", "Bear"):
                continue

            rvol_val = mf_df["rvol"].iloc[t_pos]
            comp_val = comp_df["compression_pct"].iloc[t_pos]

            c1_score = None
            if trend == "Bear":
                hist = frame.iloc[: t_pos + 1]
                try:
                    result = calculate_trade_score(hist)
                except (ValueError, RuntimeError):
                    continue
                support_pts = result["components"]["support"]["points"]
                momentum_pts = result["components"]["momentum"]["points"]
                z_support = (support_pts - frozen_c1_params["mu_support"]) / frozen_c1_params["sigma_support"]
                z_momentum = (momentum_pts - frozen_c1_params["mu_momentum"]) / frozen_c1_params["sigma_momentum"]
                c1_score = z_support + z_momentum  # is_bear=1 always true here

            rows.append({
                "symbol": symbol, "date": date, "t_pos": t_pos, "regime": trend,
                "c1_score": c1_score, "compression_pct": comp_val, "rvol": rvol_val,
            })

        if (i + 1) % 30 == 0:
            print(f"  signals: processed {i + 1}/{len(frames)} symbols", flush=True)

    return pd.DataFrame(rows)


def build_candidates(
    raw_signals: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Apply frozen candidate thresholds and compute triple-barrier outcomes."""

    bear = raw_signals[raw_signals["regime"] == "Bear"].dropna(subset=["c1_score"])
    c1_threshold = bear["c1_score"].quantile(C1_BEAR_QUANTILE)
    print(f"  C1 candidate threshold (top {int((1 - C1_BEAR_QUANTILE) * 100)}% within Bear): {c1_threshold:.3f}", flush=True)

    bull = raw_signals[raw_signals["regime"] == "Bull"]
    compression_threshold = bull["compression_pct"].quantile(COMPRESSION_QUANTILE)
    print(f"  Compression candidate threshold (bottom {int(COMPRESSION_QUANTILE * 100)}%): {compression_threshold:.4f}", flush=True)

    bear_candidates = bear[bear["c1_score"] >= c1_threshold].copy()
    bear_candidates["rank_score"] = bear_candidates["c1_score"]

    bull_candidates = bull[
        (bull["compression_pct"] <= compression_threshold) & (bull["rvol"] > RVOL_ACTIVATION_THRESHOLD)
    ].copy()
    bull_candidates["rank_score"] = bull_candidates["rvol"]  # simple frozen tie-break

    candidates = pd.concat([bear_candidates, bull_candidates], ignore_index=True)
    print(f"  total raw candidates: {len(candidates)} (Bear={len(bear_candidates)}, Bull={len(bull_candidates)})", flush=True)

    outcome_rows = []
    for row in candidates.itertuples(index=False):
        frame = frames[row.symbol]
        label = label_at(frame, row.t_pos, TRADE_HORIZON, LABELING_CONFIG)
        if label is None:
            continue
        realized_return = realized_return_from_label(frame, row.t_pos, label)
        exit_date = frame.index[row.t_pos + label["exit_day"]]
        outcome_rows.append({
            "symbol": row.symbol, "regime": row.regime, "entry_date": row.date,
            "exit_date": exit_date, "rank_score": row.rank_score,
            "realized_return": realized_return, "outcome": label["outcome"],
        })

    return pd.DataFrame(outcome_rows)


# --------------------------------------------------------------------------
# Portfolio simulation (trade-level equity curve - see module docstring)
# --------------------------------------------------------------------------

def simulate_portfolio(
    candidates: pd.DataFrame,
    initial_capital: float = INITIAL_CAPITAL,
    max_positions: int = MAX_CONCURRENT_POSITIONS,
    position_size_pct: float = POSITION_SIZE_PCT,
    transaction_cost_bps: float = TRANSACTION_COST_BPS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Event-driven portfolio simulation over pre-computed candidate trades.

    Returns:
        (equity_curve, trades) - equity_curve has one row per business day
        (date, equity, open_positions); trades has one row per closed trade.
    """

    if candidates.empty:
        raise ValueError("No candidates to simulate.")

    candidates = candidates.sort_values("entry_date").reset_index(drop=True)
    date_range = pd.bdate_range(candidates["entry_date"].min(), candidates["exit_date"].max())

    open_positions: dict[str, dict] = {}
    equity = initial_capital
    equity_curve_rows = []
    trade_rows = []
    cost_frac = transaction_cost_bps / 10_000.0

    candidates_by_date = {d: g for d, g in candidates.groupby("entry_date")}

    for today in date_range:
        closing_symbols = [s for s, pos in open_positions.items() if pos["exit_date"] == today]
        for symbol in closing_symbols:
            pos = open_positions.pop(symbol)
            gross_pnl = pos["position_value"] * pos["realized_return"]
            exit_cost = pos["position_value"] * cost_frac
            net_pnl = gross_pnl - exit_cost
            equity += net_pnl
            trade_rows.append({
                "symbol": symbol, "regime": pos["regime"], "entry_date": pos["entry_date"],
                "exit_date": today, "position_value": pos["position_value"],
                "realized_return": pos["realized_return"], "net_pnl": net_pnl,
            })

        todays_candidates = candidates_by_date.get(today)
        if todays_candidates is not None:
            todays_candidates = todays_candidates[~todays_candidates["symbol"].isin(open_positions.keys())]
            todays_candidates = todays_candidates.drop_duplicates(subset="symbol")
            todays_candidates = todays_candidates.sort_values("rank_score", ascending=False)
            available_slots = max_positions - len(open_positions)
            to_open = todays_candidates.head(max(available_slots, 0))
            for row in to_open.itertuples(index=False):
                position_value = equity * position_size_pct
                entry_cost = position_value * cost_frac
                equity -= entry_cost
                open_positions[row.symbol] = {
                    "exit_date": row.exit_date, "position_value": position_value,
                    "realized_return": row.realized_return, "regime": row.regime,
                    "entry_date": today,
                }

        equity_curve_rows.append({"date": today, "equity": equity, "open_positions": len(open_positions)})

    return pd.DataFrame(equity_curve_rows), pd.DataFrame(trade_rows)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def compute_metrics(equity_curve: pd.DataFrame, trades: pd.DataFrame, initial_capital: float) -> dict:
    equity_curve = equity_curve.sort_values("date").reset_index(drop=True)
    total_days = (equity_curve["date"].iloc[-1] - equity_curve["date"].iloc[0]).days
    years = total_days / 365.25 if total_days > 0 else float("nan")
    final_equity = equity_curve["equity"].iloc[-1]
    cagr = (final_equity / initial_capital) ** (1 / years) - 1 if years and years > 0 else float("nan")

    running_max = equity_curve["equity"].cummax()
    drawdown = equity_curve["equity"] / running_max - 1
    max_drawdown = drawdown.min()

    daily_returns = equity_curve["equity"].pct_change().dropna()
    daily_returns = daily_returns[daily_returns != 0]
    sharpe = (
        daily_returns.mean() / daily_returns.std() * np.sqrt(252)
        if len(daily_returns) > 1 and daily_returns.std() > 0 else float("nan")
    )
    downside = daily_returns[daily_returns < 0]
    sortino = (
        daily_returns.mean() / downside.std() * np.sqrt(252)
        if len(downside) > 1 and downside.std() > 0 else float("nan")
    )

    if trades.empty:
        return {
            "cagr": cagr, "max_drawdown": max_drawdown, "sharpe": sharpe, "sortino": sortino,
            "trade_count": 0, "trades_per_year": 0.0, "profit_factor": float("nan"),
            "expectancy_per_trade": float("nan"), "regime_exposure": {}, "max_same_day_entries": 0,
        }

    wins = trades[trades["net_pnl"] > 0]["net_pnl"]
    losses = trades[trades["net_pnl"] <= 0]["net_pnl"]
    profit_factor = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    expectancy = trades["net_pnl"].mean()
    trades_per_year = len(trades) / years if years and years > 0 else float("nan")
    regime_exposure = trades["regime"].value_counts(normalize=True).to_dict()
    entries_per_day = trades.groupby("entry_date").size()
    max_same_day_entries = int(entries_per_day.max()) if len(entries_per_day) else 0

    return {
        "cagr": cagr, "max_drawdown": max_drawdown, "sharpe": sharpe, "sortino": sortino,
        "trade_count": len(trades), "trades_per_year": trades_per_year,
        "profit_factor": profit_factor, "expectancy_per_trade": expectancy,
        "regime_exposure": regime_exposure, "max_same_day_entries": max_same_day_entries,
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    with open(_FROZEN_C1_PARAMS_PATH) as f:
        frozen_c1_params = json.load(f)
    print("loaded FROZEN C1 parameters (unchanged from Cycle #1)", flush=True)

    regime_df = _fetch_regime()

    print(f"\nsampling {SAMPLE_SIZE} symbols (seed={SAMPLE_SEED}, dev sample)...", flush=True)
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

    print("\ngenerating raw signals (this is the slow step - Bear-day C1 scoring)...", flush=True)
    raw_signals = generate_raw_signals(frames, regime_df, frozen_c1_params)

    print("\nbuilding candidates + triple-barrier outcomes...", flush=True)
    candidates = build_candidates(raw_signals, frames)
    _ARTIFACTS_REPORTS.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(_CANDIDATES_PATH, index=False)
    print(f"  {len(candidates)} candidates with outcomes saved to {_CANDIDATES_PATH}", flush=True)

    print("\nsimulating portfolio...", flush=True)
    equity_curve, trades = simulate_portfolio(candidates)

    metrics = compute_metrics(equity_curve, trades, INITIAL_CAPITAL)

    print("\n" + "=" * 78)
    print("CYCLE #3 BASELINE PORTFOLIO - RESULTS")
    print("=" * 78)
    print(f"  Initial capital:      ${INITIAL_CAPITAL:,.0f}")
    print(f"  Final equity:         ${equity_curve['equity'].iloc[-1]:,.0f}")
    print(f"  CAGR:                 {metrics['cagr']:+.2%}")
    print(f"  Max drawdown:         {metrics['max_drawdown']:.2%}")
    print(f"  Sharpe:               {metrics['sharpe']:.2f}")
    print(f"  Sortino:              {metrics['sortino']:.2f}")
    print(f"  Trade count:          {metrics['trade_count']}")
    print(f"  Trades per year:      {metrics['trades_per_year']:.1f}")
    print(f"  Profit factor:        {metrics['profit_factor']:.2f}")
    print(f"  Expectancy/trade:     ${metrics['expectancy_per_trade']:.2f}")
    print(f"  Regime exposure:      {metrics['regime_exposure']}")
    print(f"  Max same-day entries: {metrics['max_same_day_entries']}  (calendar clustering diagnostic)")
    print("\n  KNOWN LIMITATIONS (see module docstring):")
    print("  - Trade-level equity curve (marks only at trade close, not daily")
    print("    mark-to-market of open positions) - understates intra-trade volatility.")
    print("  - No sector/correlation-based signal down-weighting.")
    print("  - C1/compression thresholds computed via a single global quantile over")
    print("    the whole backtest period (look-ahead in threshold-SETTING only, same")
    print("    convention used throughout Cycle #1/#2 - not a new violation, but a")
    print("    known simplification for a future walk-forward version).")
    print("=" * 78)


if __name__ == "__main__":
    main()