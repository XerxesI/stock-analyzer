# Experiment Record

## Experiment ID

```text
EXP-002
```

## Title

SWING_20 auditable Logistic Regression baseline (Model 0/1/2) -- corrected daily
cross-sectional evaluation, context-timing vs. stock-selection decomposition, and
temporal/calibration robustness checks.

## Date

```text
2026-07-18
```

## Owner

Claude (agent), instructions relayed from ChatGPT (research lead) via Meelis Kivimäe.

## Related Research Question

Given EXP-001's Research Registry decisions (MF1/VC3 rejected for SWING_20; H1 rsi_14
reframed as regime/size-conditional; H2 rvol_20 confirmed as a stable U-shape; H3 the
elevated Bear hit-rate reframed as a spy_trend x spy_volatility_bucket interaction),
does an auditable Logistic Regression baseline that encodes those findings add real
predictive value over the unconditional base rate -- and is that value genuine
within-day stock selection, or mostly market-timing (picking good dates)?

## Related ADRs

- ADR-005-BlockBootstrap.md -- same-date dependence; motivated both the Fama-MacBeth
  daily IC in EXP-001 and the date-level coefficient resampling / date-block bootstrap
  CIs in this experiment.

## Related Experiments

- EXP-001_SWING20_Feature_Replication_and_Context_Mechanics.md

## Commit

```text
branch: master
```

## Dataset

```text
dataset_name: swing_20
dataset_version: swing20_20260718T135238Z
feature dataset: artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet
date_range: 2022-07-14 .. 2025-09-03
train: 1,206,131 rows, 591 dates (2022-07-14 .. 2024-11-15)
validation: 448,905 rows, 197 dates (2024-11-18 .. 2025-09-03)
```

Locked_test was never read in this experiment.

## Hypothesis

A prior run of this baseline (v1, superseded) evaluated precision@k and lift@k by
ranking ALL validation rows globally, pooled across the whole period. That measures
how well the model picks good DATES as much as how well it picks good STOCKS within a
date -- and SWING_20's real usage is a **daily** cross-sectional ranking system: rank
today's eligible symbols, take the top N. This experiment reworks the evaluation to
match that usage and answers: does Model 2's apparent lift survive when measured
correctly, and how much of it is market-timing versus genuine stock selection?

## Metrics

Primary:

- **Daily cross-sectional precision@k / lift@k** -- for each date independently, rank
  only that date's eligible symbols, compute precision against that date's own base
  rate, aggregate across dates (mean/median, date-block-bootstrap 95% CI, fraction of
  dates with lift > 1, fraction of dates with at least one positive in the selected
  set).

Secondary / diagnostic:

- Global (pooled) precision@k/lift@k -- retained only as a comparison point, not for
  model selection.
- Context-only score (date-constant, from spy_trend/spy_volatility_bucket terms only)
  and stock-only score (full logit minus the context part), each evaluated globally
  and daily, to decompose how much of the global lift is market-timing vs. stock
  selection.
- ROC-AUC, PR-AUC (diagnostics only).
- Brier score, calibration intercept/slope, expected calibration error (ECE) --
  descriptive only; no calibrator was fit or applied to reshape predictions.
- Date-level (not row-level) coefficient resampling: median, 5th/95th percentile,
  sign-consistency fraction, 20 resamples of 70% of train's dates.

## Results

### Correction from v1

v1's global ranking is retained in this report as `A_*_global_DIAGNOSTIC_ONLY`, but is
no longer used for any decision. `is_bear_x_vol_low` is structurally absent (Bear
periods never have Low SPY volatility in this dataset -- 0 of 1,206,131 train rows, 0
of 448,905 validation rows) and has been removed from the fitted feature matrix
entirely, rather than reported as a "stable" zero coefficient as v1 did.

### Model 0 (intercept-only)

Reference point: daily precision@k equals each date's own base rate and
`mean_daily_lift = 1.0` by construction (no ranking ability).

### Model 1 (log_adv20 + spy_trend + spy_volatility_bucket + interaction)

| Metric (validation) | Value |
|---|---|
| Daily precision@10%, mean lift | 1.36 (median 1.33, 95% CI [1.25, 1.48]) |
| Daily top-10-symbols, mean lift | 1.44 (median 1.09, 95% CI [1.14, 1.67]) |
| Fraction of dates with lift > 1 (top-10 symbols) | 53.8% |
| Fraction of dates with >=1 positive in top-10 | 80.2% |
| Global top-10%, full model (A) | lift 1.77 |
| Global top-10%, context-only (C) | lift **2.22** |
| Global top-10%, stock-only (D) | lift 1.25 |

