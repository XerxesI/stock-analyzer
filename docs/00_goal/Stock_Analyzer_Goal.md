# Stock Analyzer Goal v1.0

**Status:** FROZEN as product-direction baseline  
**Project:** stock-analyzer  
**Scope:** target product vision, not MVP implementation detail

This document defines the purpose and long-term direction of Stock Analyzer. It separates
the product goal from the narrower MVP 1 implementation. MVP 1 is the first falsifiable
step toward this larger system.

---

## 1. Core Purpose

Stock Analyzer is not just a stock screener and not just a BUY/SELL indicator.

The goal is to build an investment decision support system that helps the user decide:

> Which action should be taken for each relevant stock today, given expected upside,
> downside risk, time horizon, confidence, portfolio constraints, and alternative
> opportunities?

The system should eventually support actions such as:

- buy;
- wait;
- add;
- reduce;
- sell;
- avoid;
- watchlist;
- no action.

The system should not force a recommendation when good opportunities are not available.
One of its important responsibilities is to say: **do nothing for now**.

---

## 2. Primary User Outcome

The user wants to find stocks with attractive short-term upside potential while avoiding
low-quality signals, overtrading, and hidden risk concentration.

In practical terms, the system should help answer:

- Which stocks have the best near-term upside potential?
- How large is the expected upside?
- What is the downside risk?
- How likely is the target to be reached?
- How long might the setup take to work?
- Is the opportunity better than alternatives?
- Is the signal supported by market, sector, volume, volatility, and price context?
- Does the current portfolio already contain too much similar exposure?
- Has the original trade thesis weakened or failed?

---

## 3. Strategic Shift

The project began with classic technical-analysis scoring:

```text
RSI + SMA + MACD + support = trade score
```

Research Cycle #1 showed that this simple static scoring approach is not enough. Some
signals were rejected, some were validated only in specific regimes, and some were useful
as context rather than direct entry triggers.

The project direction therefore changed:

```text
Indicator scoring
    ↓
Validated feature research
    ↓
Prediction
    ↓
Decision
    ↓
Trade planning
    ↓
Position and thesis management
```

This does not mean the earlier research failed. The research framework is one of the
project's strongest assets. It produces validated features, labels, and evidence that can
feed future machine-learning and decision layers.

---

## 4. Product Philosophy

### 4.1 Prediction and Decision Are Different

The system must distinguish prediction from recommendation.

Prediction answers:

```text
What is likely to happen?
```

Decision answers:

```text
What should we do about it?
```

Example prediction outputs:

- probability of +20% target within 20 trading days;
- probability of stop before target;
- expected return;
- expected downside;
- expected holding period;
- confidence interval;
- MFE / MAE expectations.

Example decision outputs:

- buy;
- wait;
- no action;
- reduce;
- sell;
- add to watchlist;
- position size;
- maximum acceptable entry price;
- exit plan.

### 4.2 Expected Value Matters More Than Raw Confidence

Confidence is not the same as opportunity quality.

A setup with high confidence but small upside may be less attractive than a lower
confidence setup with much larger expected payoff and controlled downside.

The long-term system should rank opportunities by **Expected Net Value**, not only by
probability or confidence.

Expected Net Value should consider:

- probability of success;
- payoff size;
- downside risk;
- transaction costs;
- slippage;
- opportunity cost;
- portfolio constraints;
- current market regime.

### 4.3 Capital Is Limited

The question is not only:

```text
Is this stock a BUY?
```

The better question is:

```text
Is this stock a better use of capital than the alternatives?
```

The system must eventually account for opportunity cost. A position may be rejected or
reduced not because it is bad, but because a better risk-adjusted opportunity exists.

### 4.4 No Forced Trading

Professional decision systems must be able to stay inactive.

If the opportunity set is weak, the system should recommend waiting rather than forcing
trades. Avoiding bad trades is part of performance.

---

## 5. Research Foundation

The project's research framework remains a permanent core component.

Its role is to:

- generate hypotheses;
- test signals with strict labeling;
- avoid look-ahead bias;
- separate exploratory and confirmatory analysis;
- use train / validation / locked-test discipline;
- archive rejected signals;
- promote validated signals into the feature set;
- document negative results.

Research Cycle #1 produced important lessons:

- fixed forward-return was the wrong target for swing-trade validation;
- triple-barrier and MFE/MAE labels are more informative;
- trend v1 was rejected for the tested swing target;
- support is a weak but useful context feature;
- momentum is regime-specific, mainly Bear-regime useful;
- RVOL contains Bull-regime informational content;
- volatility compression requires an activation layer;
- simple static indicator thresholds often fail as direct entry rules;
- portfolio-level constraints matter as much as signal quality.

These findings are not final trading rules. They are validated or rejected building blocks.

---

## 6. Target System Capabilities

The long-term Stock Analyzer should include the following capabilities.

### 6.1 Candidate Discovery

Identify potentially interesting stocks from a broad US equity universe.

Candidate discovery should support:

