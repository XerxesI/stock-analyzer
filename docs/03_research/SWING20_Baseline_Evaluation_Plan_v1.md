# SWING_20 Initial Baseline Evaluation Plan v1 — Draft

**Status:** DRAFT (plan only — no baseline has been run, no locked-test data has been touched)
**Depends on:** `docs/02_mvp/SWING20_Dataset_Specification_v1.md` (frozen),
`docs/03_research/SWING20_PointInTime_Feature_Specification_v1.md` (draft)

---

## 0. Purpose

Before any gradient-boosted model, answer a narrower question first:

> Do validated signals and market/sector context produce a better daily Top-N stock selection
> than random or naive momentum selection?

If the answer is no, there is no reason to add model complexity — a complex model cannot
manufacture edge that a simple composite doesn't already show. This plan defines the
baselines, metrics, and comparison protocol used to answer that question on train/validation
only. The locked-test split is not inspected here.

---

## 1. Baselines, in Evaluation Order

### 1.1 Random Ranking

- **Definition:** on each eligible date, draw a random permutation of the eligible universe
  and take the top N.
- **Purpose:** establishes the floor. Every other baseline must beat this by a margin large
  enough to not be attributable to noise (see uncertainty, section 4).
- **Implementation note:** use a fixed seed per evaluation run for reproducibility, but report
  results averaged over multiple seeds (suggest 20) to characterize sampling variance
  separately from model variance.

### 1.2 Naive Momentum Ranking

- **Definition:** on each eligible date, rank by `return_20d` (see Feature Specification 1.1)
  descending, take the top N.
- **Purpose:** the cheapest non-random baseline — if validated signals and context can't beat
  plain momentum, they are not earning their complexity.
- **No fitting:** this baseline has no parameters to fit; it can be evaluated on train,
  validation, and (later) locked test without any train/validation split concern.

### 1.3 Validated-Signal Composite

- **Definition:** a fixed, non-learned combination of the signals already validated in prior
  research cycles and confirmed to replicate under the SWING_20 label
  (candidates: `rvol_20`/MF1, `compression_pct_100`/VC3 component, `c1_composite` if its
  extraction prerequisite is complete — see Feature Specification 1.5). Combination rule
  (e.g. equal-weight z-score sum, or regime-conditional switching per each signal's own prior
  validated regime) is fixed **before** looking at validation results, not tuned against them.
- **Purpose:** tests whether prior research's validated edges, combined, produce a usable daily
  ranking — without yet introducing a learned model that could overfit the combination weights.
- **Fitting:** the combination rule's fixed weights are a design decision made from prior
  research, not fit on this dataset's train split. If a decision is needed (e.g. which regime
  each signal applies in), it is documented as a decision here, not discovered by search.

### 1.4 Logistic Regression

- **Definition:** L2-regularized logistic regression on the stock-level and market-context
  features that passed their fail-fast criterion in the Feature Specification, predicting
  `target_20pct_20d`.
- **Purpose:** per `docs/02_mvp/MVP_1_Specification.md` section 22.1 — verify pipeline
  correctness, detect simple linear signal, check scaling and class-imbalance handling, and
  establish a transparent, inspectable baseline before any gradient-boosted model.
- **Fitting:** fit on train only; hyperparameters (regularization strength) selected on
  validation only. No feature selection informed by validation performance beyond the
  fail-fast gate already applied upstream in the Feature Specification.

Gradient boosting (LightGBM/XGBoost/CatBoost) is explicitly **out of scope** for this plan,
per `docs/02_mvp/MVP_1_Specification.md` section 2.2 and the instruction not to jump to it
before these four baselines are compared.

---

## 2. Primary Evaluation Metrics

All metrics are computed identically across all four baselines, using the same universe,
dates, entry price, label, deduplication logic, and temporal splits (frozen in
`SWING20_Dataset_Specification_v1.md`).

