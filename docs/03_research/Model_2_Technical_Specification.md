# Model 2 Technical Specification

**Status:** FROZEN ORIGINAL SPECIFICATION (Sections 1-12) + POST-HOC EMPIRICAL FINDINGS
(Section 13), clearly separated. This document does not redefine Model 2 -- it
consolidates four views that previously lived in separate places (the frozen
implementation, the validation/Locked Test methodology, the sandbox inference/ranking
process, and the economic limitations EXP-004/EXP-005 later exposed) into one
authoritative reference, so Model 3 (or any future model) can be designed and compared
against a fixed baseline instead of chat history.

**Date:** 2026-07-22
**Owner:** Claude (agent), instructions relayed from ChatGPT (research lead) via Meelis
Kivimäe.

**Authoritative sources** (every date, row count, coefficient, hash, and metric below is
taken programmatically from these, not from rounded conversational figures):

- `scripts/train_swing_20_logistic_baseline.py` (the frozen implementation)
- `stock_analyzer/sandbox/infrastructure/model2_prediction_adapter.py` (the production
  inference adapter)
- `docs/09_experiments/EXP-002_SWING20_Logistic_Baseline.md` (validation methodology and
  result)
- `docs/09_experiments/EXP-003_SWING20_Locked_Test.md` (Locked Test pre-registration and
  result)
- `docs/02_mvp/SWING20_Dataset_Specification_v1.md` (frozen label/universe/split
  definitions)
- `docs/03_research/SWING20_PointInTime_Feature_Specification_v1.md` (feature candidate
  definitions)
- `docs/09_experiments/EXP-004_Sandbox_Historical_Replay.md`,
  `docs/09_experiments/EXP-005_Portfolio_Policy_Feasibility_Pilot.md` +
  `EXP-005_Stage15_Completion_Report.md` (end-to-end economic results)
- `artifacts/sandbox/model2_diagnostics/shadow_top10_post_hoc/shadow_top10_summary.json`
  (new post-hoc diagnostic reproduced for this document -- see Section 13)