- full-market screening;
- liquidity filtering;
- regime-aware logic;
- opportunity type selection;
- two-stage screening: fast screening followed by deeper analysis.

### 6.2 Prediction

Estimate future outcomes for each candidate.

Prediction should eventually include:

- target-hit probability;
- expected return;
- expected downside;
- MFE and MAE expectations;
- expected holding time;
- probability of stop before target;
- confidence and uncertainty.

### 6.3 Decision

Convert predictions into actionable decisions.

The Decision Engine should consider:

- expected net value;
- opportunity cost;
- transaction costs;
- market regime;
- portfolio exposure;
- risk limits;
- signal confidence;
- alternative opportunities.

### 6.4 Trade Planning

Create a practical plan for each accepted opportunity:

- entry price;
- maximum acceptable buy price;
- target;
- stop;
- position size;
- invalidation conditions;
- expected holding period;
- exit rules.

### 6.5 Portfolio Awareness

Recommendations must not be evaluated in isolation.

The system should account for:

- available capital;
- existing positions;
- sector concentration;
- theme concentration;
- correlation;
- market exposure;
- simultaneous signals;
- calendar-time clustering;
- drawdown risk.

### 6.6 Position and Thesis Monitoring

An open position should be tied to an investment thesis.

The system should track:

- why the position was opened;
- which opportunity type triggered it;
- which assumptions must remain true;
- whether the thesis is improving or weakening;
- whether a better opportunity has appeared;
- whether to hold, add, reduce, or exit.

### 6.7 Learning and Feedback

The system should learn from outcomes in a controlled way.

Feedback should include:

- prediction accuracy;
- calibration drift;
- feature drift;
- strategy performance;
- regime-specific performance;
- realized vs expected MFE/MAE;
- failed thesis analysis;
- model retraining candidates.

Retraining must not be automatic without validation. The research and validation process
remains the gatekeeper.

---

## 7. Opportunity Types

The system should support multiple opportunity types over time. Each opportunity type may
have its own:

- features;
- labels;
- model;
- entry logic;
- exit logic;
- risk logic;
- position sizing logic.

Initial opportunity type:

```text
SWING_20
```

Definition:

- liquid US stocks;
- at least +20% upside target;
- 20 trading day horizon;
- next-day Open entry assumption.

Future possible opportunity types:

- short swing reversal;
- breakout continuation;
- earnings catalyst;
- volatility compression expansion;
- recovery candidate;
- longer-term momentum;
- defensive rotation;
- high-conviction thematic setup.

---

## 8. MVP Relationship

MVP 1 is intentionally narrow.

MVP 1 does not attempt to build the whole decision platform. It asks one foundational
question:

> Is the SWING_20 target trainable with the current data?

The first deliverable is:

```text
SWING_20 Dataset Audit
```

If the audit says `NOT_TRAINABLE_AS_DEFINED`, the project must redesign the target,
universe, or data setup before modeling.

If the audit says `TRAINABLE` or `CONDITIONALLY_TRAINABLE`, the project may proceed to
baseline models and then a locked temporal test.

---

## 9. Success Criteria for the Product Direction

The long-term system is successful if it can:

- find better candidates than baseline screening;
- express uncertainty honestly;
- avoid forcing trades;
- explain why a candidate is attractive;
- compare opportunities against each other;
- account for portfolio risk;
- monitor open positions against their thesis;
- improve through validated research rather than ad hoc tuning.

The system should not be judged only by hit rate. It should be judged by risk-adjusted,
cost-aware, portfolio-aware expected value.

---

## 10. Non-Goals

Stock Analyzer is not intended to be:

- a guaranteed profit machine;
- a black-box signal generator with no explanation;
- a day-trading order-flow system;
- a tool that always outputs BUY recommendations;
- a system that optimizes backtests by repeatedly mining the test period;
- an automated trading bot in its early phases.

---

## 11. Project Principles

These principles should guide future research, modeling, architecture, and product
decisions.

1. Never optimize on the locked test.
2. Never change labels after seeing model results without creating a new documented
   research version.
3. Always separate exploratory research from confirmatory validation.
4. Prediction is not recommendation.
5. Every recommendation must be explainable.
6. Every model must have a baseline.
7. Every feature must have a research history or an explicit exploratory status.
8. Every experiment must be reproducible.
9. Point-in-time correctness has priority over feature quantity.
10. Prefer simpler models until complexity is justified.
11. Do not treat confidence as expected value.
12. Do not force trades when the opportunity set is weak.
13. Keep rejected hypotheses visible; negative results are project knowledge.
14. Treat portfolio concentration and calendar-time clustering as first-class risks.
15. Use the research process as the gatekeeper for production features and models.

---

## 12. Guiding Principle

The most important product principle:

> Stock Analyzer does not optimize the number of BUY signals or even raw hit rate. It
> optimizes the expected net value of possible actions under limited capital, uncertainty,
> market regime, and portfolio constraints.

This principle should guide future architecture, modeling, and UX decisions.
