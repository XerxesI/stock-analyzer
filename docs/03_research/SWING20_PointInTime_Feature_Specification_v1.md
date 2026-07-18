# SWING_20 Point-in-Time Feature Specification v1 — Draft

**Status:** DRAFT (specification only — no feature dataset has been generated, no model trained)
**Depends on:** `docs/02_mvp/SWING20_Dataset_Specification_v1.md` (frozen)
**Related documents:** `docs/03_research/research_cycle_1_summary.md` (prior validated signals)

---

## 0. Ground Rule

This is a **candidate list**, not a commitment list. Every feature below must clear the
fail-fast criterion in its own row on the validation split before it is allowed into a
baseline model, and must not be re-justified after the fact if it doesn't. If a feature's
measured effect is small, unstable across time, or explainable only in hindsight, it is
dropped and the branch ends there — no rescue narrative, no re-parameterization to make the
same idea "work." This list is deliberately short. Market-context and sector-context features
are first-class, not an afterthought bolted on after stock-level indicators — SWING_20's
premise is that a stock's opportunity depends on the regime it moves in, not just its own
tape.

All features are computed strictly from information available **as of the signal date's
close** (`t`), the same causality convention already used by
`stock_analyzer/validation/regime.py`. None may reference `entry_date` (`t+1`) or later.

---

## 1. Stock-Level Features

### 1.1 `return_5d`, `return_20d`

- **Phenomenon:** short and medium-term price momentum — is the stock already trending before
  the signal date.
- **Formula:** `Close[t] / Close[t-N] - 1`, `N ∈ {5, 20}` trading days.
- **Point-in-time source:** frozen `prices` artifact, signal-day and earlier only.
- **Missing-value rule:** `NaN` if fewer than `N` prior bars exist for the symbol — row is
  excluded from that feature's training rows, not imputed to 0 (imputing to 0 would silently
  claim "no momentum," a false signal).
- **Leakage risk:** none if strictly `t` and earlier; must not accidentally use `entry_price`.
- **Interaction with context:** expected to matter more in `Bull` trend regime (momentum
  persistence is a regime-dependent effect, not universal) — test the interaction, don't assume
  it.
- **Fail-fast criterion:** if univariate IC (rank correlation with `target_20pct_20d` /
  `close_return_20d`) on validation is not distinguishable from zero after regime
  stratification, drop. Reject before combining with other momentum features — a redundant
  momentum family is a common way to inflate apparent signal without adding information.

### 1.2 `rsi_14`

- **Phenomenon:** short-term overbought/oversold positioning.
- **Formula:** standard 14-day RSI. **Existing implementation:**
  `stock_analyzer/core/indicators.py:30` (`ta.rsi(close, length=14)` via `pandas_ta`).
- **Point-in-time source:** same frozen prices, already computed by an existing, reused
  function — no new logic needed.
- **Missing-value rule:** `NaN` during the 14-bar warm-up; excluded, not imputed.
- **Leakage risk:** none (backward-looking only).
- **Interaction with context:** prior research
  (`docs/03_research/research_cycle_1_summary.md`) treats RSI as a component feeding into
  `S1`/support-style signals rather than a standalone edge — treat as a diagnostic input to
  test, not an assumed contributor.
- **Fail-fast criterion:** if adding RSI does not change validation IC versus the
  momentum-only feature set beyond noise, drop it as redundant with `return_5d`/`return_20d`
  rather than keeping it "just in case."

### 1.3 `rvol_20` (MF1)

- **Phenomenon:** is today's trading activity unusual relative to the stock's own recent
  history — a proxy for information arrival / crowd attention.
- **Formula:** `Volume[t] / rolling_mean(Volume, 20)[t]`. **Existing implementation:**
  `stock_analyzer/signals/money_flow.py` (`calculate_money_flow_features`, `rvol_window=20`).
  Already validated: Bull-regime informational IC ≈ +0.035
  (`docs/03_research/research_cycle_1_summary.md`, section 12).
- **Point-in-time source:** frozen `prices` Volume column, signal-day and earlier.
- **Missing-value rule:** `NaN` during the 20-bar warm-up; excluded.
- **Leakage risk:** none.
- **Interaction with context:** already established as Bull-regime-conditional in prior
  research — must be evaluated split by regime, not pooled, or its effect will look diluted or
  disappear entirely.