- `artifacts/sandbox/exp005/real_runs/exp005_real_variant_b_2024_11_2025_10/` (the real
  Variant B run's manifest, replay database, and price-path study)

---

## 1. Identity and Status

```text
Name:               SWING_20 Model 2 ("research features" model)
Model version:      swing20_model2@8857532adf518206cecc8c901866a128c9d170cf
                     (stock_analyzer/sandbox/config.py:MODEL_VERSION)
Algorithm:          sklearn.linear_model.LogisticRegression
                     (NOT LightGBM, NOT any gradient-boosted tree -- see Section 7)
Frozen commit:      8857532adf518206cecc8c901866a128c9d170cf
                     (scripts/train_swing_20_logistic_baseline.py,
                      tests/test_swing20_logistic_baseline.py --
                      per EXP-003 Part 1 Section 1)
Authoritative entry points:
  - Training/evaluation: scripts/train_swing_20_logistic_baseline.py
                          (fit_on_train, make_design_matrix, train_logistic)
  - Production inference: stock_analyzer/sandbox/infrastructure/
                           model2_prediction_adapter.py (Model2PredictionAdapter)
Frozen SWING_20 snapshot:      swing20_20260718T135238Z
Frozen feature snapshot:       swing20_features_20260718T165654Z
Feature dataset path:          artifacts/swing_20_features/snapshots/
                                swing20_features_20260718T165654Z/features.parquet
Feature dataset hash:          b5d84fb0ee3b29bdd2b15f82c2d1c85904d569cc4629e4891a1d559be5806d6d
                                (sha256, reproduced 2026-07-22 -- see Section 13's
                                diagnostic metadata)
Frozen SWING_20 prices hash:   4cf0b9263eaec1022c635ef584f3e86a6c5003b3381009c5e1aecee087f5ba02
                                (sha256 of prices.parquet, same run)
Train row count:               1,206,131 rows, 591 dates (2022-07-14 .. 2024-11-15)
```

**Status, tracked separately -- these measure DIFFERENT things and are not in
tension with each other:**

| Validation dimension | Status |
|---|---|
| Statistical / ranking validation (EXP-002) | **PASS** |
| Locked Test ranking replication (EXP-003) | **PASS** |
| Probability calibration | **NOT VALIDATED** -- calibration is explicitly descriptive-only in both EXP-002 and EXP-003; degrades materially out-of-sample (Section 13) |
| End-to-end economic feasibility (EXP-005) | **FAIL** -- real Variant B run lost -37.2%, drawdown 57.4%, profit factor 0.75, all pre-registered absolute criteria failed |

A Locked Test **PASS** and an EXP-005 **FAIL** are not a contradiction: the Locked Test
measures whether Model 2's daily ranking beats the same-day base rate at picking rows
that touch +20% intraday within 20 sessions. EXP-005 measures whether a portfolio built
from that ranking, with a specific entry/exit/capacity policy, made money. Section 13
documents exactly where between those two questions the value is lost.

---

## 2. Intended Purpose

Model 2 ranks each day's eligible SWING_20 universe by how likely each stock's price is
to **touch** (intraday High, not close) a level 20% above the next session's opening
price, at any point within the following 20 trading sessions.

**What Model 2 does NOT predict** -- stated explicitly because Section 13 shows these
have been informally assumed at various points in this project:

- 20- or 42-day **closing-price** return (Section 13 shows the model's score is
  *negatively* correlated with forward close returns at every horizon tested).
- Risk-adjusted return of any kind.
- Realizable / capturable profit (the label only asks whether a price level was
  *touched*, not whether a position could actually be exited near that level).
- The optimal exit for a position (Model 2 has no view on exits at all; exit policy is
  a wholly separate, unvalidated layer -- see EXP-005).
- A company's fundamental quality, long-term growth prospects, or sector
  attractiveness (no fundamental or sector data is used at all -- see Section 3/13).

---

## 3. Universe and Eligibility

Per `docs/02_mvp/SWING20_Dataset_Specification_v1.md` Section 1 (frozen):

- **Source universe:** `full_us` (`stock_analyzer.data.universe_filter.build_full_universe`),
  resolved fresh at snapshot-build time -- **not point-in-time** (current symbol
  availability, not a historical listing; carried as `SURVIVORSHIP_BIAS_PRESENT` /
  `UNIVERSE_MEMBERSHIP_NOT_POINT_IN_TIME`, Section 11 below).
- **Eligibility filters**, re-evaluated per signal date:
  - `minimum_price = $5.00`
  - `minimum_adv20 = $5,000,000` (20-day average dollar volume)
  - `minimum_history_days = 250`
- **Exchanges:** NASDAQ, NYSE, NYSE American. **Instrument type:** `COMMON_STOCK` only.
- **Exclusion reasons** (countable): `INSUFFICIENT_HISTORY`, `LOW_PRICE`, `LOW_ADV20`.
- **Current-day cutoff:** any bar dated on or after the current America/New_York
  calendar date is dropped before labels/eligibility are computed
  (`EXCLUDE_CURRENT_NEW_YORK_DATE`).
- **Data-quality quarantine:** a symbol is dropped from the model-eligible universe
  (never from the raw frozen snapshot) if its price history contains a non-positive
  OHLC value, High below Low, or a material OHLC deviation
  (`INVALID_PRICE_SERIES` -- 4 symbols quarantined on the full-US snapshot).
- **The frozen feature dataset used by Model 2 already carries only `eligible=True`
  rows** -- verified programmatically (Section 13's diagnostic): all 1,655,036 rows in
  `swing20_features_20260718T165654Z/features.parquet` have `eligible=True` and an
  empty `exclusion_reason`. The eligibility filter has already been applied upstream by
  the time this dataset reaches training or inference; `CandidateService` does not
  re-apply a liquidity/price/history filter of its own.

**NVDA and PLTR were eligible** for every one of the 197 validation-period dates (196
for PLTR, one date short) -- the universe filter did not remove them. Model 2's own
*ranking* placed them far outside the top 10: NVDA's best daily rank across the whole
validation period was 2182 (median 2257, worst 2388); PLTR's best was 2187 (median
2252, worst 2385). Neither ever appeared in a daily shadow top-10. This is a ranking
outcome, not a universe-eligibility outcome (Section 13).

---

## 4. Label Definition

Per `docs/02_mvp/SWING20_Dataset_Specification_v1.md` Sections 3-9 (frozen):

```text
signal_date  = t                          (the feature/context date)
entry_date   = next trading session after t
entry_price  = Open[entry_date]
target_price = entry_price * 1.20
horizon      = 20 actual ticker trading bars, entry_date INCLUDED as bar 1
               (a positional count, not a calendar-day count -- holidays/halts/
               missing bars for a specific ticker do not shrink or stretch it)
label        = target_20pct_20d = any(High[bar] >= target_price
                                       for bar in the 20-bar horizon)
```

- The **signal day's own High is never used** for target detection -- only `entry_date`
  and later bars count. `entry_date`'s own High DOES count (it is a genuine future bar
  relative to the signal date).
- `close_return_20d`, `mfe_20d`, `mae_20d`, and `days_to_target` are computed alongside
  the binary label as **outcome diagnostics, not alternative labels**:
  `close_return_20d = Close[bar 20] / entry_price - 1`;
  `mfe_20d = max(High[bar 1..20]) / entry_price - 1`;
  `mae_20d = min(Low[bar 1..20]) / entry_price - 1`.
  (Section 13's diagnostic independently recomputed all three from the frozen prices
  artifact and confirmed exact agreement with these stored columns -- max absolute
  difference ~1e-15, i.e. float noise, across all 448,905 validation rows.)
