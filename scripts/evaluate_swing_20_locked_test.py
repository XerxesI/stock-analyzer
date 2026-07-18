"""SWING_20 Model 2 Locked Test evaluation -- one-shot, per the pre-registration in
docs/09_experiments/EXP-003_SWING20_Locked_Test.md.

Reuses the frozen functions from train_swing_20_logistic_baseline.py UNCHANGED:
fit_on_train, make_design_matrix, train_logistic, daily_rank_metrics,
context_only_logit, compute_logit, precision_lift_at_k_global, temporal_blocks,
brier_and_calibration, check_not_estimable_interactions. This script adds no new
model logic -- it only assembles the pre-registered report structure and applies the
pre-registered decision rule to the (unmodified) Model 2 fit on train only.

Run exactly once, after EXP-003 Part 1 is committed. Does not retrain, revise, or
promote the model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.train_swing_20_logistic_baseline import (
    TARGET,
    brier_and_calibration,
    check_not_estimable_interactions,
    compute_logit,
    context_only_logit,
    daily_rank_metrics,
    fit_on_train,
    make_design_matrix,
    precision_lift_at_k_global,
    temporal_blocks,
    train_logistic,
)

# Pre-registered PASS thresholds (EXP-003 Part 1, section 5).
PASS_TOP10_SYMBOL_MEAN_LIFT = 1.50
PASS_TOP10_SYMBOL_CI_LOWER_BOUND = 1.00
PASS_TOP10_FRACTION_WITH_POSITIVE = 0.70
PASS_TOP10_PCT_MEAN_LIFT = 1.20


def ticker_concentration(df: pd.DataFrame, probs: np.ndarray, top_n: int = 10) -> dict[str, object]:
    work = pd.DataFrame({"date": df["date"].to_numpy(), "symbol": df["symbol"].to_numpy(), TARGET: df[TARGET].to_numpy(), "prob": probs})
    picks = []
    for _, group in work.groupby("date"):
        picks.append(group.sort_values("prob", ascending=False).head(top_n))
    picked = pd.concat(picks, ignore_index=True)

    pick_counts = picked["symbol"].value_counts()
    positive_counts = picked.loc[picked[TARGET].astype(bool), "symbol"].value_counts()
    total_slots = len(picked)
    total_positives = int(picked[TARGET].sum())

    top_symbols = pick_counts.head(10)
    return {
        "distinct_symbols_selected": int(pick_counts.shape[0]),
        "total_top_n_slots": int(total_slots),
        "total_positive_outcomes_in_selection": total_positives,
        "most_selected_symbols": {
            str(sym): {
                "times_selected": int(count),
                "share_of_all_slots": float(count / total_slots),
                "positive_outcomes": int(positive_counts.get(sym, 0)),
            }
            for sym, count in top_symbols.items()
        },
        "top_10_symbols_share_of_all_slots": float(top_symbols.sum() / total_slots) if total_slots else None,
    }


def stratified_breakdown(df: pd.DataFrame, probs: np.ndarray, strat_col: str) -> dict[str, object]:
    # Positional boolean masks (not index-label lookups) -- probs is a plain numpy
    # array built in df's row order, so this avoids any index-alignment ambiguity.
    strat_values = df[strat_col].astype(str).to_numpy()
    result = {}
    for level in np.unique(strat_values):
        mask = strat_values == level
        group = df.loc[mask]
        group_probs = probs[mask]
        daily = daily_rank_metrics(group, group_probs, k_fracs=(0.10,), fixed_ns=(10,))
        result[str(level)] = {"n": int(mask.sum()), "pct_10": daily.get("pct_10"), "top_10": daily.get("top_10")}
    return result


def mfe_mae_diagnostic(df: pd.DataFrame, probs: np.ndarray, top_n: int = 10) -> dict[str, object]:
    if "mfe_20d" not in df.columns or "mae_20d" not in df.columns:
        return {"available": False}
    work = pd.DataFrame(
        {
            "date": df["date"].to_numpy(),
            "prob": probs,
            "mfe_20d": df["mfe_20d"].to_numpy(),
            "mae_20d": df["mae_20d"].to_numpy(),
        }
    )
    picks = []
    for _, group in work.groupby("date"):
        picks.append(group.sort_values("prob", ascending=False).head(top_n))
    picked = pd.concat(picks, ignore_index=True)
    return {
        "available": True,
        "top_10_mean_mfe_20d": float(picked["mfe_20d"].mean()),
        "top_10_mean_mae_20d": float(picked["mae_20d"].mean()),
        "population_mean_mfe_20d": float(work["mfe_20d"].mean()),
        "population_mean_mae_20d": float(work["mae_20d"].mean()),
    }


def apply_decision_rule(daily: dict[str, object], subperiod_ok: bool, concentration_ok: bool) -> dict[str, object]:
    top10sym = daily["top_10"]
    top10pct = daily["pct_10"]

    c1 = top10sym["mean_daily_lift"] is not None and top10sym["mean_daily_lift"] >= PASS_TOP10_SYMBOL_MEAN_LIFT
    ci = top10sym["lift_block_bootstrap_ci95"]
    c2 = ci is not None and ci["low"] > PASS_TOP10_SYMBOL_CI_LOWER_BOUND
    c3 = (
        top10sym["fraction_dates_with_at_least_one_positive"] is not None
        and top10sym["fraction_dates_with_at_least_one_positive"] >= PASS_TOP10_FRACTION_WITH_POSITIVE
    )
    c4 = top10pct["mean_daily_lift"] is not None and top10pct["mean_daily_lift"] >= PASS_TOP10_PCT_MEAN_LIFT
    c5 = bool(subperiod_ok and concentration_ok)

    checks = {
        "1_top10_symbol_mean_lift_gte_1_50": {"value": top10sym["mean_daily_lift"], "pass": c1},
        "2_top10_symbol_ci_lower_bound_gt_1_00": {"value": ci["low"] if ci else None, "pass": c2},
        "3_fraction_dates_with_positive_gte_0_70": {"value": top10sym["fraction_dates_with_at_least_one_positive"], "pass": c3},
        "4_top10pct_mean_lift_gte_1_20": {"value": top10pct["mean_daily_lift"], "pass": c4},
        "5_not_dominated_by_subperiod_or_concentration": {"pass": c5},
    }

    all_pass = all(v["pass"] for v in checks.values())
    n_secondary_fail = sum(not v["pass"] for k, v in checks.items() if k.startswith(("3_", "4_", "5_")))
    core_pass = c1 and c2

    if all_pass:
        verdict = "PASS"
    elif core_pass and n_secondary_fail == 1:
        verdict = "CONDITIONAL_PASS"
    else:
        verdict = "FAIL"

    return {"checks": checks, "verdict": verdict}


def main() -> None:
    parser = argparse.ArgumentParser(description="SWING_20 Model 2 Locked Test evaluation (one-shot).")
    parser.add_argument("--train-features-path", required=True)
    parser.add_argument("--locked-test-features-path", required=True)
    parser.add_argument("--output-json", default="artifacts/swing_20_locked_test_report.json")
    args = parser.parse_args()

    train_df = pd.read_parquet(args.train_features_path)
    train_df = train_df[train_df["split"] == "train"].copy()
    locked_df = pd.read_parquet(args.locked_test_features_path)
    print(f"[locked_test-eval] train: {len(train_df)} rows, {train_df['date'].nunique()} dates", flush=True)
    print(f"[locked_test-eval] locked_test: {len(locked_df)} rows, {locked_df['date'].nunique()} dates, {locked_df['symbol'].nunique()} symbols", flush=True)

    not_estimable = check_not_estimable_interactions(pd.concat([train_df, locked_df], ignore_index=True))
    print(f"[locked_test-eval] not-estimable interactions: {not_estimable['not_estimable_columns']}", flush=True)

    fit = fit_on_train(train_df)
    X_train = make_design_matrix(train_df, fit, "model2")
    y_train = train_df[TARGET].to_numpy().astype(float)
    print("[locked_test-eval] refitting frozen Model 2 on train only (deterministic)...", flush=True)
    model = train_logistic(X_train, y_train)

    X_locked = make_design_matrix(locked_df, fit, "model2")
    y_locked = locked_df[TARGET].to_numpy().astype(float)
    probs_locked = model.predict_proba(X_locked.to_numpy())[:, 1]
    full_logit = compute_logit(model, X_locked)
    ctx_logit = context_only_logit(model, X_locked)
    stock_logit = full_logit - ctx_logit

    print("[locked_test-eval] computing daily cross-sectional metrics...", flush=True)
    daily = daily_rank_metrics(locked_df, probs_locked)

    print("[locked_test-eval] computing diagnostics...", flush=True)
    blocks = temporal_blocks(locked_df["date"])
    subperiod_results = {}
    for i, block_dates in enumerate(blocks):
        mask = locked_df["date"].isin(block_dates).to_numpy()
        block_df = locked_df.loc[mask]
        block_probs = probs_locked[mask]
        block_daily = daily_rank_metrics(block_df, block_probs, k_fracs=(0.10,), fixed_ns=(10,))
        subperiod_results[f"block_{i}"] = {
            "n_dates": int(len(block_dates)),
            "date_range": {"start": str(min(block_dates)), "end": str(max(block_dates))},
            "pct_10": block_daily.get("pct_10"),
            "top_10": block_daily.get("top_10"),
        }

    concentration = ticker_concentration(locked_df, probs_locked, top_n=10)

    # Subperiod check: no single block should have zero positives among its own top-10
    # picks while others carry the whole result, and each block's own top-10 mean lift
    # should exceed 1.0 (directionally consistent with the overall result).
    subperiod_ok = all(
        (b["top_10"] is not None and b["top_10"]["mean_daily_lift"] is not None and b["top_10"]["mean_daily_lift"] > 1.0)
        for b in subperiod_results.values()
    )
    # Concentration check: the top-10 most-frequently-picked symbols should not account
    # for a dominant share of all top-10 slots over the whole period.
    concentration_ok = concentration["top_10_symbols_share_of_all_slots"] is not None and concentration["top_10_symbols_share_of_all_slots"] < 0.5

    decision = apply_decision_rule(daily, subperiod_ok, concentration_ok)

    report = {
        "locked_test_read_once": True,
        "sample_size": int(len(locked_df)),
        "symbol_count": int(locked_df["symbol"].nunique()),
        "n_dates": int(locked_df["date"].nunique()),
        "date_range": {"start": str(locked_df["date"].min().date()), "end": str(locked_df["date"].max().date())},
        "not_estimable_interactions": not_estimable,
        "feature_names": list(X_train.columns),
        "primary_daily_metrics": daily,
        "global_diagnostic_only": precision_lift_at_k_global(probs_locked, y_locked),
        "context_vs_stock_decomposition_global": {
            "A_full_model": precision_lift_at_k_global(probs_locked, y_locked),
            "C_context_only": precision_lift_at_k_global(ctx_logit, y_locked),
            "D_stock_only": precision_lift_at_k_global(stock_logit, y_locked),
        },
        "context_vs_stock_decomposition_daily": {
            "B_full_model_daily": daily_rank_metrics(locked_df, probs_locked, k_fracs=(0.10,), fixed_ns=(10,)),
            "C_context_only_daily": daily_rank_metrics(locked_df, ctx_logit, k_fracs=(0.10,), fixed_ns=(10,)),
            "D_stock_only_daily": daily_rank_metrics(locked_df, stock_logit, k_fracs=(0.10,), fixed_ns=(10,)),
        },
        "subperiod_results": subperiod_results,
        "ticker_concentration": concentration,
        "adv_quintile_breakdown": None,
        "regime_breakdown": stratified_breakdown(
            locked_df.assign(_regime=locked_df["spy_trend"].astype(str) + "_" + locked_df["spy_volatility_bucket"].astype(str)),
            probs_locked,
            "_regime",
        ),
        "calibration": brier_and_calibration(probs_locked, y_locked),
        "mfe_mae_diagnostic": mfe_mae_diagnostic(locked_df, probs_locked),
        "decision": decision,
    }

    from scripts.analyze_swing_20_context_target_mechanics import _apply_quantile_bucket, _log_adv

    adv_quintile = _apply_quantile_bucket(_log_adv(locked_df), fit["adv_edges"], fit["adv_labels"])
    report["adv_quintile_breakdown"] = stratified_breakdown(locked_df.assign(_adv_quintile=adv_quintile), probs_locked, "_adv_quintile")

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"[locked_test-eval] DECISION: {decision['verdict']}", flush=True)
    print(f"[locked_test-eval] wrote {output_path}", flush=True)


if __name__ == "__main__":
    main()
