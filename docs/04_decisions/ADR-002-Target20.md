# ADR-002: Use +20% Within 20 Trading Days as the Initial Target

**Status:** Accepted  
**Date:** 2026-07-11  
**Related documents:**

- `docs/02_mvp/MVP_1_Specification.md`

---

## Context

The project needs one concrete target for MVP 1. Earlier research used triple-barrier
labels for signal validation, but MVP 1 requires a user-aligned target that can support a
future prediction and ranking system.

The user's practical goal is to find stocks with meaningful short-term upside, not merely
small positive returns.

---

## Decision

Use the following target for MVP 1:

```text
Positive label = stock reaches entry_price * 1.20 within 20 trading days.
```

Entry price is defined separately in ADR-003.

---

## Options Considered

1. +5% within 10 trading days.
2. +10% within 20 trading days.
3. +20% within 20 trading days.
4. Triple-barrier success as the direct MVP model label.

Option 3 was selected for MVP 1.

---

## Rationale

+20% is meaningful enough to match the user's stated interest in stocks with strong
short-term upside potential. The 20 trading day horizon is short enough to remain a swing
opportunity and long enough to allow non-intraday signals to work.

The target may prove too rare. That is why MVP 1 starts with a dataset audit rather than
model training.

---

## Consequences

The audit must explicitly check whether this target is trainable:

- positive label count;
- deduplicated event count;
- temporal distribution;
- regime distribution;
- ticker and calendar-time concentration.

If the target is too rare or too clustered, the audit may return
`NOT_TRAINABLE_AS_DEFINED` or `CONDITIONALLY_TRAINABLE`.