- **Same-day target/stop ambiguity:** not applicable to the primary label -- only future
  bars' High is examined; `fixed_stop = -0.08` is computed as a diagnostic
  (`target_before_fixed_stop`, `fixed_stop_hit`, `fixed_stop_day`) but never alters the
  primary label.
- **`TARGET_ALREADY_REACHED_AT_ENTRY`:** a row where `entry_price >= signal_close *
  1.20` represents a move that happened before the modeled entry -- excluded from the
  primary label population (never recoded as negative), retained in diagnostics
  (`target_already_reached_at_entry` field). Excludes 1,438 of 2,150,125 full-US
  observations (0.067%); positive rate essentially unchanged (11.67% -> 11.65%).
- **Corporate actions:** yfinance adjusted OHLCV carries no independent corporate-action
  provenance confirmation beyond the quarantine's structural OHLC checks
  (`OHLC_ROUNDING_TOLERANCE_ARTIFACTS_PRESENT`).
- **Right-censoring:** not applicable within a completed frozen snapshot -- every label
  row's 20-bar window is fully realized before the snapshot is built (unlike the
  EXP-005 sandbox replay's own live right-censoring at `outcome_data_end_date`, which is
  an unrelated, later-stage concept).
- **Missing entry price:** rows with a missing/non-positive next-day Open are dropped
  from labels entirely, never coded negative (`MISSING_ENTRY_PRICE_ROWS_EXCLUDED`, 863
  rows on the full-US snapshot).

**The label measures whether a price level was touched, not whether a position could
be reliably closed at or near that level, or whether the price held at that level
afterward.** This distinction is the central finding of Section 13.

---

## 5. Features and Exact Order

The exact 17-feature list and order Model 2 is fit and scored on
(`make_design_matrix(df, fit, "model2")`, reproduced and pinned as a regression guard
in `Model2PredictionAdapter.FROZEN_MODEL2_FEATURE_LIST`):

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

### Per-feature definitions

| # | Feature | Definition | Source | Lookback | Standardization | Missing-value handling |
|---|---|---|---|---|---|---|
| 1 | `log_adv20_z` | `(log(adv20.clip(lower=1)) - train_mean) / train_std` | `adv20` column (frozen `eligibility` artifact; `mean(Close*Volume)` over 20 days) | 20 days | train-only z-score | none needed -- ADV is part of the eligibility gate, so eligible rows always have a value |
| 2 | `is_bear` | `1.0 if spy_trend == "Bear" else 0.0` | `spy_trend` (`build_market_regime`, `stock_analyzer/validation/regime.py`) | SPY SMA200 warm-up (~200d) | none (indicator) | rows with `NaN` regime excluded upstream |
| 3 | `is_vol_low` | `1.0 if spy_volatility_bucket == "Low" else 0.0` | `spy_volatility_bucket` (VIX / 20d realized vol / ATR% 3-tier fallback) | up to 30d for the active tier | none | excluded upstream if regime is `NaN` |
| 4 | `is_vol_high` | `1.0 if spy_volatility_bucket == "High" else 0.0` | same as above | same | none | same |
| 5 | `is_bear_x_vol_high` | `is_bear * is_vol_high` | derived | -- | none | -- |
| 6-14 | `rvol_d1..rvol_d10` (excl. `d5`) | one-hot dummy for `rvol_20`'s train-fit decile bucket, `d5` (near the U-shape minimum, EXP-001 H2) held out as the reference level | `rvol_20 = Volume[t] / rolling_mean(Volume, 20)[t]` | 20 days | train-fit `pd.qcut` decile edges, outer edges extended to ±inf | excluded upstream during the 20-bar warm-up |
| 15 | `rsi_14_z` | `(rsi_14 - train_mean) / train_std` | standard 14-day RSI (`stock_analyzer/core/indicators.py`, `pandas_ta`) | 14 days | train-only z-score | excluded upstream during the 14-bar warm-up |
| 16 | `rsi_14_z_x_bear` | `rsi_14_z * is_bear` | derived | -- | -- | -- |
| 17 | `rsi_14_z_x_low_adv` | `rsi_14_z * is_low_adv`, `is_low_adv = 1.0 if adv_quintile == "adv_q1" else 0.0` (train-fit ADV quintile edges) | derived | -- | -- | -- |

`is_bear_x_vol_low` (Bear trend x Low SPY volatility) is **structurally absent** from
the feature list, not merely dropped: Bear periods never co-occur with Low SPY
volatility anywhere in this dataset (0 of 1,206,131 train rows, 0 of 448,905
validation rows) -- a property of `build_market_regime`, not a split-specific
artifact. Including a constant-zero column would be unidentifiable and was excluded
from the design matrix entirely rather than reported as a falsely "stable" zero
coefficient.

None of the 17 features have missing values in the frozen train+validation dataset
(verified during EXP-001/EXP-002 development); the pipeline does not impute --
`make_design_matrix` would propagate `NaN` and `predict_proba` would raise rather than
silently guess.

---

## 6. Preprocessing

