# ADR-004: Run Dataset Audit Before Any Prediction Model

**Status:** Accepted  
**Date:** 2026-07-11  
**Related documents:**

- `docs/02_mvp/MVP_1_Specification.md`

---

## Context

The project could proceed directly to Logistic Regression or Gradient Boosting. However,
doing so before auditing the dataset risks wasting time on a target that is too rare,
leaky, distorted by corporate actions, or not temporally testable.

---

## Decision

The first implementation deliverable for MVP 1 is the SWING_20 Dataset Audit.

No predictive model is trained before the audit returns:

- `TRAINABLE`; or
- `CONDITIONALLY_TRAINABLE`.

---

## Rationale

The audit is the cheapest way to determine whether the problem is learnable with current
data. It checks:

- label frequency;
- deduplicated event counts;
- temporal stability;
- corporate-action consistency;
- point-in-time risks;
- date-specific baseline rates;
- locked-test viability.

---

## Consequences

The next engineering task is not LightGBM, not API work, and not frontend work.

The next engineering task is:

```text
SWING_20 Dataset Audit generator
```

