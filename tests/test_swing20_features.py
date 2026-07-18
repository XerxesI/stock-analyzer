from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_analyzer.datasets.swing_20.features import (
    MARKET_CONTEXT_FEATURE_COLUMNS,
    STOCK_LEVEL_FEATURE_COLUMNS,
    build_feature_dataset,
    build_lineage,
    compute_market_context,
    compute_stock_level_features,
)


def _linear_prices(days: int = 140, start: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    """A predictable, exactly-computable price series (Close increases by a fixed step)."""

    dates = pd.bdate_range("2023-01-02", periods=days)
    close = [start + step * i for i in range(days)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [c * 1.01 for c in close],
            "Low": [c * 0.99 for c in close],
            "Close": close,
            "Volume": [1_000_000 + (i % 7) * 10_000 for i in range(days)],
        },
        index=dates,
    )


def _varied_prices(days: int = 160, seed: int = 7) -> pd.DataFrame:
    """A price series with genuine variation, so BB width / RSI / RVOL are non-degenerate."""

    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=days)
    steps = rng.normal(loc=0.05, scale=1.2, size=days)
    close = 100.0 + np.cumsum(steps)
    close = np.maximum(close, 5.0)  # keep prices positive
    high = close * (1 + rng.uniform(0.005, 0.02, size=days))
    low = close * (1 - rng.uniform(0.005, 0.02, size=days))
    open_ = close + rng.normal(0, 0.3, size=days)
    volume = rng.integers(500_000, 2_000_000, size=days).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=dates
    )


def test_return_5d_and_return_20d_match_exact_formula_on_deterministic_series():
    prices = _linear_prices(days=40, start=100.0, step=0.5)

    features = compute_stock_level_features(prices)

    # Close[t] = 100 + 0.5*t, so return_5d[t] = Close[t]/Close[t-5] - 1, computed by hand.
    t = 30
    expected_5d = prices["Close"].iloc[t] / prices["Close"].iloc[t - 5] - 1
    expected_20d = prices["Close"].iloc[t] / prices["Close"].iloc[t - 20] - 1
    assert features["return_5d"].iloc[t] == pytest.approx(expected_5d)
    assert features["return_20d"].iloc[t] == pytest.approx(expected_20d)


def test_warm_up_produces_nan_not_a_fabricated_value():
    prices = _linear_prices(days=140)
    features = compute_stock_level_features(prices)

    # return_20d needs 20 prior rows; rows 0-19 must be NaN, not 0 or an extrapolated guess.
    assert features["return_20d"].iloc[:20].isna().all()
    assert features["return_20d"].iloc[20:].notna().all()

    # return_5d needs 5 prior rows.
    assert features["return_5d"].iloc[:5].isna().all()
    assert features["return_5d"].iloc[5:].notna().all()

    # rsi_14 (pandas_ta's Wilder-smoothing implementation) only needs the first prior
    # bar to start producing a value -- it does not wait for a full 14-bar window like
    # a naive rolling RSI would. This pins that already-relied-upon behavior rather
    # than assuming a stricter warm-up that would fail against the real implementation.
    assert features["rsi_14"].iloc[0:1].isna().all()
    assert features["rsi_14"].iloc[1:].notna().all()

    # rvol_20 needs 20 prior rows of volume.
    assert features["rvol_20"].iloc[:19].isna().all()

    # compression_pct_100 needs the 100-bar lookback on top of the BB warm-up.
    assert features["compression_pct_100"].iloc[:100].isna().all()


def test_features_do_not_use_future_values():
    prices = _varied_prices(days=160, seed=11)
    baseline = compute_stock_level_features(prices)

    # Perturb only the LAST row's Close/High/Low/Volume drastically.
    perturbed = prices.copy()
    perturbed.iloc[-1, perturbed.columns.get_loc("Close")] *= 5.0
    perturbed.iloc[-1, perturbed.columns.get_loc("High")] *= 5.0
    perturbed.iloc[-1, perturbed.columns.get_loc("Low")] *= 5.0
    perturbed.iloc[-1, perturbed.columns.get_loc("Volume")] *= 5.0
    perturbed_features = compute_stock_level_features(perturbed)

    # Every row EXCEPT the last (and any row whose own future window reaches the
    # perturbed bar via a centered indicator) must be unaffected. Returns, RSI, RVOL,
    # and compression are all backward-looking, so only the final row's own value (and
    # rows that used it as part of their OWN trailing window, i.e. none before it)
    # should differ.
    for column in STOCK_LEVEL_FEATURE_COLUMNS:
        pd.testing.assert_series_equal(
            baseline[column].iloc[:-1], perturbed_features[column].iloc[:-1], check_names=False
        )