All of the following are fit on the **train split only** (`fit_on_train(train_df)`,
`scripts/train_swing_20_logistic_baseline.py`) and applied unchanged to validation and
locked_test -- reproduced exactly, 2026-07-22:

```text
log_adv_mean  = 17.608378771235728
log_adv_std   = 1.3960941241796034
rsi_mean      = 51.67936975274472
rsi_std       = 12.346393583697356
train_base_rate = 0.10217049391815648

adv20 quintile edges (log scale, pd.qcut on train, outer edges -> ±inf):
  [-inf, 16.28554565, 17.06188371, 17.85919651, 18.843061, inf]
  labels: adv_q1 .. adv_q5

rvol_20 decile edges (pd.qcut on train, outer edges -> ±inf):
  [-inf, 0.55201888, 0.65507348, 0.73536711, 0.81150815, 0.88958671,
   0.97763936, 1.08572849, 1.23942309, 1.53164315, inf]
  labels: d1 .. d10 (reference level d5, dropped from the dummy encoding)
```

- **Column-order guard:** `Model2PredictionAdapter.__init__` compares its own freshly
  re-fit design matrix's column tuple against `FROZEN_MODEL2_FEATURE_LIST` and raises
  `FrozenModelMismatchError` immediately if `make_design_matrix`'s output ever drifts
  from the frozen 17-feature list/order -- fails loudly rather than silently scoring
  with a different model than the one that passed the Locked Test.
- **Fail-closed on missing values:** no imputation branch exists; a `NaN` feature value
  propagates into `predict_proba`, which raises.

---

## 7. Hyperparameters and Fitted Coefficients

```python
sklearn.linear_model.LogisticRegression(
    C=1.0,
    solver="lbfgs",
    max_iter=2000,
)
```

L2 penalty (sklearn default), **unweighted** (no `class_weight`). Fixed since
EXP-001/EXP-002, not tuned via search. **This is a logistic regression, not a gradient-
boosted tree model of any kind (not LightGBM, not XGBoost).** LightGBM is a plausible
candidate algorithm for a *future* Model 3, but must not be attributed to Model 2.

