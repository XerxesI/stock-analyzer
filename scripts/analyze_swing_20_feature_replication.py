"""Analyze whether MF1 (rvol_20) and VC3 (compression_pct_100) replicate on the
SWING_20 label, and run fail-fast diagnostics on the remaining replication-pass
features. Reads a feature dataset produced by build_swing_20_feature_dataset.py.

This is a read-only analysis script: it does not modify the feature dataset, does
not touch locked_test (the feature dataset itself never contains it), and does not
train a model.
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


def daily_cross_sectional_ic(df: pd.DataFrame, feature: str, target: str, min_symbols: int = 20) -> pd.Series:
    """Fama-MacBeth-style daily cross-sectional Spearman IC.

    Computes one correlation per date (not one correlation pooling all rows), so the
    result respects same-date dependence instead of treating ~1.6M rows as
    independent observations -- the same concern ADR-005 and the Baseline Evaluation
    Plan raise for point-estimate metrics on this data.
    """

    def _one_day(group: pd.DataFrame) -> float:
        valid = group[[feature, target]].dropna()
        if len(valid) < min_symbols or valid[feature].nunique() < 2:
            return np.nan
        # Spearman IC = Pearson correlation of the ranks. Computed this way (rather
        # than pandas' .corr(method="spearman")) because pandas' spearman path always
        # imports scipy.stats.spearmanr internally, and scipy is not a project
        # dependency here; pandas' pearson path uses numpy.corrcoef and needs no scipy.
        ranked = valid.rank()
        return float(ranked[feature].corr(ranked[target], method="pearson"))

    return df.groupby("date").apply(_one_day, include_groups=False).dropna()


def summarize_daily_ic(daily_ic: pd.Series) -> dict[str, object]:
    if daily_ic.empty:
        return {"n_days": 0, "mean_ic": None, "std_ic": None, "t_stat": None}
    n = len(daily_ic)
    mean_ic = float(daily_ic.mean())
    std_ic = float(daily_ic.std(ddof=1)) if n > 1 else None
    t_stat = float(mean_ic / (std_ic / np.sqrt(n))) if std_ic and std_ic > 0 else None
    return {"n_days": n, "mean_ic": mean_ic, "std_ic": std_ic, "t_stat": t_stat}


def positive_rate_by_quantile(df: pd.DataFrame, feature: str, target: str, q: int = 5) -> dict[str, object]:
    valid = df[[feature, target]].dropna()
    if valid.empty or valid[feature].nunique() < q:
        return {}
    valid = valid.copy()
    valid["bucket"] = pd.qcut(valid[feature], q=q, duplicates="drop", labels=False)
    grouped = valid.groupby("bucket")[target].agg(["mean", "count"])
    return {
        f"q{int(bucket) + 1}": {"positive_rate": float(row["mean"]), "n": int(row["count"])}
        for bucket, row in grouped.iterrows()
    }


def regime_breakdown(df: pd.DataFrame, feature: str, target: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for regime, group in df.groupby("spy_trend", dropna=True):
        daily_ic = daily_cross_sectional_ic(group, feature, target)
        result[str(regime)] = summarize_daily_ic(daily_ic) | {
            "n_obs": int(len(group)),
            "positive_rate": float(group[target].mean()) if target == "target_20pct_20d" else None,
        }
    return result


def split_stability(df: pd.DataFrame, feature: str, target: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for split, group in df.groupby("split", dropna=True):
        daily_ic = daily_cross_sectional_ic(group, feature, target)
        result[str(split)] = summarize_daily_ic(daily_ic)
    return result


def mf1_replication(df: pd.DataFrame) -> dict[str, object]:
    feature = "rvol_20"
    return {
        "sample_size": int(df[feature].notna().sum()),
        "positive_rate_by_quantile": positive_rate_by_quantile(df, feature, "target_20pct_20d"),
        "daily_ic_vs_binary_target": summarize_daily_ic(daily_cross_sectional_ic(df, feature, "target_20pct_20d")),
        "daily_ic_vs_close_return_20d": summarize_daily_ic(daily_cross_sectional_ic(df, feature, "close_return_20d")),
        "regime_breakdown": regime_breakdown(df, feature, "target_20pct_20d"),
        "split_stability": split_stability(df, feature, "target_20pct_20d"),
    }


def vc3_replication(df: pd.DataFrame) -> dict[str, object]:
    feature = "compression_pct_100"
    standalone = {
        "sample_size": int(df[feature].notna().sum()),
        "daily_ic_vs_binary_target": summarize_daily_ic(daily_cross_sectional_ic(df, feature, "target_20pct_20d")),
        "regime_breakdown": regime_breakdown(df, feature, "target_20pct_20d"),
        "split_stability": split_stability(df, feature, "target_20pct_20d"),
    }

    # Pre-declared VC3 rule: bottom-20% compression (compression_pct <= 0.20) AND
    # rvol_20 > 1.0. Not searched after seeing results -- this is the same rule prior
    # research validated.
    valid = df.dropna(subset=["compression_pct_100", "rvol_20", "target_20pct_20d"])
    activated = valid[(valid["compression_pct_100"] <= 0.20) & (valid["rvol_20"] > 1.0)]
    baseline_rate = float(valid["target_20pct_20d"].mean()) if len(valid) else None
    activated_rate = float(activated["target_20pct_20d"].mean()) if len(activated) else None

    interaction = {
        "activated_sample_size": int(len(activated)),
        "activated_positive_rate": activated_rate,
        "baseline_positive_rate": baseline_rate,
        "lift": (activated_rate / baseline_rate) if activated_rate and baseline_rate else None,
        "regime_breakdown": {
            str(regime): {
                "n": int(len(group)),
                "positive_rate": float(group["target_20pct_20d"].mean()) if len(group) else None,
            }
            for regime, group in activated.groupby("spy_trend", dropna=True)
        },
        "split_stability": {
            str(split): {
                "n": int(len(group)),
                "positive_rate": float(group["target_20pct_20d"].mean()) if len(group) else None,
            }
            for split, group in activated.groupby("split", dropna=True)
        },
    }
    return {"standalone": standalone, "compression_plus_rvol_interaction": interaction}


def other_feature_diagnostics(df: pd.DataFrame) -> dict[str, object]:
    result: dict[str, object] = {}
    for feature in ("return_5d", "return_20d", "rsi_14"):
        result[feature] = {
            "daily_ic_vs_binary_target": summarize_daily_ic(daily_cross_sectional_ic(df, feature, "target_20pct_20d")),
            "regime_breakdown": regime_breakdown(df, feature, "target_20pct_20d"),
            "split_stability": split_stability(df, feature, "target_20pct_20d"),
        }
    log_adv20 = df.assign(log_adv20=np.log(df["adv20"].clip(lower=1)))
    result["log_adv20"] = {
        "daily_ic_vs_binary_target": summarize_daily_ic(
            daily_cross_sectional_ic(log_adv20, "log_adv20", "target_20pct_20d")
        ),
        "regime_breakdown": regime_breakdown(log_adv20, "log_adv20", "target_20pct_20d"),
        "split_stability": split_stability(log_adv20, "log_adv20", "target_20pct_20d"),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze SWING_20 feature replication.")
    parser.add_argument("--features-path", required=True)
    parser.add_argument("--output-json", default="artifacts/swing_20_feature_replication_report.json")
    args = parser.parse_args()

    df = pd.read_parquet(args.features_path)
    print(f"[replication] loaded {len(df)} rows, {df['symbol'].nunique()} symbols", flush=True)

    report = {
        "sample_size": int(len(df)),
        "symbol_count": int(df["symbol"].nunique()),
        "date_range": {"start": str(df["date"].min().date()), "end": str(df["date"].max().date())},
        "split_counts": df["split"].value_counts().to_dict(),
        "mf1_rvol_20": mf1_replication(df),
        "vc3_compression_pct_100": vc3_replication(df),
        "other_feature_diagnostics": other_feature_diagnostics(df),
    }

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"[replication] wrote {output_path}", flush=True)


if __name__ == "__main__":
    main()
