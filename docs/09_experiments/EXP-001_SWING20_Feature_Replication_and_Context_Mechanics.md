# Experiment Record

## Experiment ID

```text
EXP-001
```

## Title

SWING_20 point-in-time feature replication (MF1/VC3 transfer test) and follow-up
Context and Target Mechanics research cycle (H1 RSI robustness, H2 RVOL shape, H3
Bear baseline mechanics).

## Date

```text
2026-07-18
```

## Owner

Claude (agent), instructions relayed from ChatGPT (research lead) via Meelis Kivimäe.

## Related Research Question

Do the previously validated MF1 (`rvol_20`) and VC3 (`compression_pct_100`) effects
transfer to the frozen SWING_20 binary label (+20% within 20 trading days, next-day-Open
entry)? If not, what does the frozen SWING_20 population actually reward, and is that
evidence sufficient to justify a first auditable Logistic Regression baseline?

## Related ADRs

- ADR-005-BlockBootstrap.md (same-date cross-sectional dependence concern; motivated
  using Fama-MacBeth daily cross-sectional IC instead of pooled correlation here)

## Commit

```text
commit_sha: (this experiment's code committed separately -- see repo history for
             stock_analyzer/datasets/swing_20/features.py and the three analysis
             scripts listed under Artifacts below)
branch: master
```

## Dataset

```text
dataset_name: swing_20
dataset_version: swing20_20260718T135238Z
date_range: 2022-07-14 .. 2025-09-03 (train+validation feature dataset)
universe: full US common-stock universe, 5,370 symbols in the frozen snapshot;
          3,347 symbols present in the generated train+validation feature dataset
          (remaining symbols have zero primary-population rows in train/validation
          after quarantine and target-already-reached-at-entry exclusion)
label_version: SWING_20 (target_20pct_20d), next-day Open entry
feature_set_version: SWING20_PointInTime_Feature_Specification_v1, replication-pass
                      subset only (return_5d, return_20d, rsi_14, rvol_20,
                      compression_pct_100, adv20, spy_trend, spy_volatility_bucket)
```

Locked_test was never read in either cycle of this experiment.

## Hypothesis

Cycle 1 (feature replication): MF1 and VC3, validated in an earlier research context,
transfer as-is to the frozen SWING_20 label.

Cycle 2 (Context and Target Mechanics, pre-registered after Cycle 1's results):

- **H1**: Lower `rsi_14` is associated with a higher SWING_20 probability after
  stratifying for `log_adv20`, `spy_trend`, and `spy_volatility_bucket`.
- **H2**: The relationship between `rvol_20` and SWING_20 is non-linear/U-shaped
  rather than simple negative-monotonic.
- **H3**: The higher Bear-regime SWING_20 hit-rate is primarily explained by
  volatility/universe-composition (ADV) differences rather than `spy_trend` alone.

## Metrics

Primary:

- Fama-MacBeth daily cross-sectional Spearman IC (rank-based Pearson on ranks,
  computed per date, then averaged -- avoids importing scipy and respects same-date
  dependence per ADR-005)
- Hit-rate (positive_rate) by pre-declared quantile/stratum, with sample size

Secondary:

- t-statistic of the daily IC series (mean / (std / sqrt(n_days)))

## Results

### Cycle 1 — Feature replication (MF1 / VC3)

**MF1 (`rvol_20`)**: stable, statistically significant, but **sign-reversed** from the
prior validated direction. Pooled daily IC vs. binary target = -0.021 (t=-11.0); holds
in both train (t=-8.4) and validation (t=-8.0), and in both Bull (t=-11.7) and Bear
(t=-2.7). Quintile hit-rate is U-shaped, not monotonic (Q1 12.9%, Q2-Q4 ~9.4-9.8%, Q5
11.2%) -- later shown by H2 to be a genuine U-shape, not noise.

**VC3 (`compression_pct_100`)**: standalone effect not stable across splits (train
t=-3.30, validation t=+0.56, sign flips). Pre-declared interaction
(`compression_pct_100 <= 0.20 AND rvol_20 > 1.0`, n=220,892) gives lift=0.93 (mild
underperformance, not the expected outperformance), stable across train (9.79%) and
validation (9.90%), i.e. a stable *lack* of edge.

### Cycle 2 — Context and Target Mechanics (H1 / H2 / H3)

All quantile/tercile/decile bin edges (ADV quintiles, RSI terciles, RVOL
deciles/terciles) were fit on the **train split only** and applied unmodified to
validation. An earlier version of the analysis script fit ADV/RSI edges on the
train+validation union and fit RVOL edges independently per split; this was corrected
before the results below (numbers changed only marginally -- ADV/RVOL/RSI
distributions are stable over time in this dataset).

**H1 (RSI robustness) -- REFRAME.** In train, the negative `rsi_14` IC is broad and
significant across every ADV quintile (t between -9.1 and -13.1). In validation it
survives clearly only in `adv_q1` (smallest ADV, t=-9.8); `adv_q2..adv_q5` are not
significant (|t|<1.5), and `adv_q5` even flips sign (not significant). By regime,
validation shows the effect surviving in Bear (`Bear_High` t=-4.8, `Bear_Normal`
t=-2.9) but not in Bull (`Bull_Normal`, validation's largest regime bucket at 125 days,
t=-0.87; `Bull_Low` t=+0.32). Conclusion: the RSI effect is real in train but is
concentrated in Bear regime and/or the smallest-ADV names in validation, not a
universal effect -- pooled hit-rate tables looked cleaner only because they pool
same-date rows and understate ADR-005's dependence concern.

**H2 (RVOL shape) -- CONTINUE.** Decile hit-rate table shows a stable, clearly
non-monotonic U-shape in both train and validation (train: d1=14.3% -> d5/d6≈9.3% (min)
-> d10=11.3%; validation: d1=17.8% -> d5/d6≈9.8% (min) -> d10=14.1%). The interaction
with `return_5d` sign shows "recent decline" (return_5d < 0) has a higher hit-rate than
"no recent decline" within every RVOL tercile, in both splits -- an additive effect,
not an explanation of the U-shape. MF1's original rejection (as a simple negative
monotonic signal) stands; the U-shape itself is a stable, validated new finding.

