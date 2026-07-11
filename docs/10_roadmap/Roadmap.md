# Stock Analyzer Roadmap

**Status:** Living roadmap  
**Purpose:** show where the project is now and what comes next.

This roadmap is intentionally high level. Detailed implementation instructions belong in
MVP specifications, ADRs, and experiment plans.

---

## Completed

- Initial technical indicator based stock-analysis prototype.
- Research Protocol v1.x for swing-trade signal validation.
- Research Cycle #1.
- Validated and rejected several signal families.
- Established core research lessons:
  - target definition matters more than indicator choice;
  - fixed forward return was not sufficient for swing validation;
  - point-in-time discipline and locked tests are mandatory;
  - portfolio architecture matters after signal validation.
- Created documentation hierarchy:
  - Goal;
  - Architecture;
  - MVP;
  - Research;
  - Decisions.
- Created initial ADRs:
  - SWING_20;
  - +20% target;
  - next-day Open entry;
  - dataset audit before modeling;
  - block bootstrap for dependent evaluation.

---

## Current

### MVP 1: SWING_20 Dataset Audit

Current focus:

```text
Determine whether the SWING_20 target is trainable with current data.
```

Immediate deliverables:

- `artifacts/swing_20_dataset_audit.json`
- `artifacts/swing_20_dataset_audit.md`

The audit must decide:

- `TRAINABLE`;
- `CONDITIONALLY_TRAINABLE`;
- `NOT_TRAINABLE_AS_DEFINED`.

No prediction model is built before this audit is reviewed.

---

## Next

If MVP 1 audit is accepted:

1. Implement frozen baselines.
2. Implement Logistic Regression baseline.
3. Implement Gradient Boosting model.
4. Run validation ablations.
5. Fit calibration on validation only.
6. Freeze one configuration.
7. Run temporal locked test once.
8. Produce GO / CONDITIONAL GO / STOP report.

If MVP 1 audit is not accepted:

1. Identify hard blockers.
2. Redesign target, universe, data source, or horizon.
3. Create ADR for the redesign.
4. Re-run dataset audit.

---

## Later

After SWING_20 modeling has evidence:

- Prediction Engine prototype.
- Decision Engine prototype.
- Recommendation Composer.
- Strategy Registry implementation.
- Feature Store and Label Store hardening.
- Research Registry implementation.
- Model reports and model cards.
- Paper-trading workflow.
- Portfolio-aware candidate selection.

---

## Future

Longer-term capabilities:

- multiple opportunity types;
- thesis management;
- portfolio engine;
- knowledge graph / context engine;
- LLM-supported qualitative context;
- model drift monitoring;
- feedback and attribution system;
- controlled retraining workflow;
- frontend and API experience.

---

## Current Project Principle

Do not expand the product surface before the SWING_20 trainability question is answered.

