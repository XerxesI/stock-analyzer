from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.train_swing_20_logistic_baseline import (
    CONTEXT_COLUMNS,
    TARGET,
    check_not_estimable_interactions,
    compute_logit,
    context_only_logit,
    daily_rank_metrics,
    fit_on_train,
    make_design_matrix,
    resample_by_date,
    train_logistic,
)


def _price_like_frame(n_symbols: int, n_dates: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_dates)
    symbols = [f"SYM{i}" for i in range(n_symbols)]
    rows = []
    for date in dates:
        # spy_trend / spy_volatility_bucket are market-level: the same value for every
        # symbol on a given date, exactly like the real SWING_20 feature dataset. This
        # is what context_only_logit's zero-within-date-variance guarantee relies on.
        date_trend = rng.choice(["Bull", "Bear"])
        date_vol_bucket = rng.choice(["Normal", "High"])
        for symbol in symbols:
            rows.append(
                {
                    "symbol": symbol,
                    "date": date,
                    TARGET: bool(rng.integers(0, 2)),
                    "adv20": rng.uniform(1e6, 5e7),
                    "rvol_20": rng.uniform(0.3, 2.0),
                    "rsi_14": rng.uniform(20, 80),
                    "spy_trend": date_trend,
                    "spy_volatility_bucket": date_vol_bucket,
                    "split": "train",
                }
            )
    return pd.DataFrame(rows)


def test_daily_selection_is_per_date_not_global():
    # Date A: 10 rows, absolute scores 10..1, only the single highest-score row is
    # positive. Date B: 10 rows, absolute scores 0.10..0.01 (every one far below every
    # Date A score), only the single highest-score row in B is positive. If ranking
    # were global, B's positive row (tiny absolute score) would never appear in a
    # global top-1 selection dominated by A's much larger scores.
    dates = ["2024-01-02"] * 10 + ["2024-01-03"] * 10
    scores = list(range(10, 0, -1)) + [x / 100 for x in range(10, 0, -1)]
    targets = [1] + [0] * 9 + [1] + [0] * 9
    df = pd.DataFrame({"date": pd.to_datetime(dates), TARGET: targets})

    result = daily_rank_metrics(df, np.array(scores), fixed_ns=(1,), k_fracs=())

    assert result["top_1"]["n_dates"] == 2
    # Each date's own top-1 pick is its own positive row -> precision 1.0 on both days.
    assert result["top_1"]["mean_daily_precision"] == pytest.approx(1.0)


def test_daily_lift_uses_same_date_base_rate():
    # Date A: 2 rows, 1 positive -> base rate 0.5. Date B: 10 rows, 1 positive -> base
    # rate 0.1. A perfect top-1 pick on both days gives precision 1.0 on each, but the
    # lift (precision / that date's own base rate) must differ: 2.0 vs 10.0 -- proving
    # the denominator is the date's own rate, not a pooled/global rate (which would be
    # 2/12 ~= 0.167 for both days if computed incorrectly).
    dates = ["2024-01-02"] * 2 + ["2024-01-03"] * 10
    scores = [2, 1] + list(range(10, 0, -1))
    targets = [1, 0] + [1] + [0] * 9
    df = pd.DataFrame({"date": pd.to_datetime(dates), TARGET: targets})

    result = daily_rank_metrics(df, np.array(scores), fixed_ns=(1,), k_fracs=())

    assert result["top_1"]["mean_daily_precision"] == pytest.approx(1.0)
    assert result["top_1"]["mean_daily_lift"] == pytest.approx((2.0 + 10.0) / 2)


def test_fit_on_train_uses_only_the_frame_it_is_given():
    train_df = _price_like_frame(n_symbols=5, n_dates=20, seed=1)
    other_df = _price_like_frame(n_symbols=5, n_dates=20, seed=99)  # very different values

    fit_from_train = fit_on_train(train_df)
    manual_log_adv_mean = float(np.log(train_df["adv20"].clip(lower=1)).mean())

    assert fit_from_train["log_adv_mean"] == pytest.approx(manual_log_adv_mean)
    # Fitting on a completely different frame must give different edges -- confirms
    # the function is not silently pulling in some global/shared state.
    fit_from_other = fit_on_train(other_df)
    assert fit_from_train["adv_edges"].tolist() != fit_from_other["adv_edges"].tolist()


def test_make_design_matrix_reuses_train_fit_edges_for_a_different_frame():
    train_df = _price_like_frame(n_symbols=5, n_dates=30, seed=2)
    validation_df = _price_like_frame(n_symbols=5, n_dates=10, seed=3)
    fit = fit_on_train(train_df)

    X_validation = make_design_matrix(validation_df, fit, "model2")

    # The bucket edges used to build validation's design matrix must be exactly the
    # train-fit edges -- not edges recomputed from validation's own distribution.
    rvol_decile_from_manual_fit_edges = pd.cut(
        validation_df["rvol_20"], bins=fit["rvol_edges"], labels=fit["rvol_labels"], include_lowest=True
    )
    rvol_dummy_columns = [c for c in X_validation.columns if c.startswith("rvol_")]
    assert len(rvol_dummy_columns) == 9  # 10 deciles minus 1 reference (d5)
    # A row bucketed into the reference decile should have all rvol dummy columns 0.
    reference_mask = (rvol_decile_from_manual_fit_edges == "d5").to_numpy()
    if reference_mask.any():
        assert (X_validation.loc[reference_mask, rvol_dummy_columns] == 0).all().all()