**H3 (Bear baseline mechanics) -- REFRAME (corrected).** An earlier draft of this
report claimed the Bull/Bear hit-rate gap "persists in every matched ADV x volatility
cell" based on a train+validation-combined table. That claim was wrong: the
**validation-only** per-split table shows the gap is concentrated in **High**
volatility and is absent or reversed in **Normal** volatility:

| ADV quintile | Normal: Bear vs Bull (validation) | High: Bear vs Bull (validation) |
|---|---|---|
| adv_q1 | 12.5% vs 14.6% | 28.8% vs 13.6% |
| adv_q2 | 11.8% vs 12.1% | 32.8% vs 9.3% |
| adv_q3 | 8.6% vs 10.7% | 28.3% vs 9.6% |
| adv_q4 | 8.2% vs 8.7% | 24.8% vs 8.5% |
| adv_q5 | 8.3% vs 8.4% | 26.3% vs 6.8% |

In every ADV quintile, Bear <= Bull in Normal volatility, while Bear >> Bull in High
volatility. Corrected conclusion: the elevated Bear-regime hit-rate is a `spy_trend x
spy_volatility_bucket` **interaction**, not a universal, volatility-independent Bear
premium. Any model must include this interaction explicitly rather than a standalone
Bear coefficient.

## Artifacts

- report (Cycle 1): `artifacts/swing_20_feature_replication_report.json` (generated,
  not committed)
- report (Cycle 2, corrected): `artifacts/swing_20_context_target_mechanics_report.json`
  (generated, not committed)
- feature dataset used: `artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet`
  (generated, not committed; 1,655,036 rows, 3,347 symbols, train+validation only)
- code (committed):
  - `stock_analyzer/datasets/swing_20/features.py`
  - `scripts/build_swing_20_feature_dataset.py`
  - `scripts/analyze_swing_20_feature_replication.py`
  - `scripts/analyze_swing_20_context_target_mechanics.py`
  - `tests/test_swing20_features.py`

### Reproduction commands

```bash
# 1. Build the point-in-time feature dataset (train+validation only, locked_test never touched)
python scripts/build_swing_20_feature_dataset.py \
    --dataset-dir artifacts/swing_20/snapshots/swing20_20260718T135238Z \
    --progress-every 200

# 2. Cycle 1 -- MF1/VC3 replication report (use the snapshot version step 1 just printed,
#    e.g. swing20_features_20260718T165654Z for this experiment's run)
python scripts/analyze_swing_20_feature_replication.py \
    --features-path artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet \
    --output-json artifacts/swing_20_feature_replication_report.json

# 3. Cycle 2 -- Context and Target Mechanics report (H1/H2/H3)
python scripts/analyze_swing_20_context_target_mechanics.py \
    --features-path artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet \
    --output-json artifacts/swing_20_context_target_mechanics_report.json
```

