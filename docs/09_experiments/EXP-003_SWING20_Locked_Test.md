# Experiment Record

## Experiment ID

```text
EXP-003
```

## Title

SWING_20 Model 2 Locked Test -- pre-registration and (after this section is committed)
final result.

**This document has two parts, committed separately, so the git history itself proves
the pre-registration existed before any Locked Test row was read:**

- **Part 1 -- Pre-Registration** (this commit): model manifest, protocol, metric
  definitions, and decision thresholds, written and committed before opening
  locked_test.
- **Part 2 -- Result** (a later commit, appended below Part 1 once the one-shot
  evaluation has run): the actual numbers and the PASS / CONDITIONAL_PASS / FAIL
  verdict, applying Part 1's rules mechanically.

## Date

```text
2026-07-18
```

## Owner

Claude (agent), instructions relayed from ChatGPT (research lead) via Meelis Kivimäe.

## Related Experiments

- EXP-001_SWING20_Feature_Replication_and_Context_Mechanics.md
- EXP-002_SWING20_Logistic_Baseline.md -- Model 2 PASSED validation there and is the
  single candidate approved for this Locked Test.

## Related ADRs

- ADR-005-BlockBootstrap.md -- SWING_20 labels use overlapping 20-trading-day forward
  windows, so consecutive dates are not independent; this is why the Locked Test CI
  uses >=20-trading-day contiguous bootstrap blocks (see Part 1, section 3 below), not
  i.i.d. resampling of daily values.

---

# Part 1 -- Pre-Registration

Written and committed before any Locked Test row is read. Nothing in this section may
be changed after Locked Test results are observed.

## 1. Model identity (frozen manifest)

```text
Frozen implementation commit: 8857532adf518206cecc8c901866a128c9d170cf
  (scripts/train_swing_20_logistic_baseline.py, tests/test_swing20_logistic_baseline.py)
Frozen SWING_20 snapshot: swing20_20260718T135238Z
Frozen feature dataset (train+validation): artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet
Feature specification: SWING20_PointInTime_Feature_Specification_v1 (replication-pass subset)
```

**Refit policy**: the Locked Test evaluates the model **fit on train only** (591
dates, 1,206,131 rows, 2022-07-14 .. 2024-11-15), exactly as already evaluated in
EXP-002 -- the same object, not a retrain. Per the instruction's default ("otherwise
use the existing train-fitted model unchanged"), **no refit on train+validation is
performed** before or for this Locked Test. Refitting sklearn's `LogisticRegression`
with `solver="lbfgs"` on the identical train data and identical hyperparameters is
deterministic (lbfgs is a deterministic quasi-Newton optimizer, not stochastic), so
re-running `fit_on_train` + `make_design_matrix` + `train_logistic` on the same frozen
train feature parquet reproduces the exact EXP-002 Model 2 coefficients bit-for-bit;
no model artifact needs to be separately serialized.

**Exact feature list and order** (17 features, `model2` in
`make_design_matrix()`):

```text
 1. log_adv20_z
 2. is_bear
 3. is_vol_low
 4. is_vol_high
 5. is_bear_x_vol_high
 6. rvol_d1
 7. rvol_d2
 8. rvol_d3
 9. rvol_d4
10. rvol_d6
11. rvol_d7
12. rvol_d8
13. rvol_d9
14. rvol_d10
15. rsi_14_z
16. rsi_14_z_x_bear
17. rsi_14_z_x_low_adv
```

`is_bear_x_vol_low` is **not** in the feature list: `check_not_estimable_interactions`
confirms Bear x Low volatility has 0 rows in train, validation, and (to be verified
once locked_test features are built) is expected to remain 0 in locked_test, since
this is a structural property of `stock_analyzer.validation.regime.build_market_regime`
(Bear periods do not co-occur with Low realized/VIX volatility in this dataset), not a
split-specific artifact. **This term is not added back under any circumstance in this
Locked Test cycle.** Neither `is_bear_x_vol_high` nor the middle RVOL deciles
(`rvol_d6`, `rvol_d7`) -- the two weakest terms under EXP-002's date-level stability
check -- are removed, despite their weaker sign consistency; removing them would
constitute a new model (Model 3), which is out of scope for this Locked Test.