- **Fail-fast criterion:** this one already has a validated prior-cycle IC; the bar is
  **replication**, not discovery — if it does not reproduce a comparable Bull-regime IC on the
  SWING_20-labeled population (a different label/horizon than the study it was validated
  under), treat the prior result as non-transferable and drop it rather than adjusting
  thresholds to force a match.

### 1.4 `compression_pct_100` (VC3 component)

- **Phenomenon:** is the stock's recent trading range unusually tight relative to its own
  history — a coiled-spring precursor some breakout strategies rely on.
- **Formula:** `(BBW - rolling_min(BBW, 100)) / (rolling_max(BBW, 100) - rolling_min(BBW, 100))`
  where `BBW = (BB_upper - BB_lower) / BB_middle`. **Existing implementation:**
  `stock_analyzer/signals/volatility_compression.py` (`calculate_compression_state`,
  `lookback=100`). Bottom-20% quantile combined with `rvol_20 > 1.0` was previously validated
  as VC3 (Bull regime, `docs/03_research/research_cycle_1_summary.md` sections 20-22).
- **Point-in-time source:** frozen prices, 100-bar rolling window ending at `t`.
- **Missing-value rule:** `NaN` until 100 bars of history exist; excluded (this is already
  compatible with the `minimum_history_days=250` eligibility filter).
- **Leakage risk:** none.
- **Interaction with context:** validated only in Bull regime previously — do not assume it
  transfers to Bear without re-testing.
- **Fail-fast criterion:** same replication bar as 1.3 — must reproduce a comparable effect
  under the SWING_20 label before being combined with `rvol_20` into a joint VC3-style feature.

### 1.5 `c1_composite` (support + bear-conditional momentum)

- **Phenomenon:** combined support-proximity and momentum signal, previously shown to carry
  incremental information over its components in Bear regime specifically
  (IC(C1)=+0.0163 vs IC(support alone)=+0.0092, per prior research cycle).
- **Formula:** `z_support + z_momentum * is_bear`, frozen scaler parameters in
  `artifacts/reports/frozen_c1_params.json` (`stock_analyzer/evaluation/freeze_c1_params.py`).
- **Point-in-time source:** component `support_signal`/`momentum_signal` — **gap identified
  during this specification**: these were not found as standalone reusable functions under
  `stock_analyzer/core/` or `stock_analyzer/signals/` in the current tree; they exist only as
  columns in a research CSV (`phase2_retest_obs.csv`) and inline in evaluation scripts. Before
  C1 can be added to the feature dataset, its component signals must be extracted into a
  reusable, point-in-time-safe function — this is a prerequisite task, not a feature-design
  detail.
- **Missing-value rule:** TBD once the component extraction above is done.
- **Leakage risk:** unassessed until the extraction is done (the frozen scaler parameters
  themselves were fit on a specific historical sample — must confirm they don't encode
  locked-test-period information).
- **Interaction with context:** explicitly Bear-conditional by construction.
- **Fail-fast criterion:** do not include in the first feature-dataset pass. Extract and
  re-verify point-in-time safety first; only add in a follow-up pass if the extraction confirms
  no leakage.

### 1.6 `adv20`

- **Phenomenon:** liquidity level — already used as an eligibility gate; also useful as a
  continuous feature since liquidity above the gate still varies meaningfully.
- **Formula:** `mean(Close[t-19..t] * Volume[t-19..t])`. Already computed in
  `stock_analyzer/datasets/swing_20/universe.py` for eligibility.
- **Point-in-time source:** frozen `eligibility` artifact (`adv20` column already present).
- **Missing-value rule:** rows failing the eligibility gate are already excluded from labels
  upstream; no additional handling needed inside the feature dataset.
- **Leakage risk:** none.
- **Interaction with context:** liquidity effects are known to interact with regime (illiquid
  names get riskier in high-volatility regimes) — test the interaction term, don't assume it
  linearly.
- **Fail-fast criterion:** if `log(adv20)` shows no monotonic relationship with outcome
  quality (MFE/MAE) net of the eligibility gate already applied, drop as redundant with the
  gate itself.

---

## 2. Market-Context Features

### 2.1 `spy_trend` (Bull/Bear)

- **Phenomenon:** is the broad market in an uptrend or downtrend.
- **Formula:** `Bull if SPY Close > SPY SMA200 else Bear`. **Existing implementation:**
  `stock_analyzer/validation/regime.py` (`build_market_regime`, `_trend_regime`), already
  causally safe (same-day close, forward-filled as-of join via `tag_observations`).
