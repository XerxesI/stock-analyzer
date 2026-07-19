# EXP-005 Implementation Checklist

Maps every frozen requirement in
`EXP-005_Portfolio_Policy_Feasibility_Pilot.md` (Revision 5, FROZEN) to a concrete
source module, table/repository, configuration object, test, or generated artifact.
This checklist tracks implementation progress only -- it does not redefine, reinterpret,
or extend the frozen plan. Any conflict between this checklist and the frozen document
is resolved in favor of the frozen document.

Module layout (new code lives under `stock_analyzer/sandbox/exp005/`; existing sandbox
services -- `CandidateService`, `EntryService`, `MonitoringService`, `ReplayService`,
`SandboxRepository`, core `schema.py` -- are reused via the seams already frozen in the
pre-registration, not duplicated):

```text
stock_analyzer/sandbox/exp005/
    config.py                          Stage 1
    domain/
        admission.py                     Stage 2 (PortfolioAdmission, SlotReservation)
        execution.py                      Stage 2 (Execution)
        equity_snapshot.py                 Stage 2 (PortfolioEquitySnapshot)
    infrastructure/
        schema.py                          Stage 2 (DDL for the 4 new tables)
        repository.py                       Stage 3-5 (PortfolioRepository)
    application/
        admission_orchestrator.py            Stage 4 (AdmissionOrchestrator,
                                                DefaultAdmissionOrchestrator,
                                                CapacityAdmissionOrchestrator,
                                                PortfolioLedger)
        variant_runner.py                     Stage 7 (Variant B / D orchestration)
        replay.py                              Stage 8 (frozen-artifact replay entry
                                                point)
    manifest.py                                Stage 9 (Experiment Manifest)
    freeze_validation.py                        Stage 10
    diagnostics/
        diagnostics.py                           Stage 11 (mediated loading boundary)
        mfe_mae.py                                Stage 12
        entry_timing.py                            Stage 13
        hold_quality.py                             Stage 13
        sell_quality.py                              Stage 13
        opportunity_cost.py                           Stage 13
        report_generator.py                            Stage 14
```

| Frozen requirement | Location |
|---|---|
| `portfolio_admissions` | `exp005/infrastructure/schema.py`, `exp005/domain/admission.py::PortfolioAdmission` |
| `slot_reservations` | `exp005/infrastructure/schema.py`, `exp005/domain/admission.py::SlotReservation` |
| `portfolio_equity_snapshots` | `exp005/infrastructure/schema.py`, `exp005/domain/equity_snapshot.py::PortfolioEquitySnapshot` |
| `executions` | `exp005/infrastructure/schema.py`, `exp005/domain/execution.py::Execution` |
| Atomic admission/reservation/order creation | `exp005/infrastructure/repository.py::PortfolioRepository.create_admission_acceptance`, `create_admission_rejection`; orphan check: `PortfolioRepository.check_admission_integrity` |
| Candidate and decision audit records | reused unchanged: `ranked_candidates`, `position_snapshots`, `recommendations` (core `sqlite_repository.py`) + new `executions` (raw/effective price) |
| Variant B | `exp005/application/variant_runner.py` + existing `Model2PredictionAdapter` (reused, unmodified) |
| Variant D | `exp005/application/variant_runner.py` + `RankingControlAdapter` (Stage 7, deterministic hash-based scoring per Section 11.4) |
| Portfolio replay | `exp005/application/replay.py`, reusing core `ReplayService`/`CandidateService`/`EntryService`/`MonitoringService` with the frozen seam (Section 11.2-11.3) |
| Experiment Manifest | `exp005/manifest.py` |
| Post-hoc diagnostics | `exp005/diagnostics/` (Stages 11-13) |
| Report generation | `exp005/diagnostics/report_generator.py` (Stage 14) |
| Import-boundary enforcement | test: `tests/test_exp005_diagnostics_import_boundary.py` (Stage 11) |
| Deterministic-output validation | tests in Stage 8 (replay), Stage 9 (manifest), Stage 14 (reports) |
| Censoring | `exp005/diagnostics/mfe_mae.py` + shared censoring helper (Stage 12), applied consistently across Stages 12-13 |
| Orphan detection | `exp005/infrastructure/repository.py::PortfolioRepository.check_admission_integrity` (Stage 4) |
| Accounting reconciliation | `exp005/infrastructure/repository.py` reconciliation helper + tests (Stage 5) |

## Stage sequencing (must leave the test suite green before advancing)

0. This checklist (documentation only).
1. Configuration and frozen contracts.
2. Persistence schema (4 new tables).
3. Repository layer.
4. Atomic admission transaction + orphan check.
5. Execution ledger and accounting.
6. Portfolio state and equity snapshots.
7. Variant B and Variant D orchestration.
8. Replay and determinism.
9. Experiment Manifest.
10. Freeze validation gate. **Stop and report here before running any real
    experiment.**
11. Post-hoc diagnostics package skeleton + import-boundary test.
12. MFE/MAE.
13. Remaining decision-quality diagnostics.
14. Report generation.
15. Synthetic end-to-end fixture covering the full pipeline; completion report.

## Status

- [x] Stage 0 -- this checklist
- [x] Stage 1 -- typed config + canonical hashing (fixed to exact/lossless serialization)
- [x] Stage 2 -- four new tables, FK-cycle fixed
- [x] Stage 3 -- repository layer
- [x] Stage 4 -- atomic admission transaction + orphan check
- [x] Stage 5 -- execution accounting
- [ ] Stage 6
- [ ] Stage 7
- [ ] Stage 8
- [ ] Stage 9
- [ ] Stage 10 (stop point -- no real run until this passes)
- [ ] Stage 11
- [ ] Stage 12
- [ ] Stage 13
- [ ] Stage 14
- [ ] Stage 15 (synthetic fixture + completion report)