**Logistic Regression hyperparameters**: `sklearn.linear_model.LogisticRegression(C=1.0,
solver="lbfgs", max_iter=2000)`, L2 penalty (sklearn default), unweighted (no
`class_weight`). Not tuned via search; fixed since EXP-001/002.

**Preprocessing / fitting policy**: all of the following are fit on the train split
only (via `fit_on_train(train_df)`) and applied unchanged to locked_test:
- `log_adv20_z`: `(log(adv20.clip(lower=1)) - train_mean) / train_std`.
- `rsi_14_z`: `(rsi_14 - train_mean) / train_std`.
- ADV quintile edges: `pd.qcut` on train's `log(adv20)`, 5 bins, outer edges extended
  to -inf/+inf.
- RVOL decile edges: `pd.qcut` on train's `rvol_20`, 10 bins, outer edges extended to
  -inf/+inf; reference decile `d5` (near the U-shape minimum, EXP-001 H2) is dropped
  from the dummy encoding.
- `is_low_adv`: indicator for `adv_quintile == "adv_q1"` under the train-fit edges.

**Missing-value policy**: none of the 17 features have missing values in the frozen
train+validation feature dataset (verified in EXP-001/EXP-002 development); the
pipeline does not impute. If any locked_test row has a missing value in a feature
column when features are built, `make_design_matrix` will propagate NaN into that
row's design matrix and `LogisticRegression.predict_proba` will raise rather than
silently guess -- this is treated as a data-quality finding to report, not something
to patch by inventing an imputation rule after the fact.

**Ranking / tie-breaking rule**: within a date, rows are ranked by predicted
probability descending via `pandas.DataFrame.sort_values(ascending=False)` (default
`kind="quicksort"`, not guaranteed stable). In practice this has no material effect:
predicted probabilities are continuous functions of continuous inputs
(`log_adv20_z`, `rsi_14_z`, decile dummies), so exact ties between two different
`(symbol, date)` rows are not expected to occur except by construction. The one case
where scores ARE exactly tied by construction is the context-only diagnostic score
(`C_context_only_*`), which is deliberately identical for every symbol on a date;
`daily_rank_metrics` detects this (zero within-date score variance) and reports that
date's own base rate analytically rather than relying on sort order -- this avoids any
hidden tie-break bias for the one score where ties are expected. No change is made to
this behavior for the Locked Test.

**Software dependency versions** (this environment, unchanged since EXP-001/002):

```text
python 3.13.7
pandas 3.0.3
numpy 2.2.6
scikit-learn 1.9.0
```

**Deterministic random seeds**: the primary Model 2 fit requires no seed (lbfgs is
deterministic). The only stochastic component reused for the Locked Test is the
date-block bootstrap CI (`block_bootstrap_ci`, seed=7, 1000 iterations, block length
20 trading days -- see section 3). Coefficient date-resampling
(`coefficient_stability_by_date`, seed=13) is a validation-only diagnostic and is not
re-run against locked_test (there is no second dataset to resample stability across;
Locked Test is read once).

## 2. Locked Test protocol

- Locked_test is read **exactly once**, after this Part 1 is committed.
- No refitting occurs on validation or on locked_test. The model evaluated is the
  train-only fit described above.
- Train-fitted preprocessing (standardization parameters, ADV/RVOL bucket edges) and
  the frozen Model 2 specification are applied to locked_test unchanged.
- No model, feature, threshold, or metric definition may change after locked_test
  results are observed. If a result is surprising, it is reported and discussed --
  not "fixed" by re-running with a different feature set inside this cycle.

## 3. Primary Locked Test metrics

Computed identically to EXP-002's corrected daily cross-sectional method (same
`daily_rank_metrics` function, unmodified), for:

