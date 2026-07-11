# Stock Analyzer Glossary

**Status:** Living reference  
**Purpose:** shared terminology for research, architecture, MVP work, reports, and future
modeling.

This glossary defines the core terms used across the project. It should be updated when a
new term becomes important enough to appear in research reports, model outputs, or user
facing recommendations.

---

## Action

A possible decision produced by the future Decision Engine.

Examples:

- buy;
- wait;
- avoid;
- watchlist;
- add;
- reduce;
- sell;
- no action.

---

## Candidate

A stock-date pair that passes screening and is considered for deeper analysis or ranking.

A candidate is not automatically a recommendation.

---

## Coverage

How often the system produces usable candidates or signals.

Examples:

- actionable days per month;
- average candidates per day;
- deduplicated setups per year.

High precision with extremely low coverage may not be practically useful.

---

## Deduplicated Event

A grouped economic event created from overlapping raw observations.

Example:

One major 20% move may create ten consecutive positive daily labels for the same ticker.
Those ten raw observations may represent one deduplicated event.

---

## Expected Net Value

The expected value of an action after considering:

- probability of success;
- payoff size;
- downside risk;
- transaction costs;
- slippage;
- opportunity cost;
- portfolio constraints.

This is more important than raw confidence.

---

## Feature

A point-in-time variable used by a model or signal.

Features must use only information available at or before the signal date.

---

## Label

A future outcome assigned to a historical observation for research or model training.

Examples:

- target hit within 20 trading days;
- triple-barrier outcome;
- MFE;
- MAE;
- close return over horizon.

Labels may use future data because they describe outcomes. Labels must never leak into
features.

---

## Lift

The improvement of a selected group compared with a baseline.

Example:

```text
lift = selected_hit_rate / baseline_hit_rate
```

Lift should be evaluated together with absolute uplift and uncertainty.

---

## Locked Test

A final untouched evaluation period or sample used only after the model, features,
selection rule, calibration, and evaluation criteria are frozen.

The locked test must not be used for feature selection or parameter tuning.

---

## Observation

A single point-in-time ticker-date row.

An observation can be valid for model training, but adjacent observations may not be
economically independent.

---

## Opportunity

A market situation that may justify prediction and decision analysis.

Opportunity is broader than a technical pattern. It may include:

- pattern;
- event;
- context;
- market regime;
- volume behavior;
- volatility behavior.

---

## Opportunity Type

A defined strategy family with its own target, horizon, labels, features, and evaluation
logic.

Initial opportunity type:

```text
SWING_20
```

---

## Point-in-Time Correctness

The requirement that every feature, universe decision, and model input uses only
information that would have been available at the signal date.

Point-in-time correctness has priority over feature quantity.

---

## Prediction

An estimate of future outcomes.

Examples:

- probability of target hit;
- expected return;
- expected downside;
- expected holding time.

Prediction is not recommendation.

---

## Recommendation

A user-facing explanation of a decision.

A recommendation should include the action, reasoning, expected value, confidence, target,
risk, trade plan, and relevant caveats.

---

## Research Registry

A record of research questions, hypotheses, results, status, and links to artifacts.

The Research Registry tracks research history, not only validated features.

---

## Signal

A feature or rule designed to capture a market phenomenon.

A signal may be:

- candidate;
- promising;
- conditional;
- validated;
- archived;
- rejected.

---

## SWING_20

The first MVP opportunity type.

Definition:

- liquid US stocks;
- next-day Open entry;
- +20% target;
- 20 trading day horizon.

---

## Target Hit

A positive outcome where the configured target is reached within the configured horizon.

For SWING_20:

```text
future High reaches entry_price * 1.20 within 20 trading days
```

---

## Validation

Evaluation on data that was not used to fit the model or tune a hypothesis.

Validation is distinct from exploratory research.

