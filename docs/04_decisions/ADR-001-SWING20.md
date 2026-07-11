# ADR-001: Use SWING_20 as MVP 1 Opportunity Type

**Status:** Accepted  
**Date:** 2026-07-11  
**Related documents:**

- `docs/00_goal/Stock_Analyzer_Goal.md`
- `docs/02_mvp/MVP_1_Specification.md`

---

## Context

The project goal is to build an investment decision support system, not a generic
technical-indicator screener. Earlier research showed that individual signals can contain
small but meaningful information, but the next phase requires a concrete, falsifiable
prediction target.

The MVP must answer whether the system can rank stocks with materially better short-term
upside potential than baselines.

---

## Decision

Use `SWING_20` as the first MVP opportunity type.

Definition:

- liquid US stocks;
- next-day Open entry assumption;
- +20% upside target;
- 20 trading day horizon.

---

## Options Considered

1. Continue testing individual technical signals.
2. Build a broad recommendation system immediately.
3. Define one narrow opportunity type and audit/train against it.

Option 3 was selected.

---

## Rationale

`SWING_20` is narrow enough to audit and test, but directly connected to the user's goal:
finding stocks with meaningful near-term upside potential.

It creates a clear bridge from research signals to supervised prediction:

```text
point-in-time features → future target hit label → ranking model
```

---

## Consequences

The project will first build a SWING_20 Dataset Audit before model training.

No Recommendation API, frontend, Trade Planner, or Position Manager should be built until
the SWING_20 target is shown to be trainable or conditionally trainable.