- **Point-in-time source:** SPY OHLCV, same-day close.
- **Missing-value rule:** `NaN` during SMA200 warm-up (~200 bars); excluded.
- **Leakage risk:** none — already documented as causally safe in the source module's
  docstring.
- **Interaction:** this is the primary conditioning variable for several stock-level features
  above (1.3, 1.4, 1.5) — treat as an interaction term, not just an additive feature.
- **Fail-fast criterion:** N/A as a standalone feature — its role is established already
  (regime stratification is load-bearing for 1.3-1.5); the fail-fast question is whether
  *other* features' effects survive being tested within each trend bucket separately.

### 2.2 `spy_volatility_bucket` (Low/Normal/High)

- **Phenomenon:** market-wide volatility regime.
- **Formula:** VIX-based if available, else 20-day annualized SPY realized volatility, else
  SPY ATR% terciles. **Existing implementation:** `stock_analyzer/validation/regime.py`
  (`build_market_regime`), 3-tier fallback chain already built and documented.
- **Point-in-time source:** VIX close or SPY close, same-day.
- **Missing-value rule:** `NaN` if fewer than 30 valid observations in the active tier;
  excluded (already handled by the existing fallback logic).
- **Leakage risk:** none, per existing module docstring.
- **Interaction:** expected to modulate the `mfe_20d`/`mae_20d` distribution (higher-vol
  regimes plausibly widen both tails) — test directly rather than assuming.
- **Fail-fast criterion:** if outcome distributions (not just hit rate) are statistically
  indistinguishable across volatility buckets on validation, drop the bucket as a feature but
  keep it as a reporting/stratification dimension (it is still required for the audit's
  regime diagnostics regardless of model use).

### 2.3 `qqq_relative_trend`

- **Phenomenon:** growth/tech-sector-heavy market breadth relative to the broad market — SPY
  alone conflates broad-market and mega-cap-tech-driven regimes.
- **Formula:** `QQQ 20-day return - SPY 20-day return`.
- **Point-in-time source:** QQQ + SPY OHLCV, same-day close. **Not yet implemented** — no
  existing QQQ fetch path found; would reuse `get_stock_data("QQQ", period)` the same way SPY
  is fetched in `core/market_context.py`.
- **Missing-value rule:** `NaN` during the 20-bar warm-up; excluded.
- **Leakage risk:** none if both series are same-day-close-only.
- **Interaction:** exploratory — no prior validation exists for this specific feature in this
  codebase.
- **Fail-fast criterion:** if univariate IC is not distinguishable from zero on validation,
  drop immediately — this is the least-justified feature on this list and should not survive
  past a first pass without a clear signal.

### 2.4 `market_breadth`

- **Phenomenon:** what fraction of the frozen eligible universe is itself in an uptrend on a
  given date — a market-wide participation signal distinct from SPY's own trend.
- **Formula:** `count(eligible symbols with Close[t] > SMA50[t]) / count(eligible symbols on
  date t)`, computed from the frozen `eligibility` + `prices` artifacts.
- **Point-in-time source:** frozen eligibility universe on date `t` — **caveat:** the eligible
  universe itself is resolved with current, not point-in-time, symbol availability (see
  `SWING20_Dataset_Specification_v1.md` section 11), so breadth computed this way inherits
  that survivorship limitation. Not implemented yet.
- **Missing-value rule:** `NaN` on dates with fewer than some minimum eligible count (TBD,
  suggest 50) to avoid noisy breadth estimates early in the sample.
- **Leakage risk:** none for the breadth computation itself, but inherits universe
  survivorship bias.
- **Interaction:** plausible substitute or complement for `spy_trend` — test whether it adds
  information beyond SPY trend before keeping both.
- **Fail-fast criterion:** if breadth's univariate IC does not exceed `spy_trend`'s alone
  (i.e., it's redundant), drop it — do not carry two market-direction proxies into the same
  model without a demonstrated reason.

---

## 3. Sector-Context Features

Sector identity is flagged `SECTOR_NOT_POINT_IN_TIME` in the frozen Dataset Specification.
Every feature in this section inherits that limitation and must be treated as **exploratory
only** until a point-in-time sector-assignment source is confirmed or built.

### 3.1 `sector_identity`

- **Phenomenon:** categorical sector membership, needed as a grouping key for 3.2-3.4.
- **Formula:** current GICS (or equivalent) sector from existing fundamentals data.
- **Point-in-time source:** **not point-in-time** — current sector membership only, no
  historical reclassification tracking. This is a known, accepted limitation carried in the
  Dataset Specification, not a new one.
- **Missing-value rule:** rows with unknown sector excluded from sector-conditional features,
  retained for stock-level-only modeling.
- **Leakage risk:** low in practice (sector reclassifications are infrequent relative to the
  20-day horizon) but unverified — do not claim point-in-time safety.
- **Interaction:** grouping key for the rest of this section.
- **Fail-fast criterion:** if sector-conditional features (3.2-3.4) show no incremental value
  over stock- and market-level features, drop the entire sector-context branch rather than
  keeping the categorical variable "for completeness."

### 3.2 `sector_etf_trend`

- **Phenomenon:** is this stock's sector, specifically, in an uptrend (distinct from the
  broad market).
