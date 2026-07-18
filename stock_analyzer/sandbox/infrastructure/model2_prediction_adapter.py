"""Adapter around the frozen Model 2 implementation.

Calls scripts/train_swing_20_logistic_baseline.py's existing functions
(fit_on_train, make_design_matrix, train_logistic) rather than duplicating Model 2's
formulas. Per docs/09_experiments/EXP-003_SWING20_Locked_Test.md Part 1, refitting
sklearn's LogisticRegression with solver="lbfgs" on the identical train data and
hyperparameters is deterministic, so this reproduces the exact frozen Model 2
coefficients bit-for-bit -- no separate serialized model artifact is required.

The output is called `model_score` everywhere in the sandbox -- an ordinal ranking
score, never a "probability of success" (Model 2's calibration was not validated;
only its ranking was -- see EXP-002/EXP-003).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.train_swing_20_logistic_baseline import (  # noqa: E402
    fit_on_train,
    make_design_matrix,
    train_logistic,
)
from stock_analyzer.sandbox.config import MODEL_VERSION  # noqa: E402

TARGET = "target_20pct_20d"

# The exact frozen Model 2 feature list and order, per EXP-003 Part 1 section 1.
# Compared against the adapter's own fitted design matrix at construction time as a
# regression guard: if scripts/train_swing_20_logistic_baseline.py's make_design_matrix
# ever changes, this adapter fails loudly instead of silently scoring with a different
# model than the one that passed the Locked Test.
FROZEN_MODEL2_FEATURE_LIST = (
    "log_adv20_z",
    "is_bear",
    "is_vol_low",
    "is_vol_high",
    "is_bear_x_vol_high",
    "rvol_d1",
    "rvol_d2",
    "rvol_d3",
    "rvol_d4",
    "rvol_d6",
    "rvol_d7",
    "rvol_d8",
    "rvol_d9",
    "rvol_d10",
    "rsi_14_z",
    "rsi_14_z_x_bear",
    "rsi_14_z_x_low_adv",
)


class FrozenModelMismatchError(RuntimeError):
    """Raised when the live Model 2 implementation no longer matches the frozen
    feature list this sandbox was built and Locked-Test-validated against."""


class Model2PredictionAdapter:
    """Fits the frozen Model 2 once (train split only) and scores new rows."""

    def __init__(self, train_features_path: str) -> None:
        train_df = pd.read_parquet(train_features_path)
        train_df = train_df[train_df["split"] == "train"].copy()

        self._fit = fit_on_train(train_df)
        X_train = make_design_matrix(train_df, self._fit, "model2")

        actual = tuple(X_train.columns)
        if actual != FROZEN_MODEL2_FEATURE_LIST:
            raise FrozenModelMismatchError(
                "scripts/train_swing_20_logistic_baseline.py's Model 2 feature list "
                f"no longer matches the frozen manifest.\nExpected: {FROZEN_MODEL2_FEATURE_LIST}\n"
                f"Actual:   {actual}"
            )

        y_train = train_df[TARGET].to_numpy().astype(float)
        self._model = train_logistic(X_train, y_train)
        self.feature_names = actual
        self.model_version = MODEL_VERSION
        self.train_row_count = int(len(train_df))
        # Exposed read-only so callers (e.g. CandidateService) can bucket adv20/rvol_20
        # using the exact same train-fit edges this adapter scores with, instead of
        # recomputing a second, potentially-divergent set of edges.
        self.fit_params = dict(self._fit)

    def score(self, features_df: pd.DataFrame) -> pd.Series:
        """`features_df` must have the same stock-level/context columns as the frozen
        train+validation feature dataset (adv20, rvol_20, rsi_14, spy_trend,
        spy_volatility_bucket), indexed by symbol. Returns model_score per symbol --
        an ordinal ranking score, not a calibrated probability."""

        if features_df.empty:
            return pd.Series(dtype=float)
        X = make_design_matrix(features_df, self._fit, "model2")
        probs = self._model.predict_proba(X.to_numpy())[:, 1]
        return pd.Series(probs, index=features_df.index, name="model_score")