Model 1's global lift is driven **predominantly by context** (C > A > D): its only
stock-varying term is `log_adv20`, a slow-moving, mostly static-per-symbol
characteristic, so it has little genuine within-day discrimination power. This is
exactly the failure mode ChatGPT's review flagged as a risk -- confirmed here for
Model 1 specifically.

### Model 2 (Model 1 + RVOL deciles + RSI + RSI interactions)

| Metric (validation) | Value |
|---|---|
| Daily precision@10%, mean lift | 1.82 (median 1.73, 95% CI [1.64, 2.03]) |
| Daily top-10-symbols, mean lift | **3.71** (median 2.99, 95% CI [2.70, 4.79]) |
| Fraction of dates with lift > 1 (top-10 symbols) | 90.9% |
| Fraction of dates with >=1 positive in top-10 | 98.0% |
| Global top-10%, full model (A) | lift 1.89 |
| Global top-10%, context-only (C) | lift **0.85** |
| Global top-10%, stock-only (D) | lift **1.77** |

Model 2's decomposition inverts Model 1's pattern: stock-only (D) lift is close to and
at top-1% actually *exceeds* the full model's (D=2.59 vs A=2.44 at top-1%), while
context-only (C) lift is *below* 1 at every k tested (0.82-0.86). Essentially all of
Model 2's apparent lift comes from genuine within-day stock selection, not from
picking good market days.

(Note on why C's lift is below 1, not just "small": Model 2's fitted coefficients
place `Bear_Normal`'s context score fractionally above `Bear_High`'s, due to a
negative Bear-x-High interaction coefficient. `Bear_Normal` alone has more validation
rows than the top-10% budget, so the global top-10%-by-context-only selection is drawn
entirely from `Bear_Normal`, whose own average hit-rate (~9.9%) happens to sit *below*
the overall validation base rate (11.6%) -- even though `Bear_High` individually has a
much higher realized rate (~25-30%). This is a real artifact of pooled percentage-based
ranking at a specific cutoff, not a sign that context is unhelpful; it is exactly the
kind of distortion the daily/global split was built to catch.)

### Temporal robustness (validation, 4 pre-declared equal-day-count chronological blocks)

| Block | Dates | Daily precision@10% lift | Daily top-10-symbols lift | Brier |
|---|---|---|---|---|
| 0 | 2024-11-18 .. 2025-01-31 (50) | 1.93 | 4.29 (100% days had a positive) | 0.082 |
| 1 | 2025-02-03 .. 2025-04-11 (49) | 2.18 | 5.50 (98% days) | 0.077 |
| 2 | 2025-04-14 .. 2025-06-24 (49) | 1.58 | 2.53 (100% days) | 0.135 |
| 3 | 2025-06-25 .. 2025-09-03 (49) | 1.61 | 2.51 (94% days) | 0.112 |

Every block's lift is comfortably above 1 with a 95% CI that does not cross 1 -- no
single sub-period is driving the overall result.

### Calibration (Model 2, validation)

Brier score 0.101, calibration slope 0.85, intercept -0.145, ECE 1.6%. Mild
under-confidence at the extremes (slope < 1) but a small overall error -- ranking
performance (the primary MVP use case) does not depend on the predictions being
well-calibrated probabilities, but the output should not yet be described as a
trustworthy probability estimate without further calibration work.

### Coefficient stability (date-level resampling, 20 resamples of 70% of train's dates)

With dependence-aware (whole-date) resampling, the strongest terms remain fully stable
(`sign_consistency = 1.00`): `is_bear`, `log_adv20_z`, `rsi_14_z`, `rsi_14_z_x_bear`,
`rsi_14_z_x_low_adv`, `rvol_d1`, `rvol_d10`. The weakest terms now correctly show
genuine uncertainty rather than false stability: `rvol_d6`/`rvol_d7` (near the flat
minimum of the U-shape, sign_consistency 0.80/0.55), `is_bear_x_vol_high`
(sign_consistency 0.80, 90% interval crosses zero). `is_bear_x_vol_low` is marked
`NOT_ESTIMABLE` and excluded from the fitted matrix.