- **Formula:** same `Close > SMA200` logic as `spy_trend`, applied to the relevant sector
  ETF (e.g. XLK, XLF, XLE).
- **Point-in-time source:** sector ETF OHLCV, same-day close — mechanically identical to
  `spy_trend`, reusing `build_market_regime`'s `_trend_regime` per sector ETF series.
- **Missing-value rule:** `NaN` during warm-up; excluded.
- **Leakage risk:** none for the ETF trend itself; inherits `sector_identity`'s
  non-point-in-time mapping.
- **Interaction:** expected to partially duplicate `spy_trend` for broad-market-correlated
  sectors — test the residual (sector trend net of SPY trend), not the raw value.
- **Fail-fast criterion:** if `sector_etf_trend` doesn't separate from `spy_trend` (i.e. near-
  perfect correlation across sectors), drop.

### 3.3 `stock_relative_strength_vs_sector`

- **Phenomenon:** is this specific stock outperforming its own sector, isolating stock-picking
  skill from sector-timing.
- **Formula:** `stock 20-day return - sector_etf 20-day return`.
- **Point-in-time source:** same as 1.1 and 3.2, same-day close only.
- **Missing-value rule:** `NaN` if either component is `NaN`.
- **Leakage risk:** none beyond inherited sector-mapping caveat.
- **Interaction:** plausibly the most informative sector-context feature (relative strength
  concepts have consistent prior support in momentum literature), but unvalidated in this
  codebase — treat as the priority candidate within this section, not an assumption.
- **Fail-fast criterion:** if univariate IC is not distinguishable from zero on validation,
  drop, and treat that as evidence against the whole sector-context branch, not just this one
  feature.

### 3.4 `sector_breadth`

- **Phenomenon:** participation within the stock's own sector, analogous to 2.4 but narrower.
- **Formula:** same as `market_breadth`, restricted to the stock's sector.
- **Point-in-time source:** inherits `sector_identity` limitation plus small-sample risk for
  thinly represented sectors.
- **Missing-value rule:** `NaN` if fewer than a minimum sector-eligible count (suggest 15) on
  that date.
- **Leakage risk:** none beyond inherited caveats.
- **Interaction:** lowest priority in this document — only worth computing if 3.2 and 3.3
  both survive their fail-fast checks.
- **Fail-fast criterion:** do not implement this feature at all unless 3.2 and 3.3 already
  show incremental value. Implementing it unconditionally is exactly the "long indicator
  list" pattern this specification is meant to avoid.

---

## 4. Explicitly Deferred

Not included in this pass, with reasons:

- **Fundamentals** (earnings, valuation ratios): excluded per
  `docs/02_mvp/MVP_1_Specification.md` section 15 — no confirmed point-in-time publication
  dates.
- **Order-book / microstructure features:** no data source in this project.
- **Cross-sectional rank features** (e.g. percentile of return within universe on date `t`):
  plausible future addition once the stock-level feature set is validated individually; adding
  them now would make fail-fast attribution ambiguous (is the rank version working because of
  the underlying feature or the ranking transform itself).

---

## 5. Next Step After This Document

Per the agreed process, do not generate the full feature dataset yet. The next action is
review of this specification, then:

1. Build only the features whose fail-fast criterion is testable immediately with existing
   code (1.1, 1.2, 1.3, 1.4, 1.6, 2.1, 2.2) on a small sample.
2. Extract C1's component signals (1.5 prerequisite) as a separate, reviewable task before
   including C1.
3. Implement `qqq_relative_trend` and `market_breadth` (2.3, 2.4) only after the group-1 set
   is validated, since they are the least-precedented additions.
4. Treat all of section 3 (sector-context) as gated behind 3.3's fail-fast result.