def test_resample_by_date_never_splits_a_date():
    df = _price_like_frame(n_symbols=8, n_dates=15, seed=4)
    rng = np.random.default_rng(5)

    sub = resample_by_date(df, frac=0.5, rng=rng)

    original_counts = df.groupby("date").size()
    sub_counts = sub.groupby("date").size()
    # Every date present in the resample must retain its FULL original row count --
    # dates are the sampling unit, never partial rows within a date.
    for date, count in sub_counts.items():
        assert count == original_counts[date]
    assert set(sub_counts.index).issubset(set(original_counts.index))


def test_context_only_score_is_constant_within_every_date():
    df = _price_like_frame(n_symbols=6, n_dates=10, seed=6)
    fit = fit_on_train(df)
    X = make_design_matrix(df, fit, "model2")
    model = train_logistic(X, df[TARGET].to_numpy().astype(float))

    ctx_scores = context_only_logit(model, X)
    work = pd.DataFrame({"date": df["date"].to_numpy(), "score": ctx_scores})
    within_date_std = work.groupby("date")["score"].std(ddof=0)

    assert (within_date_std.fillna(0.0) < 1e-9).all()


def test_context_only_daily_lift_is_exactly_one():
    df = _price_like_frame(n_symbols=6, n_dates=10, seed=7)
    fit = fit_on_train(df)
    X = make_design_matrix(df, fit, "model2")
    model = train_logistic(X, df[TARGET].to_numpy().astype(float))

    ctx_scores = context_only_logit(model, X)
    result = daily_rank_metrics(df, ctx_scores, fixed_ns=(), k_fracs=(0.10,))

    # A score that is tied within every date cannot rank stocks within a day -- daily
    # precision falls back analytically to each date's own base rate, so lift is
    # exactly 1.0 with zero spread, by construction.
    assert result["pct_10"]["mean_daily_lift"] == pytest.approx(1.0)
    assert result["pct_10"]["lift_se"] == pytest.approx(0.0)


def test_stock_only_daily_ranking_equals_full_model_daily_ranking():
    df = _price_like_frame(n_symbols=6, n_dates=10, seed=8)
    fit = fit_on_train(df)
    X = make_design_matrix(df, fit, "model2")
    model = train_logistic(X, df[TARGET].to_numpy().astype(float))

    full_logit = compute_logit(model, X)
    ctx_logit = context_only_logit(model, X)
    stock_logit = full_logit - ctx_logit

    daily_full = daily_rank_metrics(df, full_logit, fixed_ns=(), k_fracs=(0.10,))
    daily_stock = daily_rank_metrics(df, stock_logit, fixed_ns=(), k_fracs=(0.10,))

    # Subtracting a date-constant value cannot change within-date rank order, so the
    # two daily evaluations must be identical.
    assert daily_full["pct_10"]["mean_daily_precision"] == pytest.approx(daily_stock["pct_10"]["mean_daily_precision"])
    assert daily_full["pct_10"]["mean_daily_lift"] == pytest.approx(daily_stock["pct_10"]["mean_daily_lift"])


def test_structurally_absent_bear_low_interaction_is_flagged_and_excluded():
    df = _price_like_frame(n_symbols=5, n_dates=20, seed=9)
    # Force the structural absence: no row is ever Bear x Low in this dataset (matches
    # the real SWING_20 data, where Bear periods never have Low SPY volatility).
    df.loc[(df["spy_trend"] == "Bear") & (df["spy_volatility_bucket"] == "Low"), "spy_volatility_bucket"] = "Normal"

    not_estimable = check_not_estimable_interactions(df)
    assert "is_bear_x_vol_low" in not_estimable["not_estimable_columns"]

    fit = fit_on_train(df)
    X1 = make_design_matrix(df, fit, "model1")
    X2 = make_design_matrix(df, fit, "model2")
    assert "is_bear_x_vol_low" not in X1.columns
    assert "is_bear_x_vol_low" not in X2.columns


def test_check_not_estimable_returns_empty_when_combination_present():
    df = _price_like_frame(n_symbols=5, n_dates=20, seed=10)
    # Force at least one Bear x Low row so the combination is NOT structurally absent.
    df.loc[df.index[0], "spy_trend"] = "Bear"
    df.loc[df.index[0], "spy_volatility_bucket"] = "Low"

    not_estimable = check_not_estimable_interactions(df)
    assert "is_bear_x_vol_low" not in not_estimable["not_estimable_columns"]
