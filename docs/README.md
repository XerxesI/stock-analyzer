# Stock Analyzer Documentation

This folder is organized as a layered documentation hierarchy.

```text
00_goal/
  Why the system exists and what product outcome it should optimize.

01_architecture/
  Target high-level architecture and long-term system structure.

02_mvp/
  The next concrete implementation scope and falsifiable MVP specification.

03_research/
  Research protocols, validation cycles, experiments, and findings.

04_decisions/
  Architecture Decision Records (ADRs) for important project decisions.

05_api/
  Future API specifications.

06_models/
  Future model cards, training reports, and calibration reports.

07_reports/
  Generated or reviewed reports.

08_glossary/
  Shared definitions for important project terms.

09_experiments/
  Templates and records for concrete experiment runs.

10_roadmap/
  Current, next, later, and future project roadmap.
```

The current hierarchy is:

```text
Goal
  ↓
Architecture
  ↓
MVP
  ↓
Research / Decisions / Experiments / Reports
```

New implementation work should reference the relevant MVP and ADR documents rather than
relying on chat history.