**Exact fitted coefficients** (reproduced 2026-07-22 by re-running `fit_on_train` +
`make_design_matrix` + `train_logistic` on the frozen train split -- bit-for-bit
identical to what `Model2PredictionAdapter` produces at construction time, confirmed by
direct comparison in Section 13's diagnostic):

```text
intercept              = -2.4219801869358757

log_adv20_z            = -0.26441842346743555
is_bear                =  0.3914248639631965
is_vol_low             =  0.06223505369657934
is_vol_high            =  0.06710631318877486
is_bear_x_vol_high     = -0.06759214820226117
rvol_d1                =  0.4400748371177091
rvol_d2                =  0.0633149333247239
rvol_d3                =  0.033259498728610865
rvol_d4                =  0.018902047545622476
rvol_d6                = -0.008547348287206956
rvol_d7                = -0.003175346078017302
rvol_d8                =  0.015626988572201513
rvol_d9                =  0.028682925342389295
rvol_d10               =  0.15888982905254911
rsi_14_z               = -0.1246942085062711
rsi_14_z_x_bear        = -0.17526405228927447
rsi_14_z_x_low_adv     =  0.018076188279156087
```

**These coefficients were not adjusted on, or in response to, the Locked Test** -- per
EXP-003 Part 1's refit policy, the Locked Test evaluates the model fit on train only
(the same object already evaluated in EXP-002), with no refit on train+validation and
no refit on locked_test.

**Artifact/commit provenance:** produced by `scripts/train_swing_20_logistic_baseline.py`
at commit `8857532adf518206cecc8c901866a128c9d170cf`, run against
`swing20_features_20260718T165654Z/features.parquet`
(sha256 `b5d84fb0ee3b29bdd2b15f82c2d1c85904d569cc4629e4891a1d559be5806d6d`).

---

## 8. Train / Validation / Locked-Test Splits

Per `docs/02_mvp/SWING20_Dataset_Specification_v1.md` Section 10 (temporal, fractional:
60% / 20% / 20% of the primary label population's date range) and EXP-002/EXP-003:

| Split | Start | End | Observations | Symbols | Raw positive rate | Used for | May change based on it? |
|---|---|---|---:|---:|---:|---|---|
| train | 2022-07-14 | 2024-11-15 | 1,206,131 | -- | 10.23% | Fitting preprocessing (standardization, ADV/RVOL bucket edges) AND the model itself | Yes -- this is the only split any parameter is fit on |
| validation | 2024-11-18 | 2025-09-03 | 448,905 | -- | 11.65% | Model selection (Model 0 vs. 1 vs. 2), daily ranking metric evaluation, temporal-block/calibration diagnostics | Model/feature selection only -- never used to fit a parameter |
| locked_test | 2025-09-04 | 2026-06-17 | 493,651 | 3,227 | 15.19% | One-shot replication check of the already-selected Model 2, read exactly once | Nothing -- no retraining, revision, or promotion in the same cycle as reading it |

- **Preprocessing fit ONLY on train.** **Model fit ONLY on train.**
- **Validation used for model selection** (Model 1 vs. Model 2 vs. Model 0), never for
  fitting a parameter.
- **Locked Test opened exactly once**, after Part 1 of EXP-003 was committed
  (commit `7a8995e`) -- git history itself proves the pre-registration predates any
  Locked Test row being read.
- **EXP-004's Development Historical Replay and EXP-005's real Variant B run both
  execute over the VALIDATION period (2024-11-18 .. 2025-09-03), not locked_test.**
  This is a deliberate, documented choice (EXP-005 Section 5's post-freeze addendum) --
  it makes EXP-004/EXP-005 directly comparable to EXP-002's own validation-period
  ranking numbers, but it means **neither EXP-004 nor EXP-005 is an independent
  out-of-sample confirmation** of anything; both replay a period the model's ranking
  ability was already validated on. A genuine independent economic test would need to
  run over locked_test (2025-09-04 .. 2026-06-17) or later, unused data.

---

## 9. Locked Test Methodology

Per `docs/09_experiments/EXP-003_SWING20_Locked_Test.md`:

- **Pre-registration commit:** Part 1 committed before any locked_test row was read
  (`7a8995e`).
- **Primary metric:** daily cross-sectional precision@k / lift@k -- each date's own
  eligible symbols ranked independently, precision measured against that date's own
  base rate, aggregated across dates (mean/median, block-bootstrap 95% CI, fraction of
  dates with lift > 1). **Primary business metric: daily top-10-symbols lift. Primary
  broad-ranking robustness metric: daily top-10% lift.**
  Global (pooled) ranking is retained only as a non-decision diagnostic.
- **Temporal blocks:** 4 pre-declared, equal-day-count, chronological (boundaries fixed
  by date sequence before any result was seen).
- **Uncertainty:** moving-block bootstrap, block length = 20 trading days (matching the
  label horizon, per ADR-005), 1000 resamples, seed = 7.
- **Concentration checks:** ticker concentration (top-10-selected-symbol share of total
  top-10 slots) and subperiod dominance, both reported as PASS disqualifiers if extreme.
- **Pre-declared thresholds (PASS requires ALL):** daily top-10-symbols mean lift >=
  1.50; that lift's block-bootstrap 95% CI lower bound > 1.00; >= 1 positive in top-10
  on >= 70% of dates; daily top-10% mean lift >= 1.20; not dominated by one subperiod or
  ticker concentration.
- **Actual result -- VERDICT: PASS**, all 5 conditions held:

  | Check | Threshold | Locked_test value |
  |---|---|---|
  | Top-10-symbol mean daily lift | >= 1.50 | **2.74** |
  | Top-10-symbol lift, 95% CI lower bound | > 1.00 | **2.26** |
  | Fraction of dates with >=1 positive in top-10 | >= 0.70 | **0.99** |
  | Daily top-10% mean lift | >= 1.20 | **1.41** |
  | Not dominated by subperiod/ticker concentration | -- | confirmed (worst block lift 1.95; top-10 most-frequent symbols only 11.9% of slots) |

- **Why PASS:** the result replicated EXP-002's validation finding on genuinely unseen
  data (locked_test was "previously completely unused in any prior SWING_20 work"),
  several conditions cleared with a wide margin, and locked_test's own context/stock
  decomposition showed BOTH context and stock selection independently carry positive
  lift -- if anything a more complete result than validation. Performance was
  appropriately lower than validation (top-10 lift 2.74 vs. validation's 3.71), the
  expected pattern for a genuine replication rather than a red flag.
- **Real caveats that traveled forward with the PASS:** calibration degrades materially
  out-of-sample (Brier 0.132 vs. validation's 0.101; ECE 6.2% vs. 1.6%); the edge is
  concentrated in smaller/less-liquid names and nearly vanishes in the largest-ADV
  quintile (lift ~1.01).

---

## 10. Model Output Contract

```text
predict_proba(X)[:, 1]  ->  internal ordinal score ("model_score" everywhere in the sandbox)
production meaning       =  a ranking score: higher means "ranked more likely to touch
                             +20% intraday within 20 sessions," nothing more
NOT PERMITTED meaning     =  a calibrated probability of profit, or of anything else
```

- **Daily descending sort:** `CandidateService.generate_candidates` scores every
  eligible row for the date, `ranked_symbols = scores.sort_values(ascending=False)`.
- **Shadow top-10:** `shadow_symbols = ranked_symbols.index[:shadow_top_n]`,
  `SandboxConfig.shadow_top_n = 10` -- every one of these 10 gets a persisted
  `ranked_candidates` row (`shadow_top10=True`) regardless of what happens next.
- **Actionable top-3:** of the shadow 10, up to `SandboxConfig.max_actionable_candidates
  = 3`, in rank order, become `actionable` -- skipping symbols with a data-quality
  exclusion (`MISSING_MARKET_DATA`, `STALE_DATA`, `INVALID_PRICE`, `MISSING_ATR`) or
  already-open/pending positions (`RANK_LIMIT_EXCEEDED` beyond the cap).
- **Already-open/pending exclusion:** handled in `_decide_selection`, in rank order --
  a symbol already held or with a pending order is skipped without consuming an
  actionable slot for a duplicate.
- **Capacity policy is an entirely separate layer**, applied AFTER the actionable-3
  selection (portfolio admission, slot reservation -- see EXP-005 Section 8). Model 2
  itself has no notion of capacity.
- **Calibration:** `predict_proba`'s output should not be read as "an X% chance of
  success" for any X -- see Section 9's calibration figures and Section 13's further
  degradation evidence. Ranking order is the only validated use.

---

## 11. Determinism and Reproducibility

- The fit is **recovered deterministically from the frozen train parquet** every time a
  `Model2PredictionAdapter` is constructed -- `solver="lbfgs"` is a deterministic
  quasi-Newton optimizer (not stochastic, no seed required), so re-running
  `fit_on_train` + `make_design_matrix` + `train_logistic` on the identical frozen
  train data and identical hyperparameters reproduces the exact EXP-002 Model 2
  coefficients bit-for-bit.
- **No separate serialized model binary exists or is required** -- this determinism is
  exactly why. (Verified directly in Section 13's diagnostic: a fresh, independent
  `fit_on_train`/`make_design_matrix`/`train_logistic` reproduction matched the
  production `Model2PredictionAdapter`'s own scores to `atol=1e-12` across all 448,905
  validation rows.)
- **Feature-order regression guard:** `Model2PredictionAdapter.FROZEN_MODEL2_FEATURE_LIST`
  (Section 5) is compared against the live `make_design_matrix` output's column tuple at
  every adapter construction; a mismatch raises `FrozenModelMismatchError` immediately.
- **Code commit:** `8857532adf518206cecc8c901866a128c9d170cf`.
- **Snapshot hashes:** feature dataset sha256
  `b5d84fb0ee3b29bdd2b15f82c2d1c85904d569cc4629e4891a1d559be5806d6d`; SWING_20 prices
  sha256 `4cf0b9263eaec1022c635ef584f3e86a6c5003b3381009c5e1aecee087f5ba02` (both
  reproduced 2026-07-22).
- **Software dependency versions** (unchanged since EXP-001/002/003):
  `python 3.13.7`, `pandas 3.0.3`, `numpy 2.2.6`, `scikit-learn 1.9.0`.
- **Reproduction command:**
  ```bash
  python scripts/train_swing_20_logistic_baseline.py \
      --features-path artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet \
      --output-json artifacts/swing_20_logistic_baseline_report.json
  ```
- **Expected identity:** any correct reproduction against the same frozen inputs must
  match Section 7's coefficients/intercept exactly and Section 6's preprocessing
  parameters exactly -- these are not approximate.

---

## 12. Validated Capabilities

Only what the results in Sections 8-9 actually support:

- Model 2 has **positive daily cross-sectional lift** against the +20% intraday-touch
  label's own same-day base rate -- validation mean daily top-10-symbols lift 3.71
  (EXP-002); locked_test mean daily top-10-symbols lift 2.74 (EXP-003), both far above
  the pre-declared PASS thresholds.
- This lift **replicated on genuinely unseen data** (the Locked Test).
- The lift is **predominantly genuine within-day stock selection**, not market-timing:
  on validation, context-only global lift was below 1 while stock-only lift carried
  essentially all of the apparent lift; on locked_test, both context and stock
  selection independently carried positive lift.
- Model 2 **discriminates short-term, large, intraday-High price-movement probability**
  -- specifically the probability the frozen +20%-touch/20-session label asks about,
  and specifically as an ordinal ranking, not a calibrated estimate.

---

## 13. Known Limitations -- POST-HOC FINDINGS

**Everything in this section was discovered after Model 2 was already frozen, validated,
and Locked-Test-passed. None of it is part of the original specification (Sections
1-12) and none of it triggers reopening the Locked Test or refitting the model.** Four
populations are kept strictly separate below -- they measure different things and their
different magnitudes are not a contradiction:

```text
Population A: all daily eligible rows (validation split, 448,905 rows, 197 dates)
Population B: daily Model 2 shadow top-10 (1,970 (date,symbol) rows -- OVERLAPPING,
              highly-correlated observations across nearby dates and repeated symbols;
              never described as 1,970 independent trades)
Population C: the 108 actually-filled EXP-005 Variant B BUY positions
              (2024-11-18..2025-09-03 signal period)
Population D: the EXP-005 real, capital-constrained Variant B portfolio
              (single realized outcome, not a distribution)
```

Populations A and B were reproduced 2026-07-22 by a new, dedicated, read-only diagnostic
(`scripts/model2_shadow_top10_post_hoc_diagnostic.py`), independently of anything
computed in prior conversation, and verified against the frozen dataset's own
`close_return_20d`/`mfe_20d`/`mae_20d` columns (max absolute difference ~1e-15 across all
448,905 rows) before any of the numbers below were trusted. Full machine-readable output:
`artifacts/sandbox/model2_diagnostics/shadow_top10_post_hoc/shadow_top10_summary.json`
(sha256 `a90998343a62cba4b7dfbf880ba37ed540e2e1c3b0b12f82eb3439dca8416a25`). Population C
was reproduced by `scripts/exp005_variant_b_price_path_study.py`. Population D is the
real Variant B replay's own realized result.

### 13.1 The label measures a touch, not a sustained close-price level

- **Target is intraday High touch, not close return.** A row can satisfy
  `target_20pct_20d = True` while its close price never approaches +20%, or its
  close-price trajectory over the SAME window is negative.
- **Daily cross-sectional Spearman IC** (Fama-MacBeth: computed per date, then
  averaged across 197 dates -- never pooled, per the codebase's established
  ADR-005/EXP-001 discipline):

  | Target | Mean daily IC | Median daily IC | Fraction of dates positive |
  |---|---|---|---|
  | label (`target_20pct_20d`) | **+0.081** | +0.083 | -- |
  | 5-session forward close return | **-0.026** | -0.027 | 39.1% |
  | 10-session forward close return | **-0.037** | -0.056 | 35.0% |
  | 20-session forward close return | **-0.050** | -0.061 | 20.3% |
  | 42-session forward close return | **-0.056** | -0.062 | 24.4% |

  The score is genuinely informative about the LABEL (positive IC, consistent with the
  Locked Test PASS) but is **negatively** associated with actual forward close-price
  performance at every horizon tested, and the negative correlation gets *stronger*, not
  weaker, at longer horizons.

### 13.2 Daily shadow top-10 forward close returns are negative and get worse over time

Population B (daily shadow top-10, 1,970 rows) vs. Population A (all 448,905 eligible
rows), same validation period, forward close return from entry:

| Horizon | A: all eligible mean | B: shadow top-10 mean | B: shadow top-10 median |
|---|---|---|---|
| 5 sessions | +0.075% | **-2.18%** | -1.67% |
| 10 sessions | +0.100% | **-4.44%** | -2.95% |
| 20 sessions | +0.271% | **-9.35%** | -6.55% |
| 42 sessions | +1.441% | **-12.37%** | -11.87% |

The full eligible population's forward return is small and roughly flat; the daily
top-10 selection's forward return is negative from the very first week and compounds
worse through 42 sessions -- this is BEFORE any entry timing, capacity constraint, or
exit policy is applied. It is a property of the ranking itself.

### 13.3 Target-hit rate and MFE/MAE: the top-10 hits target far more, but gives more of it back

| | Population A (all eligible) | Population B (shadow top-10) |
|---|---|---|
| Target-hit rate (label, 20-session) | 11.63% | **33.50%** |
| Mean 20-session MFE | +10.43% | **+28.97%** |
| Mean 20-session MAE | -9.40% | **-23.50%** |

The shadow top-10 is nearly 3x more likely to touch target and has much larger
favorable excursions than the general population -- but also much larger adverse
excursions, and (13.2) its close-price return ends up negative anyway. This is
consistent with EXP-003's own already-documented finding that Model 2 concentrates on
smaller, higher-volatility names (Section 13.4).

### 13.4 Strong negative liquidity (ADV) tilt

- Daily cross-sectional Spearman IC, score vs. `log(adv20)`: **mean -0.847** across 197
  dates (i.e. Model 2's score is very strongly, consistently rank-correlated with LOWER
  liquidity). Pooled Pearson correlation: **-0.608**.
- This is consistent with, and quantifies more precisely, EXP-003's own already-reported
  finding that lift is strongest in the smallest-ADV quintile and nearly vanishes
  (lift ~1.01) in the largest/most-liquid quintile.
- **Diagnostic-only ADV-component-removed re-scoring** (subtracting the fitted
  `log_adv20_z` coefficient's contribution, `-0.2644...`, from the logit before the
  sigmoid -- explicitly NOT a new model, never re-fit, never promoted): the shadow
  top-10's 42-session mean return moves from **-12.37% to -11.60%**. **Removing the
  single explicit ADV term alone does not fix the negative return** -- most of the
  effect survives, meaning the RVOL/RSI terms (which are themselves correlated with
  low-liquidity, high-volatility names) carry the bulk of the same underlying bias, not
  just the one explicit ADV coefficient.

### 13.5 No sector, size, or fundamental information is used at all

Confirmed by Section 5's exact 17-feature list: no sector identity, no market
capitalization, no fundamental/valuation data of any kind is a Model 2 input (Section
3 of `SWING20_PointInTime_Feature_Specification_v1.md` explicitly deferred
fundamentals; sector features were never built past the specification-draft stage).

### 13.6 NVDA and PLTR were eligible every day but never reached top-10

Confirmed in Section 3 above -- restated here as a limitation, not a universe defect:
Model 2's ranking, not the eligibility gate, is why large, liquid, well-known names like
these never appear in the shadow top-10 (NVDA best rank 2182/197 days eligible; PLTR
best rank 2187/196 days eligible; zero top-10 appearances for either).

### 13.7 End-to-end economic result (Population D, EXP-005)

The real Variant B replay (2024-11-18 signal start .. 2025-10-20 outcome end, 230
sessions, commit `a7a7c8ad68c3412076cb8ddf7d4478962ce506dc`) -- a real, capital-
constrained ($100,000 starting capital), entry/exit/capacity-policy-mediated portfolio
built from this exact ranking -- lost money and failed every pre-registered absolute
feasibility criterion:

| Metric | Value | Threshold |
|---|---|---|
| Net P&L / return | **-$37,202 (-37.2%)** | > 0 |
| Max drawdown | **57.4%** | <= 20% |
| Profit factor | **0.75** | >= 1.0 |

108 closed trades (51 wins / 57 losses); `SELL_TARGET` exits averaged +28.8% but only
37 of 108 positions ever hit target -- the other 71 were cut at the 20-session time
exit averaging **-20.2%**. Full detail:
`docs/09_experiments/EXP-005_Stage15_Completion_Report.md`.

### 13.8 Where the value is lost, reading 13.1-13.7 together

Populations A -> B -> C -> D form a chain of increasingly negative-but-different
numbers, each adding a layer:

```text
A (all eligible, 42-session):        +1.44%   <- baseline population drift
B (shadow top-10, 42-session):      -12.37%   <- the ranking itself, before any policy
C (108 actually-bought positions):  -24.22%   <- + entry timing, capacity, symbol overlap
D (EXP-005 portfolio, 230 sessions): -37.20%   <- + real exit policy, position sizing, costs
```

The single largest jump is A -> B: **the ranking itself, entirely independent of any
entry/exit/capacity decision, already selects stocks whose close price underperforms.**
B -> C -> D show real ADDITIONAL damage from the entry/capacity/exit layers, but the
ranking is not merely "good stock-picking undermined by a bad exit policy" -- picking
by this score is itself, on average, picking stocks whose close price goes down over
the following 5-42 sessions, even though the same score reliably identifies which
stocks briefly TOUCH a much higher intraday level along the way.

**Model 2 is not a suitable investment signal without further, materially different
economic validation** -- specifically, a label and/or ranking that is validated against
sustained close-price performance, not intraday touch, would be a prerequisite before
any future model built on this same target definition is trusted for portfolio
construction.

---

## 14. Model 2 -> Model 3 Comparison Contract

Every future model must fill in this table BEFORE training, not after seeing results.

| Dimension | Model 2 | Model 3 |
|---|---|---|
| Algorithm | Logistic Regression (`sklearn.linear_model.LogisticRegression`) | TBD |
| Label | +20% intraday High touch / 20 sessions from next-day open | Frozen BEFORE training -- must be pre-registered, and per Section 13.1, should be justified against sustained close-price performance, not touch alone, if the intent is portfolio construction |
| Universe | SWING_20 eligible (price >= $5, ADV20 >= $5M, history >= 250d) | TBD |
| Liquidity policy | `log_adv20_z` as a continuous feature only, no hard large-cap floor -- Section 13.4 shows this produces a strong, mostly-unremoved negative-liquidity tilt | TBD -- must explicitly address whether/how liquidity bias is controlled, not left as an emergent property |
| Features | 17 frozen features (Section 5), no sector/size/fundamentals | TBD |
| Primary metric | Daily cross-sectional ranking lift (precision@k / lift@k vs. same-day base rate) | TBD |
| Economic metric | **Not optimized for** -- Model 2 was never evaluated against forward close return or realized P&L before EXP-005 | **Must be pre-registered** -- a forward close-return or realized-P&L-oriented metric, decided before training, not retrofitted after a Locked Test |
| Calibration | Not validated; degrades materially out-of-sample (Section 9, Section 13) | TBD -- state explicitly whether calibration is a target |
| Locked Test | PASS (2.74 mean top-10 lift, all 5 conditions) | Unopened until a final configuration is selected |
| End-to-end feasibility | **FAIL** (EXP-005: -37.2% return, 57.4% drawdown, profit factor 0.75) | Unrun |

This table exists specifically so a future model cannot repeat Model 2's exact failure
mode: a statistically successful label (touch-based ranking, real and replicated lift)
that does not correspond to the actual investment objective (sustained, capturable
price appreciation). "Better than Model 2" must be defined against Section 13's economic
findings before training starts, not discovered again after a second Locked Test and a
second real run.
