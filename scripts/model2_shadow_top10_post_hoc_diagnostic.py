"""Model 2 -- read-only post-hoc "shadow top-10" diagnostic over the validation period.

Reproduces, from frozen artifacts only, the specific figures a research-lead review
asked about for the Model 2 Technical Specification's "Known limitations" section --
independently, not copied from conversation. Three DIFFERENT populations are computed
and kept separate throughout (never blended into one number):

    A. all daily eligible rows (the full validation-split feature population)
    B. daily shadow top-10 rows (top `shadow_top_n` symbols per date by Model 2 score,
       exactly SandboxConfig.shadow_top_n -- the same selection CandidateService makes
       every day, before any capacity/entry/exit policy is applied)
    (108 actually-filled EXP-005 Variant B positions and the EXP-005 portfolio result
    itself are a FOURTH and FIFTH population, already covered by
    scripts/exp005_variant_b_price_path_study.py and the EXP-005 diagnostics report --
    not recomputed here.)

This script does not fit a new model, does not choose a new label, and does not modify
any existing artifact. It reuses the exact frozen functions
(`fit_on_train`/`make_design_matrix`/`train_logistic` from
scripts/train_swing_20_logistic_baseline.py, wrapped identically by
Model2PredictionAdapter) on the SAME frozen feature snapshot the rest of Model 2's
provenance chain uses.

Forward-window convention (empirically verified against the frozen `close_return_20d`/
`mfe_20d`/`mae_20d` columns before trusting it for N=5/10/42, per
docs/02_mvp/SWING20_Dataset_Specification_v1.md Section 4-5): entry_date is trading
session 1 of the horizon (INCLUDED, not excluded) -- an N-session return uses
Close[entry_idx + N - 1]; N-session MFE/MAE use max(High)/min(Low) over
[entry_idx, entry_idx + N) inclusive of entry_date's own bar.

A "score-vs-log(ADV20) removed" score is also computed as an explicit, clearly-labeled
DIAGNOSTIC ONLY re-scoring (never a new model, never promoted, never re-fit): the fitted
`log_adv20_z` coefficient's contribution is subtracted from the full logit before the
sigmoid, and the SAME top-10/42-session metric is recomputed with it, to see whether the
liquidity bias alone explains the shadow top-10's negative forward return.

Overlapping (date, symbol) observations sharing highly-correlated forward windows are
NEVER described as independent trades or independent statistical observations anywhere
in this script's naming or output -- "n" columns always report raw (date, symbol) row
counts, not an implied effective sample size.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from scipy.stats import spearmanr

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.train_swing_20_logistic_baseline import fit_on_train, make_design_matrix, train_logistic
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.infrastructure.model2_prediction_adapter import Model2PredictionAdapter

FEATURES_PATH = "artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet"
PRICES_PATH = "artifacts/swing_20/snapshots/swing20_20260718T135238Z/prices.parquet"
TARGET = "target_20pct_20d"
HORIZONS = (5, 10, 20, 42)
VERIFICATION_HORIZON = 20  # cross-checked against close_return_20d/mfe_20d/mae_20d
FLOAT_TOLERANCE = 1e-6

OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "sandbox" / "model2_diagnostics" / "shadow_top10_post_hoc"
DAILY_TOP10_CSV = OUTPUT_DIR / "shadow_top10_daily_rows.csv"
SUMMARY_JSON = OUTPUT_DIR / "shadow_top10_summary.json"
SUMMARY_MD = OUTPUT_DIR / "shadow_top10_summary.md"


def _sha256_of_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _forward_arrays(symbol_df: pd.DataFrame, n: int) -> dict[str, np.ndarray]:
    """For a symbol's own date-sorted OHLC rows (length M), returns arrays of length M:
    close_fwd[i]=Close[i+n-1], mfe_fwd[i]=max(High[i:i+n]), mae_fwd[i]=min(Low[i:i+n]),
    NaN wherever fewer than n bars remain (entry_date's own bar is session 1 of the
    horizon -- see module docstring)."""

    m = len(symbol_df)
    close = symbol_df["Close"].to_numpy()
    high = symbol_df["High"].to_numpy()
    low = symbol_df["Low"].to_numpy()

    close_fwd = np.full(m, np.nan)
    mfe_fwd = np.full(m, np.nan)
    mae_fwd = np.full(m, np.nan)
    if m >= n:
        close_fwd[: m - n + 1] = close[n - 1 :]
        mfe_fwd[: m - n + 1] = sliding_window_view(high, n).max(axis=1)
        mae_fwd[: m - n + 1] = sliding_window_view(low, n).min(axis=1)
    return {"close_fwd": close_fwd, "mfe_fwd": mfe_fwd, "mae_fwd": mae_fwd}


def build_forward_outcomes(prices_df: pd.DataFrame, symbols: set[str], horizons: tuple[int, ...]) -> pd.DataFrame:
    """One row per (symbol, date) actually present in the frozen prices artifact, with
    forward close/MFE/MAE PRICE levels (not yet returns -- converted to returns per row
    against that row's own entry_price after the merge, since entry_price varies)."""

    rows = []
    for symbol, g in prices_df[prices_df["symbol"].isin(symbols)].groupby("symbol", sort=False):
        # Mirrors _shared.symbol_sessions's own dedup guard -- a duplicate (symbol, date)
        # row would silently shift every positional forward-window lookup after it.
        g = g.sort_values("date").drop_duplicates(subset="date", keep="last").reset_index(drop=True)
        out = {"symbol": symbol, "date": g["date"].to_numpy()}
        for n in horizons:
            arrs = _forward_arrays(g, n)
            out[f"close_fwd_{n}"] = arrs["close_fwd"]
            out[f"mfe_fwd_{n}"] = arrs["mfe_fwd"]
            out[f"mae_fwd_{n}"] = arrs["mae_fwd"]
        rows.append(pd.DataFrame(out))
    return pd.concat(rows, ignore_index=True)


def fama_macbeth_daily_ic(df: pd.DataFrame, date_col: str, x_col: str, y_col: str) -> dict[str, object]:
    """Per-date Spearman rank correlation between x_col and y_col, averaged across
    dates (never pooled across dates) -- the same date-level-first discipline ADR-005
    and EXP-001's Fama-MacBeth IC already use in this codebase."""

    daily_ics = []
    for d, g in df.groupby(date_col):
        sub = g[[x_col, y_col]].dropna()
        if len(sub) < 5 or sub[x_col].nunique() < 2 or sub[y_col].nunique() < 2:
            continue
        rho, _ = spearmanr(sub[x_col], sub[y_col])
        if not np.isnan(rho):
            daily_ics.append(rho)
    if not daily_ics:
        return {"n_dates": 0, "mean_ic": None, "median_ic": None, "fraction_positive": None}
    arr = np.array(daily_ics)
    return {
        "n_dates": len(arr),
        "mean_ic": float(arr.mean()),
        "median_ic": float(np.median(arr)),
        "fraction_positive": float((arr > 0).mean()),
    }


def top10_metric(df: pd.DataFrame, score_col: str, n_top: int, horizon: int) -> dict[str, object]:
    """Daily shadow top-`n_top` by `score_col`, N-session forward close return
    (population B) -- vs. the same horizon's return over ALL eligible rows that date
    (population A). Both reported; never blended."""

    ret_col = f"close_return_fwd_{horizon}"
    all_returns = df[ret_col].dropna()

    top_rows = []
    for d, g in df.groupby("date"):
        top = g.sort_values(score_col, ascending=False).head(n_top)
        top_rows.append(top)
    top_df = pd.concat(top_rows, ignore_index=True)
    top_returns = top_df[ret_col].dropna()

    return {
        "population_A_all_eligible_rows": {
            "n_rows": int(len(all_returns)),
            "mean_return": float(all_returns.mean()) if len(all_returns) else None,
            "median_return": float(all_returns.median()) if len(all_returns) else None,
        },
        "population_B_daily_shadow_top_n": {
            "n_rows": int(len(top_returns)),
            "n_top": n_top,
            "mean_return": float(top_returns.mean()) if len(top_returns) else None,
            "median_return": float(top_returns.median()) if len(top_returns) else None,
        },
    }, top_df


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("[diag] loading frozen feature dataset...")
    features = pd.read_parquet(FEATURES_PATH)
    features["date"] = pd.to_datetime(features["date"])
    features["entry_date"] = pd.to_datetime(features["entry_date"])
    train_df = features[features["split"] == "train"].copy()
    validation_df = features[features["split"] == "validation"].copy().reset_index(drop=True)
    print(f"[diag] validation population: {len(validation_df)} rows, "
          f"{validation_df['date'].nunique()} dates, "
          f"{validation_df['date'].min().date()} .. {validation_df['date'].max().date()}")

    print("[diag] fitting frozen Model 2 (train-only) via the exact training-script functions...")
    fit = fit_on_train(train_df)
    X_train = make_design_matrix(train_df, fit, "model2")
    y_train = train_df[TARGET].to_numpy().astype(float)
    model = train_logistic(X_train, y_train)
    feature_names = list(X_train.columns)

    X_validation = make_design_matrix(validation_df, fit, "model2")
    validation_df["model_score"] = model.predict_proba(X_validation.to_numpy())[:, 1]

    print("[diag] cross-checking against the production Model2PredictionAdapter (bit-for-bit)...")
    adapter = Model2PredictionAdapter(FEATURES_PATH)
    adapter_scores = adapter.score(validation_df).to_numpy()
    adapter_match = bool(np.allclose(adapter_scores, validation_df["model_score"].to_numpy(), atol=1e-12, rtol=0))
    print(f"[diag] adapter score match (bit-for-bit): {adapter_match}")
    if not adapter_match:
        max_diff = float(np.max(np.abs(adapter_scores - validation_df["model_score"].to_numpy())))
        print(f"[diag] WARNING: max abs diff vs adapter = {max_diff}")

    print("[diag] building per-symbol forward-window outcomes from the frozen prices artifact...")
    prices = pd.read_parquet(PRICES_PATH)
    prices["date"] = pd.to_datetime(prices["date"])
    symbols = set(validation_df["symbol"].unique())
    forward = build_forward_outcomes(prices, symbols, HORIZONS)

    merged = validation_df.merge(
        forward, left_on=["symbol", "entry_date"], right_on=["symbol", "date"], how="left", suffixes=("", "_pricebar")
    )
    missing_entry_bar_mask = merged[[f"close_fwd_{n}" for n in HORIZONS]].isna().all(axis=1)
    print(f"[diag] rows with NO matching entry-date price bar at all (excluded from all horizons): "
          f"{int(missing_entry_bar_mask.sum())}")

    for n in HORIZONS:
        merged[f"close_return_fwd_{n}"] = merged[f"close_fwd_{n}"] / merged["entry_price"] - 1.0
        merged[f"mfe_fwd_{n}"] = merged[f"mfe_fwd_{n}"] / merged["entry_price"] - 1.0
        merged[f"mae_fwd_{n}"] = merged[f"mae_fwd_{n}"] / merged["entry_price"] - 1.0
        merged[f"target_hit_fwd_{n}"] = merged[f"mfe_fwd_{n}"] >= 0.20 - 1e-12

    # --- Verification: recomputed 20-session values must match the frozen dataset's own
    # close_return_20d/mfe_20d/mae_20d to float precision, or the whole diagnostic stops
    # here rather than silently reporting numbers built on a wrong windowing convention.
    print("[diag] verifying 20-session construction against frozen close_return_20d/mfe_20d/mae_20d...")
    verify_mask = merged["close_return_20d"].notna() & merged["close_return_fwd_20"].notna()
    close_diff = (merged.loc[verify_mask, "close_return_fwd_20"] - merged.loc[verify_mask, "close_return_20d"]).abs()
    mfe_diff = (merged.loc[verify_mask, "mfe_fwd_20"] - merged.loc[verify_mask, "mfe_20d"]).abs()
    mae_diff = (merged.loc[verify_mask, "mae_fwd_20"] - merged.loc[verify_mask, "mae_20d"]).abs()
    verification = {
        "n_rows_compared": int(verify_mask.sum()),
        "close_return_20d_max_abs_diff": float(close_diff.max()) if len(close_diff) else None,
        "mfe_20d_max_abs_diff": float(mfe_diff.max()) if len(mfe_diff) else None,
        "mae_20d_max_abs_diff": float(mae_diff.max()) if len(mae_diff) else None,
        "tolerance": FLOAT_TOLERANCE,
        "close_return_20d_all_within_tolerance": bool((close_diff <= FLOAT_TOLERANCE).all()) if len(close_diff) else None,
        "mfe_20d_all_within_tolerance": bool((mfe_diff <= FLOAT_TOLERANCE).all()) if len(mfe_diff) else None,
        "mae_20d_all_within_tolerance": bool((mae_diff <= FLOAT_TOLERANCE).all()) if len(mae_diff) else None,
    }
    print(f"[diag] verification: {json.dumps(verification, indent=2)}")
    if not (verification["close_return_20d_all_within_tolerance"] and verification["mfe_20d_all_within_tolerance"]
            and verification["mae_20d_all_within_tolerance"]):
        print("[diag] ABORT: recomputed 20-session values do not match the frozen dataset's own columns -- "
              "the forward-window convention is wrong. Stopping before reporting any downstream metric.")
        raise SystemExit(1)

    shadow_top_n = SandboxConfig().shadow_top_n
    print(f"[diag] shadow_top_n = {shadow_top_n} (from SandboxConfig, not hardcoded)")

    top10_by_horizon = {}
    top10_daily_rows_by_horizon = {}
    for n in HORIZONS:
        metric, top_df = top10_metric(merged, "model_score", shadow_top_n, n)
        top10_by_horizon[str(n)] = metric
        top10_daily_rows_by_horizon[n] = top_df

    # The canonical daily shadow top-10 row set (score-ranked, independent of horizon) --
    # used for the CSV export and the target-hit/MFE/MAE-distribution sections below.
    shadow_rows = []
    for d, g in merged.groupby("date"):
        shadow_rows.append(g.sort_values("model_score", ascending=False).head(shadow_top_n))
    shadow_df = pd.concat(shadow_rows, ignore_index=True)

    target_hit_summary = {
        "population_A_all_eligible": {
            "n": int(merged[TARGET].notna().sum()),
            "target_hit_rate_label": float(merged[TARGET].mean()),
            "target_hit_rate_recomputed_20session": float(merged["target_hit_fwd_20"].mean()),
        },
        "population_B_shadow_top10": {
            "n": int(shadow_df[TARGET].notna().sum()),
            "target_hit_rate_label": float(shadow_df[TARGET].mean()),
            "target_hit_rate_recomputed_20session": float(shadow_df["target_hit_fwd_20"].mean()),
        },
    }

    mfe_mae_20_summary = {
        "population_A_all_eligible": {
            "mean_mfe_20d": float(merged["mfe_20d"].mean()),
            "mean_mae_20d": float(merged["mae_20d"].mean()),
        },
        "population_B_shadow_top10": {
            "mean_mfe_20d": float(shadow_df["mfe_20d"].mean()),
            "mean_mae_20d": float(shadow_df["mae_20d"].mean()),
        },
    }

    print("[diag] computing daily cross-sectional Spearman IC (score vs label, score vs forward returns)...")
    ic_label = fama_macbeth_daily_ic(merged, "date", "model_score", TARGET)
    ic_by_horizon = {
        str(n): fama_macbeth_daily_ic(merged, "date", "model_score", f"close_return_fwd_{n}") for n in HORIZONS
    }
    ic_score_vs_log_adv20 = fama_macbeth_daily_ic(
        merged.assign(log_adv20=np.log(merged["adv20"].clip(lower=1.0))), "date", "model_score", "log_adv20"
    )
    pooled_corr_score_vs_log_adv20 = float(
        np.corrcoef(merged["model_score"], np.log(merged["adv20"].clip(lower=1.0)))[0, 1]
    )

    print("[diag] NVDA / PLTR eligibility, rank, and shadow-top-10 appearance...")
    nvda_pltr = {}
    for sym in ("NVDA", "PLTR"):
        sub = merged[merged["symbol"] == sym].copy()
        if sub.empty:
            nvda_pltr[sym] = {"eligible_day_count": 0, "note": "symbol not present in validation-period feature rows at all"}
            continue
        ranks = []
        top10_days = 0
        for d, g in merged.groupby("date"):
            if sym not in set(g["symbol"]):
                continue
            g_sorted = g.sort_values("model_score", ascending=False).reset_index(drop=True)
            rank = int(g_sorted.index[g_sorted["symbol"] == sym][0]) + 1
            ranks.append(rank)
            if rank <= shadow_top_n:
                top10_days += 1
        nvda_pltr[sym] = {
            "eligible_day_count": len(ranks),
            "best_rank": int(min(ranks)) if ranks else None,
            "median_rank": float(np.median(ranks)) if ranks else None,
            "worst_rank": int(max(ranks)) if ranks else None,
            "shadow_top10_appearance_count": top10_days,
            "shadow_top10_appearance_rate": (top10_days / len(ranks)) if ranks else None,
        }

    print("[diag] diagnostic-only ADV-component-removed re-scoring (never a new model)...")
    adv_col_idx = feature_names.index("log_adv20_z")
    adv_coef = float(model.coef_.ravel()[adv_col_idx])
    full_logit = X_validation.to_numpy() @ model.coef_.ravel() + model.intercept_[0]
    logit_no_adv = full_logit - adv_coef * X_validation["log_adv20_z"].to_numpy()
    merged["model_score_adv_removed_DIAGNOSTIC_ONLY"] = 1.0 / (1.0 + np.exp(-logit_no_adv))
    adv_removed_top10_metric, _ = top10_metric(merged, "model_score_adv_removed_DIAGNOSTIC_ONLY", shadow_top_n, 42)

    # --- Outputs ---
    shadow_df_export_cols = [
        "symbol", "date", "entry_date", "entry_price", "target_price", "model_score",
        TARGET, "mfe_20d", "mae_20d", "close_return_20d",
        *[f"close_return_fwd_{n}" for n in HORIZONS],
        *[f"target_hit_fwd_{n}" for n in HORIZONS],
    ]
    shadow_df[shadow_df_export_cols].to_csv(DAILY_TOP10_CSV, index=False)

    summary = {
        "population_definitions": {
            "A_all_eligible_daily_rows": "Every (date, symbol) row in the frozen validation split "
                                          f"({FEATURES_PATH}) -- {len(merged)} rows, {merged['date'].nunique()} dates, "
                                          f"already pre-filtered to eligible=True upstream.",
            "B_daily_shadow_top10": f"For each date, the top {shadow_top_n} rows by Model 2 score -- "
                                     f"exactly SandboxConfig.shadow_top_n, the same daily selection "
                                     "CandidateService makes before any capacity/entry/exit policy runs. "
                                     f"{len(shadow_df)} (date, symbol) rows across {merged['date'].nunique()} dates. "
                                     "These are OVERLAPPING, highly-correlated observations (many symbols repeat "
                                     "across nearby dates; forward windows overlap) -- never independent trades.",
            "not_recomputed_here": "The 108 actually-filled EXP-005 Variant B positions and the EXP-005 "
                                    "capital-constrained portfolio result are separate populations, already "
                                    "computed by scripts/exp005_variant_b_price_path_study.py and the real "
                                    "Variant B run -- not reproduced by this script.",
        },
        "inputs": {
            "features_path": FEATURES_PATH,
            "features_sha256": _sha256_of_file(FEATURES_PATH),
            "prices_path": PRICES_PATH,
            "prices_sha256": _sha256_of_file(PRICES_PATH),
        },
        "model": {
            "feature_names_in_order": feature_names,
            "adapter_score_matches_bit_for_bit": adapter_match,
            "train_row_count": int(len(train_df)),
            "validation_row_count": int(len(validation_df)),
        },
        "forward_window_convention": (
            "entry_date is trading session 1 of the horizon (included); N-session close return = "
            "Close[entry_idx + N - 1] / entry_price - 1; N-session MFE/MAE = max(High)/min(Low) over "
            "[entry_idx, entry_idx + N) inclusive of entry_date's own bar. Verified against the frozen "
            "close_return_20d/mfe_20d/mae_20d columns before use -- see verification_against_frozen_columns."
        ),
        "verification_against_frozen_columns": verification,
        "shadow_top_n": shadow_top_n,
        "top10_vs_all_eligible_by_horizon": top10_by_horizon,
        "target_hit_rate": target_hit_summary,
        "mfe_mae_20_session": mfe_mae_20_summary,
        "daily_cross_sectional_spearman_ic": {
            "score_vs_label": ic_label,
            "score_vs_close_return_by_horizon": ic_by_horizon,
            "score_vs_log_adv20": ic_score_vs_log_adv20,
            "score_vs_log_adv20_pooled_pearson": pooled_corr_score_vs_log_adv20,
        },
        "nvda_pltr": nvda_pltr,
        "diagnostic_adv_component_removed_NOT_A_NEW_MODEL": {
            "log_adv20_z_fitted_coefficient": adv_coef,
            "method": "full_logit - (fitted log_adv20_z coefficient * that row's log_adv20_z value), then "
                      "sigmoid -- a diagnostic re-scoring only, never re-fit, never promoted.",
            "top10_42session_with_adv_removed_vs_original": {
                "original_shadow_top10": top10_by_horizon["42"]["population_B_daily_shadow_top_n"],
                "adv_removed_shadow_top10": adv_removed_top10_metric["population_B_daily_shadow_top_n"],
            },
        },
    }

    SUMMARY_JSON.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    daily_csv_sha256 = _sha256_of_file(DAILY_TOP10_CSV)
    summary["outputs"] = {
        "daily_top10_csv": {"path": str(DAILY_TOP10_CSV), "sha256": daily_csv_sha256, "row_count": len(shadow_df)},
        "summary_json_sha256_note": "computed after this dict was serialized; see the file's own git/OS metadata "
                                     "for verification, or re-hash the written file directly.",
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    summary_json_sha256 = _sha256_of_file(SUMMARY_JSON)

    md_lines = [
        "# Model 2 -- Shadow Top-10 Post-Hoc Diagnostic",
        "",
        f"Validation period: {merged['date'].min().date()} .. {merged['date'].max().date()} "
        f"({merged['date'].nunique()} dates, {len(merged)} eligible (date,symbol) rows).",
        f"shadow_top_n = {shadow_top_n}.",
        "",
        "## Verification (recomputed 20-session values vs frozen close_return_20d/mfe_20d/mae_20d)",
        f"- rows compared: {verification['n_rows_compared']}",
        f"- max abs diff: close={verification['close_return_20d_max_abs_diff']:.3e}, "
        f"mfe={verification['mfe_20d_max_abs_diff']:.3e}, mae={verification['mae_20d_max_abs_diff']:.3e}",
        f"- all within {FLOAT_TOLERANCE} tolerance: "
        f"{verification['close_return_20d_all_within_tolerance'] and verification['mfe_20d_all_within_tolerance'] and verification['mae_20d_all_within_tolerance']}",
        "",
        "## Top-10 vs all-eligible forward close return, by horizon",
        "| Horizon | A: all eligible mean | B: shadow top-10 mean | B: shadow top-10 median | B n rows |",
        "|---|---|---|---|---|",
    ]
    for n in HORIZONS:
        a = top10_by_horizon[str(n)]["population_A_all_eligible_rows"]
        b = top10_by_horizon[str(n)]["population_B_daily_shadow_top_n"]
        md_lines.append(
            f"| {n} | {a['mean_return']:.4%} | {b['mean_return']:.4%} | {b['median_return']:.4%} | {b['n_rows']} |"
        )
    md_lines += [
        "",
        "## Target hit rate",
        f"- A (all eligible): label={target_hit_summary['population_A_all_eligible']['target_hit_rate_label']:.4%}, "
        f"recomputed={target_hit_summary['population_A_all_eligible']['target_hit_rate_recomputed_20session']:.4%}",
        f"- B (shadow top-10): label={target_hit_summary['population_B_shadow_top10']['target_hit_rate_label']:.4%}, "
        f"recomputed={target_hit_summary['population_B_shadow_top10']['target_hit_rate_recomputed_20session']:.4%}",
        "",
        "## Daily cross-sectional Spearman IC (Fama-MacBeth, never pooled)",
        f"- score vs label: mean={ic_label['mean_ic']}, median={ic_label['median_ic']}, "
        f"n_dates={ic_label['n_dates']}",
    ]
    for n in HORIZONS:
        ic = ic_by_horizon[str(n)]
        md_lines.append(f"- score vs {n}-session close return: mean={ic['mean_ic']}, median={ic['median_ic']}, "
                         f"frac_positive={ic['fraction_positive']}")
    md_lines += [
        f"- score vs log(ADV20): mean={ic_score_vs_log_adv20['mean_ic']}, "
        f"pooled Pearson={pooled_corr_score_vs_log_adv20:.4f}",
        "",
        "## NVDA / PLTR",
    ]
    for sym, d in nvda_pltr.items():
        md_lines.append(f"- {sym}: {json.dumps(d)}")
    md_lines += [
        "",
        "## Diagnostic: ADV-component-removed re-scoring (NOT a new model)",
        f"- fitted log_adv20_z coefficient: {adv_coef:.6f}",
        f"- original shadow top-10, 42-session mean return: "
        f"{top10_by_horizon['42']['population_B_daily_shadow_top_n']['mean_return']:.4%}",
        f"- ADV-removed shadow top-10, 42-session mean return: "
        f"{adv_removed_top10_metric['population_B_daily_shadow_top_n']['mean_return']:.4%}",
        "",
        "## Hashes",
        f"- features.parquet sha256: {summary['inputs']['features_sha256']}",
        f"- prices.parquet sha256: {summary['inputs']['prices_sha256']}",
        f"- daily_top10 CSV sha256: {daily_csv_sha256}",
        f"- this summary JSON sha256: {summary_json_sha256}",
    ]
    SUMMARY_MD.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"[diag] wrote {DAILY_TOP10_CSV}")
    print(f"[diag] wrote {SUMMARY_JSON}")
    print(f"[diag] wrote {SUMMARY_MD}")
    print("\n".join(md_lines))


if __name__ == "__main__":
    main()
