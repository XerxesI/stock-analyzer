"""SWING_20 auditable Logistic Regression baseline (pre-declared Model 0/1/2) -- v2.

Per the Research Registry decisions in docs/09_experiments/EXP-001 (MF1/VC3
REJECTED_FOR_SWING_20; H1 rsi_14 REFRAMED as regime/size-conditional; H2 rvol_20
CONTINUE as a stable U-shape; H3 the elevated Bear hit-rate REFRAMED as a
spy_trend x spy_volatility_bucket interaction), this script fits three pre-declared
models and evaluates them the way SWING_20 is actually used: as a DAILY
cross-sectional ranking system, not a system that ranks all validation rows across
all dates as one pool.

v1 of this script (see docs/09_experiments/EXP-001) computed precision@k and lift@k by
ranking every validation row globally across the whole period. That measures how well
the model picks good DATES (market timing) as much as how well it picks good STOCKS
within a date, and SWING_20's real usage is "rank today's eligible stocks, pick the
top N" -- a fundamentally different question. v2, corrected per review, adds:

    - Daily cross-sectional precision@k / lift@k (the PRIMARY MVP metric), aggregated
      across dates with mean/median, a date-block bootstrap CI, and the fraction of
      dates with lift > 1.
    - A context-timing vs. stock-selection decomposition: the fitted logit is split
      into a date-constant "context" part (spy_trend/spy_volatility_bucket terms
      only) and a residual "stock" part (everything that varies within a date, incl.
      log_adv20, rvol_20 deciles, rsi_14 and its interactions). Global ranking by each
      part shows how much of the original global lift was market-timing versus
      genuine within-day stock selection.
    - Chronological, pre-declared (not performance-chosen) validation date blocks, to
      check whether the daily metrics are stable over time.
    - Date-level (not row-level) resampling for coefficient stability, since same-date
      stock observations are not independent (the same ADR-005 concern the Fama-MacBeth
      IC work in EXP-001 was built around).
    - Calibration diagnostics (Brier score, calibration intercept/slope, expected
      calibration error) -- descriptive only; no calibrator is fit or applied to
      reshape predictions in this script.
    - is_bear_x_vol_low is structurally absent (zero rows in both splits, since Bear
      periods never have Low SPY volatility in this dataset) and is removed from the
      fitted feature matrix rather than reported as a falsely "stable" zero
      coefficient.

All preprocessing (standardization, ADV quintile edges, RVOL decile edges) is fit on
the train split ONLY, reusing the same train-only-fit helpers as the Context and
Target Mechanics analysis. Locked_test is never read. This script stops after writing
the validation report -- it does not promote a model, open locked_test, or search
thresholds/features post-hoc.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_swing_20_context_target_mechanics import _apply_quantile_bucket, _fit_quantile_edges, _log_adv

TARGET = "target_20pct_20d"
PCT_K_FRACS = (0.01, 0.05, 0.10)
FIXED_N_K = (5, 10, 20)
# Reference decile for the rvol_20 dummy encoding: near the minimum of the U-shape
# found in H2 (docs/09_experiments/EXP-001).
RVOL_REFERENCE_DECILE = "d5"
# Date-constant terms only (spy_trend / spy_volatility_bucket derived) -- everything
# else in the design matrix varies within a date and belongs to "stock selection".
# is_bear_x_vol_low is intentionally absent: see check_not_estimable_interactions().
CONTEXT_COLUMNS = ["is_bear", "is_vol_low", "is_vol_high", "is_bear_x_vol_high"]
N_TEMPORAL_BLOCKS = 4
DATE_RESAMPLES = 20
DATE_RESAMPLE_FRAC = 0.7
BLOCK_BOOTSTRAP_BLOCK_LEN = 20
BLOCK_BOOTSTRAP_ITERS = 1000


def fit_on_train(train_df: pd.DataFrame) -> dict[str, object]:
    """All standardization parameters and bucket edges, fit on train only."""

    log_adv_train = _log_adv(train_df)
    rsi_train = train_df["rsi_14"]

    return {
        "log_adv_mean": float(log_adv_train.mean()),
        "log_adv_std": float(log_adv_train.std()),
        "rsi_mean": float(rsi_train.mean()),
        "rsi_std": float(rsi_train.std()),
        "adv_edges": _fit_quantile_edges(log_adv_train, 5),
        "adv_labels": [f"adv_q{i}" for i in range(1, 6)],
        "rvol_edges": _fit_quantile_edges(train_df["rvol_20"], 10),
        "rvol_labels": [f"d{i}" for i in range(1, 11)],
        "train_base_rate": float(train_df[TARGET].mean()),
    }


def check_not_estimable_interactions(df: pd.DataFrame) -> dict[str, object]:
    """Detect structurally-zero regime combinations so they can be excluded from the
    fitted feature matrix instead of reporting a false sign-consistency for an
    unidentifiable all-zero column."""

    ct = pd.crosstab(df["spy_trend"], df["spy_volatility_bucket"])
    bear_low = int(ct.loc["Bear", "Low"]) if "Bear" in ct.index and "Low" in ct.columns else 0
    not_estimable = ["is_bear_x_vol_low"] if bear_low == 0 else []
    return {
        "regime_cell_counts": {f"{trend}_{vol}": int(ct.loc[trend, vol]) for trend in ct.index for vol in ct.columns},
        "not_estimable_columns": not_estimable,
    }


def make_design_matrix(df: pd.DataFrame, fit: dict[str, object], model: str) -> pd.DataFrame:
    log_adv_z = (_log_adv(df) - fit["log_adv_mean"]) / fit["log_adv_std"]
    is_bear = (df["spy_trend"] == "Bear").astype(float)
    is_vol_low = (df["spy_volatility_bucket"] == "Low").astype(float)
    is_vol_high = (df["spy_volatility_bucket"] == "High").astype(float)

    # is_bear_x_vol_low is deliberately not built: Bear x Low volatility has zero rows
    # in this dataset (see check_not_estimable_interactions), so the column would be
    # an unidentifiable constant zero.
    model1 = pd.DataFrame(
        {
            "log_adv20_z": log_adv_z.to_numpy(),
            "is_bear": is_bear.to_numpy(),
            "is_vol_low": is_vol_low.to_numpy(),
            "is_vol_high": is_vol_high.to_numpy(),
            "is_bear_x_vol_high": (is_bear * is_vol_high).to_numpy(),
        },
        index=df.index,
    )
    if model == "model1":
        return model1

    adv_quintile = _apply_quantile_bucket(_log_adv(df), fit["adv_edges"], fit["adv_labels"])
    is_low_adv = (adv_quintile == "adv_q1").astype(float)

    rvol_decile = _apply_quantile_bucket(df["rvol_20"], fit["rvol_edges"], fit["rvol_labels"])
    rvol_dummy_columns = [f"rvol_{label}" for label in fit["rvol_labels"] if label != RVOL_REFERENCE_DECILE]
    rvol_dummies = pd.get_dummies(rvol_decile, prefix="rvol", dtype=float)
    rvol_dummies = rvol_dummies.reindex(columns=rvol_dummy_columns, fill_value=0.0)
    rvol_dummies.index = df.index

    rsi_z = (df["rsi_14"] - fit["rsi_mean"]) / fit["rsi_std"]

    model2 = pd.concat([model1, rvol_dummies], axis=1)
    model2["rsi_14_z"] = rsi_z.to_numpy()
    model2["rsi_14_z_x_bear"] = (rsi_z * is_bear).to_numpy()
    model2["rsi_14_z_x_low_adv"] = (rsi_z * is_low_adv).to_numpy()
    return model2


def train_logistic(X: pd.DataFrame, y: np.ndarray) -> LogisticRegression:
    # L2 penalty (sklearn's default) at C=1.0 -- a fixed, undramatic regularization
    # strength for an audit baseline, not tuned via search.
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=2000)
    model.fit(X.to_numpy(), y)
    return model


def resample_by_date(df: pd.DataFrame, frac: float, rng: np.random.Generator) -> pd.DataFrame:
    """Sample whole dates (all of that date's rows), not individual rows -- same-date
    stock observations are not independent (ADR-005)."""

    dates = df["date"].unique()
    n_pick = max(1, int(round(len(dates) * frac)))
    picked = rng.choice(dates, size=n_pick, replace=False)
    return df[df["date"].isin(picked)]


def coefficient_stability_by_date(
    train_df: pd.DataFrame,
    fit: dict[str, object],
    model_name: str,
    n_resamples: int = DATE_RESAMPLES,
    frac: float = DATE_RESAMPLE_FRAC,
    seed: int = 13,
) -> tuple[LogisticRegression, pd.DataFrame, dict[str, object]]:
    X_full = make_design_matrix(train_df, fit, model_name)
    y_full = train_df[TARGET].to_numpy().astype(float)
    full_model = train_logistic(X_full, y_full)
    full_coef = full_model.coef_.ravel()

    rng = np.random.default_rng(seed)
    resample_coefs = []
    for _ in range(n_resamples):
        sub_df = resample_by_date(train_df, frac, rng)
        X_sub = make_design_matrix(sub_df, fit, model_name)
        y_sub = sub_df[TARGET].to_numpy().astype(float)
        resample_model = train_logistic(X_sub, y_sub)
        resample_coefs.append(resample_model.coef_.ravel())
    resample_coefs = np.array(resample_coefs)

    stability: dict[str, object] = {
        "resampling_unit": "date (whole trading days, not individual rows)",
        "n_resamples": n_resamples,
        "resample_date_fraction": frac,
    }
    for i, name in enumerate(X_full.columns):
        sign_consistency = float(np.mean(np.sign(resample_coefs[:, i]) == np.sign(full_coef[i])))
        stability[name] = {
            "full_train_coef": float(full_coef[i]),
            "resample_median_coef": float(np.median(resample_coefs[:, i])),
            "resample_p05_coef": float(np.percentile(resample_coefs[:, i], 5)),
            "resample_p95_coef": float(np.percentile(resample_coefs[:, i], 95)),
            "sign_consistency_fraction": sign_consistency,
        }
    stability["intercept"] = {"full_train_value": float(full_model.intercept_[0])}
    stability["is_bear_x_vol_low"] = {
        "status": "NOT_ESTIMABLE",
        "reason": "Bear x Low volatility has zero rows in train and validation; excluded from the fitted feature matrix rather than reported as a stable zero coefficient.",
    }
    return full_model, X_full, stability


def compute_logit(model: LogisticRegression, X: pd.DataFrame) -> np.ndarray:
    return X.to_numpy() @ model.coef_.ravel() + model.intercept_[0]


def context_only_logit(model: LogisticRegression, X: pd.DataFrame) -> np.ndarray:
    """The date-constant part of the fitted logit -- every stock on the same date gets
    the same value, so ranking by this within a date is a pure tie (no discrimination
    by construction)."""

    coef_map = dict(zip(X.columns, model.coef_.ravel()))
    score = np.full(len(X), float(model.intercept_[0]))
    for col in CONTEXT_COLUMNS:
        if col in X.columns:
            score = score + X[col].to_numpy() * coef_map[col]
    return score


def precision_lift_at_k_global(scores: np.ndarray, y: np.ndarray, k_fracs: tuple[float, ...] = PCT_K_FRACS) -> dict[str, object]:
    n = len(scores)
    base_rate = float(y.mean())
    order = np.argsort(-scores)
    result = {}
    for k in k_fracs:
        top_n = max(1, int(round(n * k)))
        idx = order[:top_n]
        precision = float(y[idx].mean())
        result[f"top_{k * 100:.0f}pct"] = {
            "n": int(top_n),
            "precision": precision,
            "lift": (precision / base_rate) if base_rate > 0 else None,
        }
    return result


def block_bootstrap_ci(values: np.ndarray, block_len: int = BLOCK_BOOTSTRAP_BLOCK_LEN, iters: int = BLOCK_BOOTSTRAP_ITERS, seed: int = 7) -> dict[str, float] | None:
    """Moving-block bootstrap 95% CI on the mean, reusing ADR-005's block-bootstrap
    rationale for date-to-date serial dependence (values here are already one
    observation per date via daily_rank_metrics, but adjacent dates can still be
    correlated through overlapping 20-day forward windows)."""

    n = len(values)
    if n < 2:
        return None
    eff_block_len = min(block_len, max(1, n // 2))
    rng = np.random.default_rng(seed)
    n_blocks_needed = int(np.ceil(n / eff_block_len))
    means = []
    for _ in range(iters):
        blocks = [values[start : start + eff_block_len] for start in rng.integers(0, n - eff_block_len + 1, size=n_blocks_needed)]
        sample = np.concatenate(blocks)[:n]
        means.append(sample.mean())
    means = np.array(means)
    return {"low": float(np.percentile(means, 2.5)), "high": float(np.percentile(means, 97.5))}


def daily_rank_metrics(
    df: pd.DataFrame,
    scores: np.ndarray,
    target: str = TARGET,
    k_fracs: tuple[float, ...] = PCT_K_FRACS,
    fixed_ns: tuple[int, ...] = FIXED_N_K,
) -> dict[str, object]:
    """Per-date ranking metrics: rank each date's own rows independently, precision/
    lift against that date's own base rate. A score with (near) zero variance within a
    date (e.g. a context-only score, which is identical for every stock on that date)
    is handled analytically -- its precision is set to that date's own base rate,
    rather than trusting whatever arbitrary order a stable sort gives to tied values
    (which could otherwise introduce a hidden bias, e.g. always favoring
    alphabetically-earlier symbols)."""

    work = pd.DataFrame({"date": df["date"].to_numpy(), target: df[target].to_numpy(), "score": scores})
    k_keys = [f"pct_{k * 100:.0f}" for k in k_fracs]
    n_keys = [f"top_{n}" for n in fixed_ns]
    records: dict[str, list[dict[str, object]]] = {key: [] for key in k_keys + n_keys}

    for date, group in work.groupby("date", sort=True):
        n = len(group)
        base_rate = float(group[target].mean())
        is_degenerate = bool(np.isclose(group["score"].std(ddof=0), 0.0))
        sorted_group = group.sort_values("score", ascending=False)

        def _record(top_n: int) -> dict[str, object]:
            if is_degenerate:
                precision = base_rate
                has_positive = None
            else:
                selected = sorted_group[target].iloc[:top_n]
                precision = float(selected.mean())
                has_positive = bool(selected.sum() > 0)
            return {
                "n_eligible": n,
                "top_n": top_n,
                "base_rate": base_rate,
                "precision": precision,
                "lift": (precision / base_rate) if base_rate > 0 else None,
                "has_positive": has_positive,
            }

        for k, key in zip(k_fracs, k_keys):
            top_n = max(1, int(round(n * k)))
            records[key].append(_record(top_n))
        for fn, key in zip(fixed_ns, n_keys):
            if n < fn:
                continue
            records[key].append(_record(fn))

    summary = {}
    for key, rows in records.items():
        if not rows:
            summary[key] = None
            continue
        precisions = np.array([r["precision"] for r in rows])
        lifts = np.array([r["lift"] for r in rows if r["lift"] is not None])
        has_pos = [r["has_positive"] for r in rows if r["has_positive"] is not None]
        top_ns = [r["top_n"] for r in rows]
        summary[key] = {
            "n_dates": len(rows),
            "mean_daily_precision": float(precisions.mean()),
            "median_daily_precision": float(np.median(precisions)),
            "mean_daily_lift": float(lifts.mean()) if len(lifts) else None,
            "median_daily_lift": float(np.median(lifts)) if len(lifts) else None,
            "lift_se": float(lifts.std(ddof=1) / np.sqrt(len(lifts))) if len(lifts) > 1 else None,
            "lift_block_bootstrap_ci95": block_bootstrap_ci(lifts) if len(lifts) > 1 else None,
            "fraction_dates_lift_gt_1": float(np.mean(lifts > 1)) if len(lifts) else None,
            "fraction_dates_with_at_least_one_positive": float(np.mean(has_pos)) if has_pos else None,
            "candidate_count_distribution": {"min": int(min(top_ns)), "median": float(np.median(top_ns)), "max": int(max(top_ns))},
        }
    return summary


def temporal_blocks(dates: pd.Series, n_blocks: int = N_TEMPORAL_BLOCKS) -> list[np.ndarray]:
    """Pre-declared equal-day-count chronological blocks, defined purely from the date
    sequence -- never from performance."""

    unique_dates = np.array(sorted(dates.unique()))
    return list(np.array_split(unique_dates, n_blocks))


def brier_and_calibration(probs: np.ndarray, y: np.ndarray) -> dict[str, object]:
    """Descriptive-only calibration diagnostics on already-fixed predictions. No
    calibrator is fit here to reshape predictions -- calibration_intercept/slope are a
    diagnostic regression of y on logit(p), not a correction applied going forward."""

    eps = 1e-6
    clipped = np.clip(probs, eps, 1 - eps)
    logit = np.log(clipped / (1 - clipped))

    if len(np.unique(y)) > 1:
        cal_model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
        cal_model.fit(logit.reshape(-1, 1), y)
        slope = float(cal_model.coef_.ravel()[0])
        intercept = float(cal_model.intercept_[0])
    else:
        slope = None
        intercept = None

    frame = pd.DataFrame({"prob": probs, "y": y})
    frame["bucket"] = pd.qcut(frame["prob"], 10, duplicates="drop")
    ece = 0.0
    for _, group in frame.groupby("bucket", observed=True):
        ece += (len(group) / len(frame)) * abs(group["prob"].mean() - group["y"].mean())

    return {
        "n": int(len(y)),
        "brier_score": float(np.mean((probs - y) ** 2)),
        "calibration_intercept_diagnostic_only": intercept,
        "calibration_slope_diagnostic_only": slope,
        "expected_calibration_error": float(ece),
        "note": "Descriptive diagnostics of already-fixed predictions on this split only; no calibrator is fit or applied.",
    }


def evaluate_model(model: LogisticRegression, X: pd.DataFrame, df: pd.DataFrame) -> dict[str, object]:
    y = df[TARGET].to_numpy().astype(float)
    probs = model.predict_proba(X.to_numpy())[:, 1]
    full_logit = compute_logit(model, X)
    ctx_logit = context_only_logit(model, X)
    stock_logit = full_logit - ctx_logit

    result: dict[str, object] = {
        "roc_auc_diagnostic_only": float(roc_auc_score(y, probs)) if len(np.unique(y)) > 1 else None,
        "pr_auc_diagnostic_only": float(average_precision_score(y, probs)) if len(np.unique(y)) > 1 else None,
        "A_full_model_global_DIAGNOSTIC_ONLY": precision_lift_at_k_global(probs, y),
        "B_full_model_daily_PRIMARY_MVP_METRIC": daily_rank_metrics(df, probs),
        "C_context_only_global": precision_lift_at_k_global(ctx_logit, y),
        "C_context_only_daily": daily_rank_metrics(df, ctx_logit),
        "D_stock_only_global": precision_lift_at_k_global(stock_logit, y),
        "D_stock_only_daily_should_equal_B": daily_rank_metrics(df, stock_logit),
        "calibration_overall": brier_and_calibration(probs, y),
        "calibration_by_regime": {},
    }
    # Positional boolean masks (not index-label lookups) -- probs/y are plain numpy
    # arrays built in df's row order, so this avoids any index-alignment ambiguity.
    regime_series = (df["spy_trend"].astype(str) + "_" + df["spy_volatility_bucket"].astype(str)).to_numpy()
    for regime in np.unique(regime_series):
        mask = regime_series == regime
        if mask.sum() < 30:
            continue
        result["calibration_by_regime"][str(regime)] = brier_and_calibration(probs[mask], y[mask])
    return result


def temporal_robustness(model: LogisticRegression, X: pd.DataFrame, df: pd.DataFrame) -> dict[str, object]:
    y_all = df[TARGET].to_numpy().astype(float)
    probs_all = model.predict_proba(X.to_numpy())[:, 1]
    blocks = temporal_blocks(df["date"])
    result = {}
    for i, block_dates in enumerate(blocks):
        mask = df["date"].isin(block_dates).to_numpy()
        if mask.sum() == 0:
            continue
        block_df = df.loc[mask]
        block_probs = probs_all[mask]
        block_y = y_all[mask]
        daily = daily_rank_metrics(block_df, block_probs)
        result[f"block_{i}"] = {
            "n_dates": int(len(block_dates)),
            "date_range": {"start": str(min(block_dates)), "end": str(max(block_dates))},
            "precision_at_10pct": daily.get("pct_10"),
            "precision_at_top_10_symbols": daily.get("top_10"),
            "calibration": brier_and_calibration(block_probs, block_y),
        }
    return result


def evaluate_model0(train_df: pd.DataFrame, validation_df: pd.DataFrame, train_base_rate: float) -> dict[str, object]:
    """Intercept-only model: constant prediction = train base rate for every row. Daily
    ranking is degenerate (all predictions tied within every date), so daily
    precision@k equals each date's own base rate and mean_daily_lift = 1.0 by
    construction -- this is the null-model reference point for the daily metric."""

    def _eval(df: pd.DataFrame) -> dict[str, object]:
        constant_scores = np.full(len(df), train_base_rate)
        return {
            "base_rate": float(df[TARGET].mean()),
            "predicted_probability": train_base_rate,
            "B_full_model_daily_PRIMARY_MVP_METRIC": daily_rank_metrics(df, constant_scores),
            "note": "No ranking ability by construction; daily precision@k equals each date's own base rate, mean_daily_lift = 1.0.",
        }

    return {"train": _eval(train_df), "validation": _eval(validation_df)}


def main() -> None:
    parser = argparse.ArgumentParser(description="SWING_20 auditable Logistic Regression baseline (Model 0/1/2), v2.")
    parser.add_argument("--features-path", required=True)
    parser.add_argument("--output-json", default="artifacts/swing_20_logistic_baseline_report.json")
    args = parser.parse_args()

    df = pd.read_parquet(args.features_path)
    print(f"[baseline] loaded {len(df)} rows, {df['symbol'].nunique()} symbols", flush=True)

    train_df = df[df["split"] == "train"].copy()
    validation_df = df[df["split"] == "validation"].copy()

    fit = fit_on_train(train_df)
    not_estimable = check_not_estimable_interactions(pd.concat([train_df, validation_df]))
    print(f"[baseline] not-estimable interactions: {not_estimable['not_estimable_columns']}", flush=True)

    report: dict[str, object] = {
        "sample_size": int(len(df)),
        "split_counts": {"train": int(len(train_df)), "validation": int(len(validation_df))},
        "n_dates": {"train": int(train_df["date"].nunique()), "validation": int(validation_df["date"].nunique())},
        "preprocessing_fit_policy": (
            "All standardization means/stds and quantile bucket edges (adv20 quintiles, rvol_20 deciles) "
            "are fit on the train split only. Validation never contributes to a fitted parameter."
        ),
        "not_estimable_interactions": not_estimable,
        "evaluation_note": (
            "Primary MVP metric is B (daily cross-sectional ranking: each date's eligible "
            "symbols ranked independently, precision/lift against that date's own base "
            "rate). A (global ranking across all validation rows pooled) is retained only "
            "as a diagnostic -- it conflates date selection with stock selection. C "
            "(context-only score) and D (stock-only score, i.e. full logit minus the "
            "date-constant context part) decompose how much of A's apparent lift is "
            "market-timing (C) versus genuine within-day stock selection (D)."
        ),
        "models": {},
    }

    print("[baseline] Model 0 (intercept-only)...", flush=True)
    report["models"]["model_0_intercept_only"] = evaluate_model0(train_df, validation_df, fit["train_base_rate"])

    for model_name, key in (("model1", "model_1_context_control"), ("model2", "model_2_research_features")):
        print(f"[baseline] {model_name}: fitting + date-level coefficient stability...", flush=True)
        fitted_model, X_train, stability = coefficient_stability_by_date(train_df, fit, model_name)
        X_validation = make_design_matrix(validation_df, fit, model_name)

        print(f"[baseline] {model_name}: evaluating (daily + global + decomposition)...", flush=True)
        report["models"][key] = {
            "feature_names": list(X_train.columns),
            "coefficients_and_stability": stability,
            "train": evaluate_model(fitted_model, X_train, train_df),
            "validation": evaluate_model(fitted_model, X_validation, validation_df),
            "validation_temporal_robustness": temporal_robustness(fitted_model, X_validation, validation_df),
        }

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"[baseline] wrote {output_path}", flush=True)


if __name__ == "__main__":
    main()
