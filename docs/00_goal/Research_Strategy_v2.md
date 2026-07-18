# Research Strategy v2 — Building a Context-Aware AI for Swing Trading

**Status:** Accepted as the project's governing research philosophy
**Project:** stock-analyzer
**Scope:** how research is directed, evaluated, and decided across all opportunity types —
not a technical specification for any single MVP
**Related documents:**

- `docs/00_goal/Stock_Analyzer_Goal.md` (product vision this strategy serves)
- `docs/01_architecture/High_Level_Architecture.md` (system shape this strategy will refine,
  particularly the Opportunity Detection Engine and Context Engine sketched in sections 15
  and 28)
- `docs/02_mvp/SWING20_Dataset_Specification_v1.md`,
  `docs/03_research/SWING20_PointInTime_Feature_Specification_v1.md`,
  `docs/03_research/SWING20_Baseline_Evaluation_Plan_v1.md` (frozen MVP contracts this
  document does not modify)

This document sits above the MVP-level contracts. It does not change the SWING_20 Dataset
Specification, Feature Specification, or Baseline Evaluation Plan — those remain frozen. It
changes how research decisions get made from this point forward, for SWING_20 and for every
opportunity type that follows it.

---

## 1. Purpose

The objective of this project is not to build another stock screener or indicator
collection. The objective is to discover whether market data contains repeatable,
exploitable patterns that allow a systematic, AI-driven process to identify short-term swing
trading opportunities before they become obvious to the market.

The project is research-first and model-second. Every implementation exists only to answer a
research question.

---

## 2. Business Objective

The business objective is simple:

> Increase the probability that the daily Top-N recommendations achieve the defined
> opportunity target, with acceptable adverse excursion and turnover, materially more often
> than random and naive-momentum baselines.

This connects business value to four concrete things, not one vague word: target-hit
probability, lift over the random and naive-momentum baselines (already specified in
`docs/03_research/SWING20_Baseline_Evaluation_Plan_v1.md`), acceptable adverse excursion
(MAE), and acceptable turnover/costs. "Outperform" alone was ambiguous about which of these
it meant. This does **not** change the frozen SWING_20 target definition
(`docs/02_mvp/SWING20_Dataset_Specification_v1.md` section 5, entry-Open +20%/20-day) — it
only makes explicit what "business value" means when judging whether a hypothesis is worth
pursuing.

Success is **not** measured by model accuracy, F1 score, AUC, or elegant ML architecture.
Success is measured by whether the recommendations become genuinely more useful for a real
trader. Business value always dominates statistical elegance.

---

## 3. The North Star

The original assumption behind this project was:

> Technical indicators may predict future price movements.

The current research direction is different:

> Swing trading opportunities emerge from the interaction between stock-specific behavior
> and the surrounding market context, rather than from universal technical patterns.

This changes the philosophy of the project. The project is no longer searching for universal
indicators. The project is searching for **conditional market behavior** — under which
conditions a given stock-level signal produces an edge, not whether it produces one
everywhere, always.

---

## 4. Context Before Signal

The first design principle of the project:

> No technical signal is assumed to be universally predictive. Every signal must be
> evaluated inside its surrounding context.

Context includes:

- overall market regime;
- volatility regime;
- sector behavior;
- liquidity;
- relative strength;
- participation;
- market breadth.

The question is no longer *does VC3 work?* The question becomes *under which market
conditions does VC3 create statistical edge?*

---

## 5. Context Engine and Opportunity Engine

The long-term architecture consists of two independent systems. This section is a strategic
framing, not an implementation plan — the implementation plan is a separate deliverable
(`Context Engine Architecture Proposal v1`, in progress).

### 5.1 Context Engine

Determines:

- current market regime (Bull / Bear);
- volatility regime;
- sector leadership;
- market breadth;
- risk-on / risk-off environment.

The Context Engine never recommends stocks. It only describes the environment.

### 5.2 Opportunity Engine

Evaluates individual stocks inside the environment produced by the Context Engine. It
determines: *which stocks have an elevated probability of becoming successful swing trades
under the current conditions?*

This reframes the target architecture's existing Opportunity Detection Engine
(`High_Level_Architecture.md` section 15, `Pattern + Event + Context = Opportunity`) with
Context as an explicit, first-class upstream stage rather than one input alongside others.

---

## 6. Research Principles

Every research activity follows six principles.

**Principle 1 — Research serves business objectives.** Not the other way around.