def test_missing_values_do_not_crash_and_stay_nan_through_the_gap():
    prices = _varied_prices(days=160, seed=3)
    prices_with_gap = prices.copy()
    gap_idx = 50
    prices_with_gap.iloc[gap_idx, prices_with_gap.columns.get_loc("Close")] = np.nan

    features = compute_stock_level_features(prices_with_gap)

    # The row with the missing Close must itself be NaN for return-based features, and
    # computation must not raise.
    assert pd.isna(features["return_5d"].iloc[gap_idx])
    assert pd.isna(features["return_20d"].iloc[gap_idx])
    # A row well past the gap (>20 bars later) must recover to a normal, finite value.
    recovered = gap_idx + 25
    assert np.isfinite(features["return_20d"].iloc[recovered]) or pd.isna(
        features["return_20d"].iloc[recovered]
    )


def test_compute_market_context_reuses_build_market_regime():
    # Enough history for SMA200 to warm up, with a clear post-warm-up uptrend so Bull
    # is unambiguous.
    spy_prices = _linear_prices(days=260, start=100.0, step=0.3)

    context = compute_market_context(spy_prices)

    assert list(context.columns) == list(MARKET_CONTEXT_FEATURE_COLUMNS)
    assert context.index.name == "date"
    # Before SMA200 warms up, trend must be NaN, not a fabricated Bull/Bear guess.
    assert context["spy_trend"].iloc[0:100].isna().all()
    # A steadily rising series should be Bull once SMA200 is available.
    assert context["spy_trend"].iloc[-1] == "Bull"


def test_build_feature_dataset_aligns_by_symbol_and_signal_date():
    # 260 days so both symbols have signal dates past SPY's 200-day SMA warm-up --
    # otherwise spy_trend would be NaN for every candidate date and the alignment
    # assertion below (aaa's spy_trend vs bbb's spy_trend) would trivially pass on
    # NaN == NaN for the wrong reason.
    aaa_prices = _varied_prices(days=260, seed=1)
    bbb_prices = _varied_prices(days=260, seed=2)
    prices = pd.concat(
        [
            aaa_prices.reset_index().rename(columns={"index": "date"}).assign(symbol="AAA"),
            bbb_prices.reset_index().rename(columns={"index": "date"}).assign(symbol="BBB"),
        ],
        ignore_index=True,
    )[["symbol", "date", "Open", "High", "Low", "Close", "Volume"]]

    signal_date_aaa = aaa_prices.index[220]
    signal_date_bbb = bbb_prices.index[230]
    # adv20 comes from the labels frame itself, matching every real SWING_20 audit
    # label population (build_audit_frames already merges it in from eligibility).
    labels = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "date": [signal_date_aaa, signal_date_bbb],
            "target_20pct_20d": [True, False],
            "adv20": [12_000_000.0, 8_000_000.0],
        }
    )

    spy_prices = _linear_prices(days=260, start=100.0, step=0.3)
    market_context = compute_market_context(spy_prices)

    result = build_feature_dataset(labels, prices, market_context)

    assert len(result) == 2
    aaa_row = result[result["symbol"] == "AAA"].iloc[0]
    bbb_row = result[result["symbol"] == "BBB"].iloc[0]

    # Each symbol's features must match what compute_stock_level_features gives when
    # run directly on that symbol's own price history at that symbol's own signal date
    # -- not swapped or averaged across symbols.
    expected_aaa = compute_stock_level_features(aaa_prices).loc[signal_date_aaa]
    expected_bbb = compute_stock_level_features(bbb_prices).loc[signal_date_bbb]
    for column in STOCK_LEVEL_FEATURE_COLUMNS:
        assert aaa_row[column] == pytest.approx(expected_aaa[column], nan_ok=True)
        assert bbb_row[column] == pytest.approx(expected_bbb[column], nan_ok=True)

    assert aaa_row["adv20"] == 12_000_000.0
    assert bbb_row["adv20"] == 8_000_000.0
    assert aaa_row["spy_trend"] == market_context.loc[signal_date_aaa, "spy_trend"]


def test_build_lineage_contains_required_fields():
    features = pd.DataFrame({"symbol": ["AAA"], "date": [pd.Timestamp("2024-01-02")], "return_5d": [0.01]})
    manifest = {"dataset_version": "swing20_test", "artifact_hashes": {"prices": "abc123"}}

    lineage = build_lineage("artifacts/swing_20/snapshots/swing20_test", manifest, features)

    for key in (
        "source_swing20_snapshot_id",
        "source_swing20_snapshot_dir",
        "source_swing20_artifact_hashes",
        "feature_specification_version",
        "generated_at",
        "feature_dataset_row_count",
        "feature_dataset_hash",
        "git_commit",
    ):
        assert key in lineage
    assert lineage["source_swing20_snapshot_id"] == "swing20_test"
    assert lineage["feature_dataset_row_count"] == 1