## Decision

```text
ACCEPT (as a negative/redirecting result -- see Research Registry decisions below)
```

### Research Registry decisions

| Item | Status | Note |
|---|---|---|
| MF1 (`rvol_20`), original positive direction | REJECTED_FOR_SWING_20 | Does not transfer; sign is reversed and stable. Historical validation record preserved, applicability scope restricted to its original context. |
| VC3 standalone (`compression_pct_100`) | REJECTED_FOR_SWING_20 | Not stable across train/validation. |
| VC3 + RVOL pre-declared interaction | REJECTED_FOR_SWING_20 | Lift=0.93, stable but wrong direction. |
| `rvol_20` reversed-sign / U-shape | NEW_HYPOTHESIS_VALIDATED (H2) | Genuine, stable, non-monotonic shape confirmed in Cycle 2. Not a directional trading signal by itself; must be encoded non-linearly in any model (train-fit buckets, not a raw linear term). |
| `rsi_14` | PROMISING_EXPLORATORY, REFRAMED (H1) | Real in train; in validation concentrated in Bear regime and/or smallest-ADV quintile, not universal. Candidate model terms: `rsi_14`, `rsi_14 x Bear`, optionally `rsi_14 x low_ADV`. |
| `log_adv20` | REQUIRED_CONTROL_AND_STRATIFICATION | Strongest single IC in the dataset (t~-48.7 pooled); reflects a real structural liquidity effect, not just a nuisance confound, but must not be allowed to dominate the model into a trivial "pick illiquid names" strategy. Must not be used to change the frozen universe or eligibility rules -- any new ADV floor requires a new dataset contract version and a new audit. |
| Bear-regime elevated hit-rate | REFRAMED (H3, corrected) | `spy_trend x spy_volatility_bucket` interaction, concentrated in High volatility; does not replicate as a standalone Bear premium in Normal volatility. Any model must include the interaction term, not a standalone trend coefficient. |

## Conclusion

Both prior-validated effects (MF1, VC3) fail to transfer to the frozen SWING_20 label
-- this is a genuine business-target transfer failure, not an implementation defect
(lineage checks passed, a small deterministic sample matched full-population math
before scaling, and the corrected train-only-fit rerun changed H1/H2 numbers only
marginally). The mechanics cycle turned that negative result into three usable,
falsifiable findings: RVOL's relationship is a stable U-shape (H2), RSI's edge is
regime/size-conditional rather than universal (H1), and the market-regime effect on
the label is a trend-x-volatility interaction rather than a standalone trend effect
(H3, corrected after an initial overstated claim was caught and fixed before commit).

## Follow-Up

Proceed to an auditable (not yet promotable) Logistic Regression baseline with three
pre-declared models (Model 0: intercept-only; Model 1: `log_adv20` + `spy_trend` +
`spy_volatility_bucket` + their interaction; Model 2: Model 1 + train-fit non-linear
`rvol_20` + `rsi_14` + `rsi_14 x Bear` + optional `rsi_14 x low_ADV`). All
preprocessing fit on train only, same frozen split, locked_test untouched. Report
precision@k, lift@k, success rate, and calibration (by regime/volatility/ADV quintile)
as primary metrics, ROC-AUC/PR-AUC as diagnostics only. Stop after the validation
report; do not promote a model or open locked_test in that cycle.

## Notes

- All stratification cut points in Cycle 2 (ADV quintiles, RSI terciles, RVOL
  deciles/terciles) are fit on train only and applied unmodified to validation --
  verified explicitly after ChatGPT flagged that an earlier draft fit some edges on
  the train+validation union and fit others independently per split.
- An earlier draft of this document's H3 finding stated the Bear/Bull gap "persists in
  every matched cell," based on a train+validation-combined table. This was caught in
  review (ChatGPT) and corrected before commit using the validation-only per-split
  table, which shows the gap is a trend x volatility interaction, not a universal
  trend effect. The train+validation-combined table is retained in the JSON report
  only as a labeled `_REFERENCE_ONLY` field and must not be used alone to answer H3.
- Generated feature datasets and JSON reports are not committed to git; this document
  is the durable, version-controlled record of what was found and why, per repository
  policy of not committing generated artifacts.