**Principle 2 — Every hypothesis must be falsifiable.** A hypothesis that cannot fail is not
useful.

**Principle 3 — Negative results are valuable.** A rejected hypothesis removes uncertainty.
Removing uncertainty is progress.

**Principle 4 — Every failed hypothesis must produce an explicit decision.** Not always
another hypothesis in the same branch — the decision is one of the Fail-Fast Framework's
five non-continue outcomes (section 7): **Stop**, **Narrow**, **Reframe**, **Combine**, or
identify the highest-value next hypothesis. Research never ends with *it doesn't work* and
silence. It ends with a recorded decision — and sometimes the correct decision is closing
the branch entirely and moving to a different business question, not producing a variation
of the same idea.

**Principle 5 — Context is tested before complexity.** Before adding another feature or
another model, ask: is the missing information actually context?

**Principle 6 — Models do not create edge.** Models only exploit edge already present inside
the data.

---

## 7. Fail-Fast Framework

Research effort is expensive. Every completed experiment receives exactly one of six
outcomes:

| Outcome | Meaning |
|---|---|
| **Continue** | Evidence is strong. Continue deeper. |
| **Narrow** | The hypothesis is not universal. Restrict it to specific market conditions. |
| **Combine** | The hypothesis alone is weak. Combine it with other validated signals. |
| **Reframe** | The original question was incorrect. Replace it with a better question. |
| **Stop** | Additional work is unlikely to produce business value. Terminate the branch. |
| **Escalate** | Evidence is sufficiently strong. Promote it into the production candidate pipeline. |

---

## 8. Research Workflow

Every experiment follows the same lifecycle:

```text
Observation
    ↓
Hypothesis
    ↓
Experiment
    ↓
Validation
    ↓
Business Evaluation
    ↓
Decision
    ↓
Next Hypothesis
```

Not:

```text
Experiment → Experiment → Experiment
```

No experiment is repeated without a clear reason.

---

## 9. Decision Framework

Every research cycle ends with five explicit questions:

1. Did the experiment reduce uncertainty?
2. Did it improve business value?
3. Can additional testing materially change the conclusion?
4. Should the hypothesis continue?
5. What is the highest-value next hypothesis?

### 9.1 Research Review Format

Each research cycle should close with a short, explicit review, not only an analysis:

```text
Research Review
  Business value:          ★★★★★
  Statistical evidence:    ★★★★☆
  Risk of overfitting:     ★★☆☆☆
  Information gain:        ★★★★★
  Continue?                NO
  Reason:                  We already know enough.
  Next hypothesis:         Market Context Layer.
```

This is a required output of the research process, not an optional summary — it is what
prevents a research cycle from continuing past the point where it stopped producing
information.

---

## 10. Research Quality Gates

Every accepted result must satisfy:

- reproducible;
- point-in-time correct;
- free from look-ahead bias;
- independently testable;
- explainable;
- economically meaningful.

Statistical significance alone is never sufficient.

---

## 11. When to Stop Research

A research direction ends when continued investment is unlikely to change the decision —
this is the same standard already applied during the SWING_20 Dataset Audit phase (see
`docs/02_mvp/MVP_1_Specification.md` section 3.7, no silent optimization) extended to every
subsequent research question. The explicit failure mode this guards against: continuing to
test variations of an already-answered hypothesis because the next variation is easy to try,
not because it is likely to change the conclusion.

---

## 12. AI Collaboration

Multiple AI systems participate in this project, each with a different responsibility.

**Research AI** — evaluates hypotheses, challenges assumptions, suggests new research
directions, and is expected to say *this research direction has exhausted its information
value* when that is true, not to keep generating variations of a settled question.

**Engineering AI** — implements reproducible experiments, produces deterministic pipelines,
and maintains code quality.

**Human** — defines business objectives, approves strategic direction, and makes final
research decisions.

---

## 13. Guiding Principle

> The goal is not to prove a hypothesis correct. The goal is to reduce uncertainty as
> quickly as possible.

Many research projects try to prove that an idea works. This project tries to determine, as
fast as possible, whether an idea is worth further investment — a rejected hypothesis that
took one day to reject is a better outcome than a validated hypothesis that took a month to
confirm what a faster test would have shown was too weak to matter.

---

## 14. Long-Term Vision

The final objective is not to build a better indicator library. The final objective is to
build a context-aware decision system capable of continuously learning which market
environments produce statistically significant swing trading opportunities.

The system should eventually answer *what kind of market exists today?* before answering
*which stock should I buy?*
