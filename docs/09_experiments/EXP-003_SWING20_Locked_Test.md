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

---

# Part 2 -- Result

Locked_test was read exactly once, after Part 1 above was committed (commit
`7a8995e`). No change was made to the model, feature list, hyperparameters,
preprocessing, or decision thresholds after this point. One plumbing bug was found and
fixed during the run (`pd.concat([train_df, locked_df])` produced duplicate row-index
labels, crashing `check_not_estimable_interactions`'s internal `pd.crosstab`; fixed
with `ignore_index=True`) -- this is an infrastructure fix, not a change to the model,
methodology, or thresholds, and the corrected run is the one reported below.

## Locked_test population

```text
Rows: 493,651
Symbols: 3,227
Dates: 198 (2025-09-04 .. 2026-06-17)
```

This period was previously completely unused in any prior SWING_20 work in this
project -- it was excluded from every feature build, audit, and analysis through
EXP-001 and EXP-002.

## Decision (VERDICT)

```text
PASS
```

| # | Check | Threshold | Locked_test value | Pass? |
|---|---|---|---|---|
| 1 | Top-10-symbol mean daily lift | >= 1.50 | **2.74** | Yes |
| 2 | Top-10-symbol lift, block-bootstrap 95% CI lower bound | > 1.00 | **2.26** | Yes |
| 3 | Fraction of dates with >=1 positive in top-10 | >= 0.70 | **0.99** | Yes |
| 4 | Daily top-10% mean lift | >= 1.20 | **1.41** | Yes |
| 5 | Not dominated by one subperiod or ticker concentration | -- | see below | Yes |

All five pre-registered conditions hold, several by a wide margin -- **Model 2 PASSES
the Locked Test.**

## Primary daily cross-sectional metrics

| Selection | n dates | Mean daily precision | Median daily precision | Mean daily lift | Median daily lift | 95% CI (lift) | % dates lift>1 | % dates with >=1 positive |
|---|---|---|---|---|---|---|---|---|
| Top 5 symbols | 198 | 40.2% | -- | 2.79 | 2.69 | -- | 87.9% | 89.4% |
| **Top 10 symbols (primary)** | 198 | **38.6%** | 40.0% | **2.74** | 2.54 | **[2.26, 3.21]** | 90.9% | **99.0%** |
| Top 20 symbols | 198 | 34.3% | -- | 2.41 | 2.31 | -- | 94.9% | 99.5% |
| Top 1% | 198 | 32.3% | -- | 2.26 | 2.18 | -- | 94.4% | 99.5% |
| Top 5% | 198 | 23.7% | -- | 1.62 | 1.57 | -- | 95.5% | 100.0% |
| **Top 10% (primary robustness)** | 198 | **20.8%** | 19.4% | **1.41** | 1.38 | **[1.27, 1.51]** | 92.4% | 100.0% |

Candidate counts were exactly 10/20/etc. for the fixed-N metrics on every date (no
date had fewer than 10 eligible symbols); percentage-based candidate counts ranged
237-262 for top-10%.

## Context-only vs. stock-only decomposition (global, diagnostic)

| k | A (full model) | C (context-only) | D (stock-only) |
|---|---|---|---|
| top 1% | lift 2.06 | lift 1.62 | lift 2.18 |
| top 5% | lift 1.70 | lift 1.60 | lift 1.60 |
| top 10% | lift 1.50 | lift 1.36 | lift 1.43 |

Unlike EXP-002's validation result (where context-only global lift was *below* 1 and
stock-only carried essentially all of the lift), locked_test's context-only score
carries real positive lift on its own (1.36-1.62) -- the market-regime signal
generalizes too. Stock-only lift remains close to or above the full model's own lift
at every k (and is the single highest number at top-1%, 2.18), confirming stock
selection is not a validation-period artifact: it holds up out-of-sample on its own,
in addition to context also holding up.

## Subperiod robustness (4 pre-declared chronological blocks)

| Block | Dates | n | Top-10-symbol lift | % dates with a positive | Top-10% lift |
|---|---|---|---|---|---|
| 0 | 2025-09-04 .. 2025-11-12 | 50 | 3.05 | 100% | 1.52 |
| 1 | 2025-11-13 .. 2026-01-27 | 50 | 2.83 | 98% | 1.43 |
| 2 | 2026-01-28 .. 2026-04-08 | 49 | 3.12 | 98% | 1.43 |
| 3 | 2026-04-09 .. 2026-06-17 | 49 | 1.95 | 100% | 1.25 |