| Metric | Definition | Why it matters here |
|---|---|---|
| `precision@k` | Fraction of the daily Top-`k` selections that hit `target_20pct_20d` | Direct measure of daily selection quality; `k` suggested at 3 and 10 to match both a tight "top pick" and a broader shortlist |
| `lift_vs_random` | `precision@k(baseline) / precision@k(random)` | Normalizes for how rare positives are on a given date; a baseline that isn't better than random by a meaningful multiple isn't worth building on |
| `daily_topN_success_rate` | Fraction of dates where at least one Top-N pick hits target | The practical, single-user-facing question: "on a day I check the recommendation, does at least one pick work out" |
| `deduplicated_event_capture` | Fraction of deduplicated positive events (per Dataset Specification section 7) that the baseline's Top-N selections capture on the event's first eligible signal date | Prevents a baseline from looking good only by repeatedly flagging the same already-known move across its whole overlapping window |
| `MFE` | Distribution of maximum favorable excursion for selected picks (median, p25, p75) | Upside profile beyond the binary hit/miss |
| `MAE` | Distribution of maximum adverse excursion for selected picks | Downside/risk profile — a baseline with good precision but catastrophic MAE tails is not actually usable |
| `turnover` | Day-over-day change in the Top-N selection set | High turnover raises real transaction costs even if precision looks good |
| `estimated_costs` | Turnover × an assumed round-trip cost estimate (spread + slippage placeholder, to be parameterized, not a market microstructure model) | Converts turnover into a comparable cost figure; deliberately approximate — this is not a costs research project |
| `temporal_stability` | `precision@k` and `lift_vs_random`, broken out by split (train/validation) and by `spy_trend`/`spy_volatility_bucket` regime | A baseline that only works in a specific regime or period is not a robust baseline — report the breakdown, don't average it away |

Secondary/diagnostic metrics (not decision-driving on their own): PR-AUC and calibration for
the Logistic Regression baseline only (the non-learned baselines don't produce a
probability to calibrate).

---

## 3. Comparison Protocol

1. Compute all metrics for all four baselines on **train** first, as a sanity check (bugs
   show up here, not as "signal").
2. Compute all metrics on **validation** — this is the actual comparison basis.
3. Rank baselines by `lift_vs_random` and `daily_topN_success_rate` on validation, with
   `temporal_stability` as a disqualifying check: a baseline with strong average metrics but a
   regime or sub-period where it performs at or below random is flagged, not averaged past.
4. Document the comparison in a dated experiment record under `docs/09_experiments/`
   (using `Experiment_Template.md`), not only in this plan document.

**Locked test is not touched in this phase.** Section 23 of `MVP_1_Specification.md` governs
when and how it is eventually used, after one configuration is frozen — not during baseline
comparison.

---

## 4. Uncertainty

Because same-date selections are not independent (shared market/sector exposure) and 20-day
outcome windows overlap, per-observation confidence intervals would understate real
uncertainty. Per `MVP_1_Specification.md` section 24.4, use calendar-time block bootstrap for
`precision@k` and `lift_vs_random`, with block length fixed on validation before any
locked-test use. Random-ranking baseline results are additionally averaged over multiple
random seeds (section 1.1) to separate sampling noise from the block-bootstrap's estimate of
temporal dependence.

---

## 5. Fail-Fast Gate for This Phase

Consistent with the Feature Specification's fail-fast principle: if the Validated-Signal
Composite (1.3) does not beat Naive Momentum (1.2) by a margin larger than its block-bootstrap
uncertainty interval, do not proceed to tuning Logistic Regression feature sets in search of a
result — stop and report that prior signals, at least in this combination, do not transfer to
the SWING_20 label. That finding is a valid, useful outcome of this phase, not a failure to
paper over.

---

## 6. Explicit Non-Goals for This Phase

- No LightGBM/XGBoost/CatBoost.
- No probability calibration beyond what Logistic Regression naturally produces (and even
  that is diagnostic only, not decision-driving).
- No locked-test inspection.
- No feature engineering beyond what is already listed in the Feature Specification's
  fail-fast-cleared set.
