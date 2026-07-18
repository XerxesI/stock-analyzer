"""SWING_20 point-in-time feature computation -- replication pass v1.

Implements only the features cleared for this first pass per
``docs/03_research/SWING20_PointInTime_Feature_Specification_v1.md`` sections 1 and 2:

    stock-level: return_5d, return_20d, rsi_14, rvol_20, compression_pct_100, adv20
    market-context: spy_trend, spy_volatility_bucket

C1, QQQ relative trend, market breadth, and sector-context features are explicitly out
of scope for this module -- see the Feature Specification for why each is gated behind
a separate prerequisite.

Every feature is computed strictly from information available at the signal date's
close, the same causality convention as ``stock_analyzer.validation.regime``: a value
at date ``t`` only ever depends on rows at or before ``t``.

This module intentionally does not implement new indicator math. It only reuses:
    stock_analyzer.core.indicators.calculate_indicators       (RSI, Bollinger Bands)
    stock_analyzer.signals.money_flow.calculate_money_flow_features   (RVOL)
    stock_analyzer.signals.volatility_compression.calculate_compression_state
    stock_analyzer.validation.regime.build_market_regime       (SPY trend/volatility)
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.signals.money_flow import calculate_money_flow_features
from stock_analyzer.signals.volatility_compression import calculate_compression_state
from stock_analyzer.validation.regime import build_market_regime

RETURN_WINDOWS = (5, 20)
RVOL_WINDOW = 20
COMPRESSION_LOOKBACK = 100

STOCK_LEVEL_FEATURE_COLUMNS = ("return_5d", "return_20d", "rsi_14", "rvol_20", "compression_pct_100")
MARKET_CONTEXT_FEATURE_COLUMNS = ("spy_trend", "spy_volatility_bucket")
FEATURE_SPECIFICATION_VERSION = "SWING20_PointInTime_Feature_Specification_v1"

_PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def compute_stock_level_features(symbol_prices: pd.DataFrame) -> pd.DataFrame:
    """Compute return/RSI/RVOL/compression features for one symbol's date-indexed OHLCV.

    ``symbol_prices`` must be sorted ascending by date with Open/High/Low/Close/Volume
    columns (the frozen SWING_20 ``prices`` artifact's per-symbol shape). Returns a
    frame aligned to the same date index; every value at date ``t`` uses only rows at
    or before ``t`` -- ``pct_change``, ``rolling``, and the reused indicator functions
    are all backward-looking by construction, so this function adds no new look-ahead
    risk beyond what those already-reviewed functions carry.
    """

    missing = set(_PRICE_COLUMNS) - set(symbol_prices.columns)
    if missing:
        raise ValueError(f"symbol_prices is missing required columns: {sorted(missing)}")

    enriched = calculate_indicators(symbol_prices)
    money_flow = calculate_money_flow_features(symbol_prices, rvol_window=RVOL_WINDOW)
    compression = calculate_compression_state(enriched, lookback=COMPRESSION_LOOKBACK)

    close = symbol_prices["Close"]
    return pd.DataFrame(
        {
            "return_5d": close.pct_change(RETURN_WINDOWS[0]),
            "return_20d": close.pct_change(RETURN_WINDOWS[1]),
            "rsi_14": enriched["RSI"],
            "rvol_20": money_flow["rvol"],
            "compression_pct_100": compression["compression_pct"],
        },
        index=symbol_prices.index,
    )


def compute_market_context(
    spy_prices: pd.DataFrame,
    vix_close: pd.Series | None = None,
) -> pd.DataFrame:
    """Compute spy_trend/spy_volatility_bucket from SPY's own date-indexed OHLCV.

    Thin wrapper around ``validation.regime.build_market_regime`` that first runs
    ``calculate_indicators`` (which ``build_market_regime`` requires for SMA200/ATR14).
    Reused as-is, not reimplemented.
    """

    enriched = calculate_indicators(spy_prices)
    regime = build_market_regime(enriched, vix_close=vix_close)
    return pd.DataFrame(
        {"spy_trend": regime["trend"], "spy_volatility_bucket": regime["volatility"]},
        index=regime.index,
    ).rename_axis("date")


def build_feature_dataset(
    labels: pd.DataFrame,
    prices: pd.DataFrame,
    market_context: pd.DataFrame,
    progress_every: int | None = None,
) -> pd.DataFrame:
    """Build the point-in-time feature dataset for exactly the rows in ``labels``.

    ``labels`` must already be the target population (e.g. post-quarantine,
    post-target-gap-exclusion, train+validation only) -- this function applies none of
    that filtering itself. It only computes features for the ``(symbol, date)`` pairs
    already present in ``labels`` and left-joins them on, so a row with a feature this
    function cannot compute (e.g. inside a warm-up window) keeps its label but gets
    ``NaN`` features rather than being silently dropped.

    ``labels`` must already carry an ``adv20`` column -- every SWING_20 audit label
    population does (``build_audit_frames`` merges it in from eligibility), so this
    function reuses it rather than re-merging eligibility itself and risking a
    duplicate-column collision (``adv20_x``/``adv20_y``) if the caller's ``labels``
    already has one.
    """

    if labels.empty:
        return labels.copy()

    required_symbols = sorted(labels["symbol"].unique())
    total = len(required_symbols)
    start_time = time.monotonic()

    per_symbol_features: list[pd.DataFrame] = []
    for position, symbol in enumerate(required_symbols, start=1):
        symbol_prices = (
            prices[prices["symbol"] == symbol]
            .sort_values("date")
            .set_index("date")[_PRICE_COLUMNS]
        )
        if symbol_prices.empty:
            continue
        feats = compute_stock_level_features(symbol_prices).reset_index().rename(columns={"index": "date"})
        feats.insert(0, "symbol", symbol)
        per_symbol_features.append(feats)

        if progress_every and (position % progress_every == 0 or position == total):
            elapsed = time.monotonic() - start_time
            rate = position / elapsed if elapsed > 0 else 0
            remaining = total - position
            eta = f"{remaining / rate / 60:.1f} min" if rate > 0 else "unknown"
            print(f"[features] {position}/{total} symbols processed -- ETA {eta}", flush=True)

    stock_features = (
        pd.concat(per_symbol_features, ignore_index=True)
        if per_symbol_features
        else pd.DataFrame(columns=["symbol", "date", *STOCK_LEVEL_FEATURE_COLUMNS])
    )
    stock_features["date"] = pd.to_datetime(stock_features["date"])

    merged = labels.merge(stock_features, on=["symbol", "date"], how="left")

    market_context_reset = market_context.reset_index()
    market_context_reset["date"] = pd.to_datetime(market_context_reset["date"])
    merged = merged.merge(
        market_context_reset[["date", *MARKET_CONTEXT_FEATURE_COLUMNS]], on="date", how="left"
    )

    return merged


def build_lineage(
    swing20_snapshot_dir: str,
    swing20_manifest: dict[str, object],
    feature_dataset: pd.DataFrame,
) -> dict[str, object]:
    """Assemble the lineage block a generated feature artifact must carry.

    Per ``docs/01_architecture/Context_Engine_Architecture_Proposal_v1.md`` section 8
    (snapshot lineage), a feature dataset must always be traceable to exactly the
    SWING_20 snapshot, feature specification version, and code commit that produced it,
    without relying on file timestamps or chat history. Reuses
    ``prepare._provenance()`` (the same git-commit/dependency-version lookup already
    used for every SWING_20 snapshot manifest) rather than reimplementing it.
    """

    import hashlib
    from datetime import datetime, timezone

    from stock_analyzer.datasets.swing_20.prepare import _provenance

    feature_hash = hashlib.sha256(
        pd.util.hash_pandas_object(feature_dataset, index=True).values.tobytes()
    ).hexdigest()

    return {
        "source_swing20_snapshot_id": swing20_manifest.get("dataset_version"),
        "source_swing20_snapshot_dir": str(swing20_snapshot_dir),
        "source_swing20_artifact_hashes": swing20_manifest.get("artifact_hashes"),
        "feature_specification_version": FEATURE_SPECIFICATION_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "feature_dataset_row_count": int(len(feature_dataset)),
        "feature_dataset_hash": feature_hash,
        **_provenance(),
    }