Every block clears lift > 1 comfortably on both metrics; block 3 is the weakest but
still well above the PASS bar. The result is not concentrated in a single sub-period.

## Ticker concentration

481 distinct symbols appeared in a daily top-10 pick across the 198-day period (1,980
total top-10 slots). The 10 most-frequently-selected symbols account for only **11.9%**
of all top-10 slots (well under the 50% concentration threshold) -- the model is not
repeatedly recommending a small fixed set of names.

## Regime and ADV-quintile breakdown (daily top-10% lift)

| spy_trend x spy_volatility_bucket | n | Lift |
|---|---|---|
| Bull_Normal | 409,096 | 1.43 |
| Bull_Low | 36,958 | 1.40 |
| Bear_Normal | 7,550 | 1.34 |
| Bear_High | 22,626 | 1.25 |
| Bull_High | 17,421 | 1.15 |

| ADV quintile | n | Lift |
|---|---|---|
| adv_q1 (smallest) | 78,109 | 1.63 |
| adv_q2 | 80,178 | 1.40 |
| adv_q3 | 93,959 | 1.32 |
| adv_q4 | 104,104 | 1.26 |
| adv_q5 (largest) | 137,301 | **1.01** |

Every regime cell clears lift > 1. The ADV breakdown reproduces EXP-001's structural
finding out-of-sample: edge is strongest in the smallest-ADV quintile and nearly
vanishes (lift ~1.01, essentially no edge) in the largest/most liquid quintile. This
has direct capacity/tradability implications for any future deployment -- it is not a
free-floating edge across all liquidity levels.

## Calibration (diagnostic only, no calibrator fit)

Brier score 0.132, calibration slope 0.544, intercept -0.441, ECE 6.2%. This is
materially worse than EXP-002's validation calibration (slope 0.85, ECE 1.6%) --
predicted probabilities should **not** be treated as trustworthy out-of-sample
probability estimates. Ranking (the metric this Locked Test is actually about) is far
less sensitive to this than a probability-dependent use (e.g. position sizing by raw
predicted probability) would be.

## MFE / MAE diagnostic

Top-10 daily picks: mean 20-day MFE +137.8%, mean 20-day MAE -23.8%. Population mean:
MFE +12.3%, MAE -9.2%. Selected candidates have much larger favorable excursions but
also larger adverse excursions than the population average -- consistent with the
model concentrating on smaller, higher-volatility names (matches the ADV-quintile
finding above). This is a real risk/reward trade-off to carry into any future
paper-trading or position-sizing work, not a one-sided free lunch.

## Conclusion

Model 2 passes the Locked Test on every pre-registered condition, several with a
comfortable margin. The result replicates EXP-002's validation finding that ranking
value is genuine and not merely a market-timing artifact -- and, on locked_test,
context and stock selection both independently carry positive lift, which is if
anything a stronger, more complete result than validation showed. Performance is
somewhat lower than validation's (top-10-symbol mean lift 2.74 vs. validation's 3.71),
which is the expected, healthy pattern for a genuine out-of-sample check rather than a
red flag. Two real caveats travel forward with this PASS: calibration degrades
out-of-sample, and the edge is concentrated in smaller/less-liquid names and is nearly
absent in the largest-ADV quintile.

## Follow-Up (per instruction: no retraining, revision, or promotion in this cycle)

- This experiment does not implement production recommendation generation or
  portfolio logic. That is explicitly out of scope for this cycle.
- A successful Locked Test does not mean the model is ready for real-money decisions.
  The suggested next phase (not yet started) is transaction-cost modeling, MFE/MAE-
  aware position management, and a paper-trading validation, per the research lead's
  framing.
- Locked_test is not reopened in this project to try a different feature subset. Any
  future revision to Model 2 (e.g. addressing the weak `is_bear_x_vol_high` /
  middle-RVOL-decile terms, or the ADV-quintile-5 capacity limit) would constitute a
  new model requiring its own validation cycle and, if warranted, its own new Locked
  Test population.

## Notes

- The plumbing bug fixed during this run (`pd.concat` duplicate-index crash in
  `check_not_estimable_interactions`) was caught immediately by a traceback on the
  first run attempt, before any results were computed or observed -- no locked_test
  metric was seen, discarded, or influenced by this fix.
- Generated reports and feature datasets (`artifacts/swing_20_locked_test_report.json`,
  `artifacts/swing_20_features_locked_test/`) are not committed to git; this document
  is the durable, version-controlled record.
