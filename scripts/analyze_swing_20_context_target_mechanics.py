"""Context and Target Mechanics research cycle (pre-registered), SWING_20.

Tests three pre-registered hypotheses on the frozen SWING_20 train+validation feature
dataset, per the Research Registry decision that followed the feature-replication pass
(MF1/VC3 REJECTED_FOR_SWING_20; rsi_14 PROMISING_EXPLORATORY; rvol_20 reversed-sign
finding NEW_HYPOTHESIS_REQUIRED):

    H1 -- RSI robustness: is the negative rsi_14 effect still present after
          stratifying for log_adv20 and market regime (trend x volatility bucket),
          rather than being a confound of those?
    H2 -- RVOL shape: is the rvol_20 relationship non-linear/U-shaped rather than
          simple negative-monotonic? Tested via pre-defined deciles and a pre-declared
          interaction with return_5d only.
    H3 -- Bear baseline mechanics: is Bear regime's higher SWING_20 hit-rate explained
          by volatility/liquidity composition rather than trend alone? Tested by
          comparing Bull vs Bear hit-rate within matched ADV-quintile x
          volatility-bucket strata.

All stratification cut points (ADV quintiles, RSI terciles, RVOL deciles/terciles,
return_5d sign) are declared in PRE_REGISTRATION below BEFORE any table is computed,
and are not re-chosen after seeing results. Every quantile bin edge (ADV, RSI, RVOL)
is fit on the train split only and then applied unmodified to validation -- validation
never contributes to a boundary computation, so validation stability numbers are a
genuine out-of-sample check rather than validation being scored against its own
distribution. Locked_test is never read. This script is read-only diagnostics -- it
does not train a model.
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

from scripts.analyze_swing_20_feature_replication import daily_cross_sectional_ic, summarize_daily_ic

TARGET = "target_20pct_20d"

PRE_REGISTRATION = {
    "H1_rsi_robustness": {
        "hypothesis": (
            "Lower rsi_14 is associated with a higher SWING_20 probability after "
            "controlling/stratifying for log_adv20, spy_trend, and spy_volatility_bucket."
        ),
        "adv_strata": "adv20 quintile edges fit on train only -> adv_q1 (smallest) .. adv_q5 (largest)",
        "regime_strata": "spy_trend x spy_volatility_bucket, all combinations that occur in the data",
        "effect_size_table": "rsi_14 tercile (edges fit on train only) x adv20 quintile, hit-rate and n per cell",
        "decision_rule": (
            "Continue if the negative daily-IC direction and its significance survive within "
            "the large majority of ADV-quintile and regime strata, in both train and validation. "
            "Reframe if it holds in some strata but not others (regime- or size-conditional). "
            "Stop if the pooled effect disappears once stratified."
        ),
    },
    "H2_rvol_shape": {
        "hypothesis": (
            "The relationship between rvol_20 and SWING_20 is non-linear/U-shaped rather than "
            "a simple negative-monotonic relationship."
        ),
        "shape_table": "rvol_20 decile (edges fit on train only), hit-rate and n per decile, train and validation separately",
        "interaction": (
            "rvol_20 tercile (edges fit on train only) x return_5d sign (Negative: <0, "
            "Non-negative: >=0), hit-rate and n per of the 6 cells, train and validation "
            "separately. No other interaction variable is tested."
        ),
        "decision_rule": (
            "Continue (as a shape hypothesis, not a directional signal) if a non-monotonic "
            "(U- or inverse-U-shaped) pattern is visible and stable across train/validation. "
            "Reframe if the shape is monotonic after all (the simple negative reading from the "
            "replication pass would then stand, just previously under-described). Stop if the "
            "pattern is not stable between train and validation."
        ),
    },
    "H3_bear_baseline_mechanics": {
        "hypothesis": (
            "The higher Bear-regime SWING_20 hit-rate is primarily explained by volatility "
            "and/or universe-composition (ADV) differences rather than spy_trend alone."
        ),
        "strata": "adv20 quintile (train-fit) x spy_volatility_bucket, compare Bull vs Bear hit-rate and n within each cell",
        "decision_rule": (
            "Reframe (trend x volatility interaction, not a universal Bear premium) if the "
            "Bull/Bear gap is concentrated in specific volatility strata (e.g. present in High "
            "volatility but absent or reversed in Normal volatility), rather than uniform across "
            "all strata. Continue (trend has a genuinely universal independent effect) only if "
            "the gap persists at a similar magnitude in EVERY matched stratum, including Normal "
            "volatility, in both train and validation. Stop if cells are too sparse to draw any "
            "conclusion. NOTE: the train+validation-combined table must never be used on its own "
            "to make this call -- it can average over a split-dependent interaction and hide "
            "exactly the pattern this hypothesis is testing for. Decide from the per-split tables."
        ),
    },
    "preprocessing_fit_policy": (
        "All quantile/tercile/decile bin edges (adv20 quintiles, rsi_14 terciles, rvol_20 "
        "deciles and terciles) are computed with pd.qcut on the train split ONLY, then applied "
        "to both train and validation via pd.cut using those exact train-fit edges (with the "
        "outer edges extended to -inf/+inf so no validation row falls outside the fitted range). "
        "Validation rows never contribute to where a bucket boundary sits. This differs from an "
        "earlier version of this script, which fit adv20/rsi_14 edges on the train+validation "
        "union and fit rvol_20 edges independently per split -- both are corrected here."
    ),
    "safeguards": [
        "Same frozen train/validation split as the replication pass; locked_test not read.",
        "Fama-MacBeth daily cross-sectional Spearman IC used for all IC estimates.",
        "Effect size (hit-rate, lift) and sample size reported alongside every t-statistic.",
        "All quantile/tercile/decile cut points are fit on train only, fixed before computation, and not re-chosen after seeing results.",
        "Any pattern noticed outside the three pre-registered hypotheses is reported as exploratory only.",
    ],
}


def _log_adv(df: pd.DataFrame) -> pd.Series:
    return np.log(df["adv20"].clip(lower=1))


def _fit_quantile_edges(train_series: pd.Series, q: int) -> np.ndarray:
    """Fit quantile bin edges on train data only; the same edges are reused, unmodified,
    to bucket validation -- validation never contributes to where a boundary sits."""

    _, edges = pd.qcut(train_series, q, retbins=True, duplicates="drop")
    edges = edges.astype(float).copy()
    edges[0] = -np.inf
    edges[-1] = np.inf
    return edges


def _apply_quantile_bucket(series: pd.Series, edges: np.ndarray, labels: list[str]) -> pd.Series:
    n_bins = len(edges) - 1
    return pd.cut(series, bins=edges, labels=labels[:n_bins], include_lowest=True)


def _hit_rate_table(df: pd.DataFrame, group_cols: list[str], target: str = TARGET) -> list[dict[str, object]]:
    rows = []
    for keys, group in df.groupby(group_cols, dropna=True, observed=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {col: str(key) for col, key in zip(group_cols, keys)}
        row["n"] = int(len(group))
        row["positive_rate"] = float(group[target].mean()) if len(group) else None
        rows.append(row)
    return rows


def h1_rsi_robustness(df: pd.DataFrame, adv_edges: np.ndarray, adv_labels: list[str]) -> dict[str, object]:
    train_mask = df["split"] == "train"

    work = df.assign(adv_quintile=_apply_quantile_bucket(_log_adv(df), adv_edges, adv_labels))
    work["regime"] = work["spy_trend"].astype(str) + "_" + work["spy_volatility_bucket"].astype(str)

    def _ic_by_stratum(frame: pd.DataFrame, stratum_col: str) -> dict[str, object]:
        result = {}
        for stratum, group in frame.groupby(stratum_col, dropna=True, observed=True):
            result[str(stratum)] = summarize_daily_ic(
                daily_cross_sectional_ic(group, "rsi_14", TARGET)
            ) | {"n_obs": int(len(group))}
        return result

    by_split_adv = {}
    by_split_regime = {}
    for split, split_group in work.groupby("split"):
        by_split_adv[str(split)] = _ic_by_stratum(split_group, "adv_quintile")
        by_split_regime[str(split)] = _ic_by_stratum(split_group, "regime")

    rsi_labels = ["rsi_low", "rsi_mid", "rsi_high"]
    rsi_edges = _fit_quantile_edges(df.loc[train_mask, "rsi_14"], 3)
    work["rsi_tercile"] = _apply_quantile_bucket(work["rsi_14"], rsi_edges, rsi_labels)

    effect_size_table = {}
    for split, split_group in work.groupby("split"):
        effect_size_table[str(split)] = _hit_rate_table(split_group, ["adv_quintile", "rsi_tercile"])

    return {
        "adv_quintile_daily_ic_by_split": by_split_adv,
        "regime_daily_ic_by_split": by_split_regime,
        "rsi_tercile_edges_fit_on_train": rsi_edges[1:-1].tolist(),
        "adv_quintile_x_rsi_tercile_hit_rate_by_split": effect_size_table,
    }


def h2_rvol_shape(df: pd.DataFrame) -> dict[str, object]:
    train_mask = df["split"] == "train"

    decile_labels = [f"d{i}" for i in range(1, 11)]
    decile_edges = _fit_quantile_edges(df.loc[train_mask, "rvol_20"], 10)
    work = df.assign(rvol_decile=_apply_quantile_bucket(df["rvol_20"], decile_edges, decile_labels))
    decile_table = {}
    for split, split_group in work.groupby("split"):
        decile_table[str(split)] = _hit_rate_table(split_group, ["rvol_decile"])

    tercile_labels = ["rvol_low", "rvol_mid", "rvol_high"]
    tercile_edges = _fit_quantile_edges(df.loc[train_mask, "rvol_20"], 3)
    work["rvol_tercile"] = _apply_quantile_bucket(df["rvol_20"], tercile_edges, tercile_labels)
    work["return_5d_sign"] = np.where(df["return_5d"] < 0, "negative", "non_negative")
    interaction_table = {}
    for split, split_group in work.groupby("split"):
        interaction_table[str(split)] = _hit_rate_table(split_group, ["rvol_tercile", "return_5d_sign"])

    return {
        "rvol_decile_edges_fit_on_train": decile_edges[1:-1].tolist(),
        "rvol_decile_hit_rate_by_split": decile_table,
        "rvol_tercile_edges_fit_on_train": tercile_edges[1:-1].tolist(),
        "rvol_tercile_x_return_5d_sign_hit_rate_by_split": interaction_table,
    }


def h3_bear_baseline_mechanics(df: pd.DataFrame, adv_edges: np.ndarray, adv_labels: list[str]) -> dict[str, object]:
    work = df.assign(adv_quintile=_apply_quantile_bucket(_log_adv(df), adv_edges, adv_labels))

    unconditional_by_split = {}
    for split, split_group in work.groupby("split"):
        unconditional_by_split[str(split)] = _hit_rate_table(split_group, ["spy_trend"])

    stratified = {}
    for split, split_group in work.groupby("split"):
        stratified[str(split)] = _hit_rate_table(split_group, ["adv_quintile", "spy_volatility_bucket", "spy_trend"])

    # Reference only -- averages over train/validation and can mask a split-dependent
    # interaction. Must NOT be used on its own to decide H3; see decision_rule above.
    combined_strata_reference_only = _hit_rate_table(work, ["adv_quintile", "spy_volatility_bucket", "spy_trend"])

    return {
        "unconditional_bull_vs_bear_by_split": unconditional_by_split,
        "adv_quintile_x_volatility_bucket_x_trend_hit_rate_by_split": stratified,
        "adv_quintile_x_volatility_bucket_x_trend_hit_rate_combined_REFERENCE_ONLY": combined_strata_reference_only,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="SWING_20 Context and Target Mechanics research cycle.")
    parser.add_argument("--features-path", required=True)
    parser.add_argument("--output-json", default="artifacts/swing_20_context_target_mechanics_report.json")
    args = parser.parse_args()

    df = pd.read_parquet(args.features_path)
    print(f"[mechanics] loaded {len(df)} rows, {df['symbol'].nunique()} symbols", flush=True)

    train_mask = df["split"] == "train"
    adv_labels = [f"adv_q{i}" for i in range(1, 6)]
    adv_edges = _fit_quantile_edges(_log_adv(df.loc[train_mask]), 5)

    report = {
        "pre_registration": PRE_REGISTRATION,
        "sample_size": int(len(df)),
        "symbol_count": int(df["symbol"].nunique()),
        "split_counts": df["split"].value_counts().to_dict(),
        "adv_quintile_edges_fit_on_train_log_adv20": adv_edges[1:-1].tolist(),
        "H1_rsi_robustness": h1_rsi_robustness(df, adv_edges, adv_labels),
        "H2_rvol_shape": h2_rvol_shape(df),
        "H3_bear_baseline_mechanics": h3_bear_baseline_mechanics(df, adv_edges, adv_labels),
    }

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"[mechanics] wrote {output_path}", flush=True)


if __name__ == "__main__":
    main()