## Artifacts

- report: `artifacts/swing_20_logistic_baseline_report.json` (generated, not
  committed)
- feature dataset: `artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet`
  (generated, not committed)
- code (committed):
  - `scripts/train_swing_20_logistic_baseline.py`
  - `tests/test_swing20_logistic_baseline.py`

### Reproduction command

```bash
python scripts/train_swing_20_logistic_baseline.py \
    --features-path artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet \
    --output-json artifacts/swing_20_logistic_baseline_report.json
```

## Decision

```text
ACCEPT (Model 2 corrected evaluation; still not promoted, Locked Test not opened)
```

### Per-model decision

| Model | Decision | Reason |
|---|---|---|
| Model 0 (intercept-only) | Reference | Behaves exactly as designed (lift = 1.0 by construction); not a candidate. |
| Model 1 (context/control) | **REFRAME** | Real lift over Model 0, but the decomposition shows it is mostly market-timing (context-only global lift 2.22 > full model's 1.77 > stock-only 1.25). Useful as a context baseline and as the C-vs-A-vs-D reference point, but should not be read as evidence of stock-selection skill on its own. |
| Model 2 (research features) | **PASS** | Daily cross-sectional lift is substantially and consistently above 1 (mean 1.82 at precision@10%, mean 3.71 at top-10 symbols), holds across all 4 pre-declared temporal blocks, and the context/stock decomposition shows the lift is predominantly genuine stock selection (D >= A globally; C < 1 globally). This directly answers the concern raised about EXP-002 v1: the lift is not primarily an artifact of selecting high-base-rate dates. |

### Recommendation on Locked Test readiness

Model 2 has cleared the specific methodological concern that blocked it after v1 (lift
attributable to stock selection, not date selection, confirmed via decomposition and
temporal blocks). Given that, plus stable date-level coefficient signs for its primary
terms, it is a reasonable candidate for **one** final Locked Test evaluation --
recommended, not decided here. Two caveats should be weighed before that step: (1)
calibration is decent but not excellent (slope 0.85), so any Locked Test evaluation
should continue to lead with ranking metrics (precision@k, lift@k) rather than treat
the output as a calibrated probability; (2) `is_bear_x_vol_high` and the middle RVOL
deciles are the least stable terms under date-level resampling and contribute little
individually -- their presence is not expected to change the ranking materially, but
this should be watched if the model is later revised.

Per instruction, the Locked Test is not opened and the model is not promoted in this
experiment.

## Conclusion

The v1 global-ranking evaluation overstated how directly Model 2's lift translates to
SWING_20's actual daily-ranking use case, and could not distinguish market-timing from
stock-selection value. The corrected daily cross-sectional evaluation, decomposition,
and temporal-block checks show Model 2's value is real, stable across roughly a year
of validation dates, and predominantly comes from within-day stock selection --
addressing the central concern raised in review. Model 1, by contrast, is now shown to
be mostly a market-timing model, which is itself a useful, previously-unstated
finding.

## Follow-Up

- Await sign-off before any Locked Test evaluation of Model 2.
- If a Locked Test evaluation is later approved, use the same daily cross-sectional
  primary metric (not global pooled ranking) and pre-declare the exact metrics to be
  reported before opening it.
- Consider dropping or simplifying `is_bear_x_vol_high` given its weak date-level
  stability, as a future (not yet executed) revision -- would require a new
  pre-declaration and Locked Test would still need to stay closed until then.
- Calibration could be improved with a properly out-of-fold or temporally-split
  calibrator fit inside train only, if a calibrated probability (not just a ranking)
  is later required by a downstream consumer.

## Notes

- Coefficient resampling was changed from row-level (v1) to whole-date resampling in
  this experiment, per ADR-005's same-date-dependence concern -- the row-level
  version's `resample_std_coef` values in v1 were optimistically narrow.
- No calibrator was fit anywhere in this experiment; the calibration section reports
  descriptive diagnostics of already-fixed predictions only, computed separately on
  train and validation (not transferred between them).
- All bucket edges (ADV quintiles, RVOL deciles) and standardization parameters are
  fit on train only, reusing the same helpers as EXP-001's Context and Target
  Mechanics analysis.
- Generated reports and feature datasets are not committed to git; this document is
  the durable, version-controlled record.
