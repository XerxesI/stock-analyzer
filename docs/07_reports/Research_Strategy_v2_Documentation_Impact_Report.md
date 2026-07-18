# Documentation Impact Report — Research Strategy v2 Integration

**Date:** 2026-07-18
**Trigger:** `docs/00_goal/Research_Strategy_v2.md` added as the project's governing research
philosophy after the SWING_20 Dataset Audit phase closed.

This report records what changed as a result, and — as important — what deliberately did
not change and why.

---

## 1. New Document

| File | Purpose |
|---|---|
| `docs/00_goal/Research_Strategy_v2.md` | New governing research philosophy: North Star, Context Before Signal, Context/Opportunity Engine framing, fail-fast framework, research workflow, decision framework, when to stop research, AI collaboration model. |

Placed under `docs/00_goal/` because it describes *why* the project does research and *how*
research is directed, not a specific technical solution — the same level as
`Stock_Analyzer_Goal.md`, not the MVP or research-cycle level below it.

---

## 2. Documents Changed (reference additions only)

All changes below are header/cross-reference additions or short pointer sentences. No
existing technical content, decision, or number was rewritten.

| File | Change | Why |
|---|---|---|
| `docs/00_goal/Stock_Analyzer_Goal.md` | Added `Research_Strategy_v2.md` to the header's Related documents; added one pointer sentence in section 5 (Research Foundation) | Section 5 already describes the research framework's role — it should point to the document that now governs it |
| `docs/01_architecture/High_Level_Architecture.md` | Added header reference; added a note under section 15 (Opportunity Detection Engine) and section 28 (Future Knowledge Graph / Context Engine) | These two sections already sketch a Context Engine / Opportunity Engine split (`Pattern + Event + Context = Opportunity`, "Future Knowledge Graph / Context Engine") — the new strategy elevates context from one input among several to an explicit first-class upstream stage. The notes point to the forthcoming `Context Engine Architecture Proposal v1` for the actual redesign rather than rewriting the diagrams here |
| `docs/02_mvp/MVP_1_Specification.md` | Added header reference only | Frozen document; the new strategy applies after this MVP's audit phase and does not change its content, per the strategy document's own scope statement |
| `docs/02_mvp/SWING20_Dataset_Specification_v1.md` | Added one line to existing Related documents list | Explicit "frozen contract" per instruction — reference only |
| `docs/03_research/SWING20_PointInTime_Feature_Specification_v1.md` | Added one line to existing Related documents list | Same — frozen contract, reference only. Worth noting: this document's own "Ground Rule" section (short, falsifiable, context-first feature list) already anticipated several Research Strategy v2 principles independently |
| `docs/03_research/SWING20_Baseline_Evaluation_Plan_v1.md` | Added one line to existing Related documents list | Same — frozen contract, reference only |

---

## 3. Documents Reviewed, Not Changed

| File | Why unchanged |
|---|---|
| `docs/README.md` | Describes folder purposes at a generic level and does not enumerate individual files within each folder; adding a specific file reference here would be inconsistent with how every other document in the tree is (not) listed. No content contradicts the new strategy. |
| `docs/04_decisions/ADR-001-SWING20.md` | Records why SWING_20 was chosen as the MVP opportunity type — a scope decision, not a claim about universal indicators. Compatible with the new strategy as-is. |
| `docs/04_decisions/ADR-002-Target20.md` | Records the +20%/20-day target definition — a label mechanism decision, orthogonal to context-first vs. universal-indicator research philosophy. |
| `docs/04_decisions/ADR-003-NextOpen.md` | Records the next-day-Open entry assumption — same category, mechanism-level, not contradicted. |
| `docs/04_decisions/ADR-004-DatasetAuditBeforeModel.md` | Records "audit before modeling" — this is a *precursor* of the new strategy's fail-fast discipline, not a conflict with it. |
| `docs/04_decisions/ADR-005-BlockBootstrap.md` | Records the calendar-time block bootstrap decision for dependent-observation uncertainty — a statistical-methodology decision, unaffected by the research-direction change. |

None of the five ADRs make a "universal indicator" claim or otherwise assume signals work
independent of context, so none needed correction — they document *mechanism* decisions
(target, entry, evaluation statistics), which sit a level below the research-philosophy
question Research Strategy v2 answers.

---

## 4. Explicitly Not Touched (per instruction)

`docs/02_mvp/SWING20_Dataset_Specification_v1.md`,
`docs/03_research/SWING20_PointInTime_Feature_Specification_v1.md`, and
`docs/03_research/SWING20_Baseline_Evaluation_Plan_v1.md` received only the single reference
line noted above. Their technical content (universe rules, label definitions, quarantine
policy, feature candidates, fail-fast criteria, baseline definitions, evaluation metrics) is
unchanged. They remain the frozen contracts for the SWING_20 MVP phase.

---

## 5. Next Step

Per the agreed two-stage plan, the next deliverable is `Context Engine Architecture
Proposal v1` — an architecture document (not an implementation) describing how a Context
Engine fits the existing SWING_20 architecture, its inputs/outputs, and how an Opportunity
Engine would consume them.