- top 5 symbols, top 10 symbols, top 20 symbols (fixed N per date)
- top 1%, top 5%, top 10% (percentage of that date's eligible universe)

For each, reported:

- mean and median daily precision
- mean and median daily lift against that same date's own base rate
- fraction of dates with lift > 1
- fraction of dates with at least one positive in the selected set
- evaluated date count (`n_dates`)
- candidate count distribution (min / median / max selected count per date)

**Primary business metric**: daily top-10-symbols. **Primary broad-ranking robustness
metric**: daily top-10%.

## 4. Dependence-aware uncertainty

SWING_20 labels use overlapping 20-trading-day forward windows, so consecutive
trading days' outcomes are not independent. The 95% CI on each daily-lift series uses
`block_bootstrap_ci`: a moving-block bootstrap with **block length = 20 trading days**
(matching the label horizon, per ADR-005's rationale), **1000 resamples**, **seed =
7**. This is the same function and same defaults already used, unmodified, throughout
EXP-002 -- no new estimator is introduced for the Locked Test.

## 5. Pre-declared decision thresholds

**PASS** requires ALL of the following to hold on locked_test:

1. Daily top-10-symbols mean lift >= 1.50.
2. Daily top-10-symbols lift block-bootstrap 95% CI lower bound > 1.00.
3. At least one positive in the top-10 on >= 70% of evaluated dates.
4. Daily top-10% mean lift >= 1.20.
5. The result is not dominated by a single chronological subperiod or a narrow set of
   tickers (see section 6 -- reported as a diagnostic; a PASS is disqualified if, e.g.,
   one of the 4 pre-declared subperiods accounts for effectively all of the positive
   outcomes, or a small handful of symbols account for most of the top-10 slots
   without corresponding to a broad-based ranking signal).

**CONDITIONAL_PASS**: ranking is clearly above the same-day baseline (i.e.
requirements 1-2 hold, or are narrowly missed by a small margin while the others
clearly hold), but exactly one secondary stability requirement (3, 4, or 5) narrowly
fails. Labeled `CONDITIONALLY_VALIDATED`. Per instruction, a CONDITIONAL_PASS does not
trigger feature re-selection inside this cycle -- the next step would be paper-trading
or a forward test, not repeating the Locked Test.

**FAIL**: the top-10 ranking does not reliably beat the same-day baseline (requirement
1 or 2 clearly fails), or the result is dominated by a narrow period/ticker
concentration. Labeled `FAILED_LOCKED_TEST`. Locked Test is not reopened to try a
different feature subset in this cycle.

These thresholds are deliberately set below EXP-002's validation numbers (validation
daily top-10-symbols mean lift was 3.71, more than double the 1.50 PASS bar) -- the
Locked Test is a replication check, not a demand for an equally exceptional result.

## 6. Additional diagnostics (reported, not used to redefine the model)

- Chronological subperiod results: locked_test's own dates split into 4 pre-declared
  equal-day-count blocks (same `temporal_blocks` function/parameters as EXP-002),
  boundaries fixed purely by date sequence before any result is seen.
- Ticker concentration: number of distinct symbols appearing in any daily top-10 pick
  over the whole locked_test period, and the most-frequently-selected symbols' share
  of total top-10 slots and of total positive outcomes.
- ADV-quintile breakdown (train-fit edges).
- Trend x volatility regime breakdown.
- Calibration diagnostics (Brier score, calibration intercept/slope, ECE) --
  descriptive only, no calibrator fit.
- MFE (`mfe_20d`) / MAE (`mae_20d`) distribution among top-10 picks vs. the locked_test
  population, where available in the frozen labels.
- Context-only vs. stock-only decomposition (same method as EXP-002), to confirm
  whether the locked_test lift is also predominantly stock-selection-driven.

## 7. Durable documentation

This document is the pre-registration. Part 2 (below) is appended and committed
separately, after the one-shot Locked Test run, applying these rules mechanically. No
retraining, revision, or promotion occurs in the same cycle as the Locked Test result.
