# ADR-005: Use Calendar-Time Block Bootstrap for Dependent Evaluation Metrics

**Status:** Accepted for later model evaluation  
**Date:** 2026-07-11  
**Related documents:**

- `docs/02_mvp/MVP_1_Specification.md`

---

## Context

Stocks observed on the same date are not independent. They share market regime, macro
events, sector movement, liquidity conditions, and overlapping 20-day outcome windows.

Simple binomial standard errors can understate uncertainty.

---

## Decision

For later model evaluation, use dependence-aware uncertainty estimates. Calendar-time
block bootstrap is preferred for:

- Top 5% lift;
- daily Top 3 hit rate;
- at-least-one-of-Top-3 hit rate;
- other candidate-selection metrics with same-date dependence.

The exact block length must be selected before the locked test, using validation data only.

---

## Rationale

Calendar-time blocks better preserve common market shocks and overlapping event windows
than independent observation-level resampling.

---

## Consequences

GO / CONDITIONAL GO / STOP decisions should not rely only on point estimates.

Evaluation reports should show uncertainty ranges where practical.

