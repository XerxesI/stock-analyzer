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
        admission_orchestrator.py            Stage 4 (AdmissionTransactionService --
                                                the actual atomic-write class; see
                                                Stage 6 note below on naming)
        portfolio_accounting_seam.py           Stage 6 (Exp005AccountingSeam --
                                                 implements the core
                                                 PortfolioAccountingSeam Protocol;
                                                 see stock_analyzer/sandbox/
                                                 application/accounting_seam.py)
        portfolio_ledger.py                     Stage 6 (PortfolioLedger -- cash/
                                                  reserved/mark-to-market equity,
                                                  implements CashAvailabilityProvider)
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

**Naming note (discovered during Stage 6):** Section 8.2 names the atomic-write
methods `SandboxRepository.create_admission_acceptance`/`create_admission_rejection`.
The actual Stage 4 implementation is `AdmissionTransactionService.admit_candidate`
(`exp005/application/admission_orchestrator.py`), calling
`PortfolioRepository.insert_admission`/`insert_reservation` plus
`SandboxRepository._insert_entry_order_row` inside one transaction it owns -- the
same atomicity/idempotency/orphan-freedom guarantees Section 8.2 requires, under
different names. This is a naming difference only, not a behavioral deviation.

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
- [x] Stage 6 -- portfolio ledger, equity snapshots, aligned dual-accounting sizing seam
- [x] Stage 7 -- admission orchestrator seam (CandidateService), RankingControlAdapter (Variant D), CapacityAdmissionOrchestrator
- [x] Stage 8 -- replay entry point (build_exp005_replay_services), day-start/day-complete hooks on ReplayService, determinism/resume tests
- [x] Stage 9 -- Experiment Manifest generator (exp005/manifest.py)
- [x] Stage 10 -- freeze validation gate (exp005/freeze_validation.py). **Stop
      point reached: no real EXP-005 run has occurred. Independent review
      required before any real Variant B / Variant D execution.**
      Stage 9-10 closure (3 confirmed P1s from independent review, fixed):
      (1) CandidateService/EntryService/MonitoringService now require an
      injected MarketDataProvider seam (application/market_data_provider.py);
      EXP-005 wires a FrozenSwing20MarketDataProvider with no live/network
      fallback, hash-verified against its upstream SWING_20 snapshot at
      construction (exp005/infrastructure/frozen_market_data_provider.py,
      frozen_artifacts.py). (2) exp005/manifest.py rewritten: every hash
      (universe/ohlc/signal/eligibility/feature) now comes from physically
      re-verified snapshot lineage, not an arbitrary raw-file hash; added
      calendar_version (derived from the frozen prices artifact's own session
      dates); manifest now persists as a canonical, round-tripping JSON
      artifact. (3) exp005/application/real_run.py is the one enforced
      execution boundary (verify_real_run_preconditions/run_real_experiment):
      commit/working-tree/schema/artifact-hash/config/seed/calendar checks all
      run BEFORE any service is constructed or ReplayService.run is called.

      Second Stage 9-10 closure (3 further confirmed P1 identity/gate bypasses,
      fixed): (1) trading_dates were only checked against calendar_version's
      endpoints, so an internal session could be silently omitted -- the
      manifest now freezes signal_start_date/signal_end_date/
      outcome_data_end_date/calendar_session_count, and the gate recomputes the
      exact ordered session sequence from the re-verified frozen prices artifact
      (manifest.compute_frozen_calendar) and requires trading_dates to equal it
      element-for-element. Calendar-identity naming corrected
      (FROZEN_SWING20_PRICES_SESSION_CALENDAR, not a separately-sourced SPY
      series -- see manifest.py's module docstring for the documented
      clarification). (2) the gate only checked control_seed_list/
      feasibility_criteria/diagnostic_definitions were non-empty, so a manifest
      with invented-but-non-empty values passed -- now checked by exact equality
      against DEFAULT_CONTROL_SEEDS / exp005_config.feasibility_criteria.
      canonical() / manifest.build_canonical_diagnostic_definitions (the ONE
      function both the manifest builder and the gate use, so they cannot
      drift). (3) run_real_experiment accepted an arbitrary caller-supplied
      model_adapter/universe_provider and a separate replay_id argument that
      could disagree with replay_metadata_template -- both removed; the model
      adapter and feature-universe provider are now constructed internally from
      the verified feature snapshot's own features.parquet, manifest.
      model_version is checked against the running MODEL_VERSION constant, and
      replay_id is derived solely from replay_metadata_template. The manifest is
      now loaded from a persisted artifact file (read_manifest_artifact), never
      an in-memory object, and the replay's configuration identity incorporates
      that artifact file's own hash. code_commit_sha/working_tree_is_clean_fn
      public override parameters were removed entirely (Point 4) -- production
      always reads real git state; tests monkeypatch the private
      _current_code_commit_sha/_working_tree_is_clean functions directly.

      Third Stage 9-10 closure (1 confirmed P1 provenance defect + 1 schema-
      boundary gap, fixed): (1) run_real_experiment previously overwrote only
      configuration_json/configuration_hash on the final ReplayMetadata,
      leaving code_commit_sha/model_version/feature_snapshot_id/
      market_data_snapshot_id/signal_start_date/signal_end_date/
      outcome_data_end_date as whatever the caller's template carried (often
      None) -- a correctly-gated real run would still persist a replay row
      with empty primary provenance fields. Now every one of those fields is
      unconditionally overwritten from the verified manifest; only replay_id/
      classification/started_at remain caller-owned. Verified against both
      the in-memory object reaching ReplayService.run and the actually
      persisted replay_metadata row (an integration-level test lets
      build_exp005_replay_services construct for real). (2) the gate compared
      manifest schema versions only against code constants, never against the
      SUPPLIED CONNECTION's actual schema_meta/decision-audit physical shape
      -- a malformed or uninitialized connection would only fail later,
      ungracefully, deep inside construction. verify_database_schema_matches_
      manifest now runs the existing idempotent init_db/init_exp005_schema
      (physical verification, not label-trusting) against the actual
      connection before anything else, reads back both recorded versions,
      requires them to equal the manifest, and requires PRAGMA foreign_keys=ON
      (checked, never silently enabled).
- [ ] Stage 11
- [ ] Stage 12
- [ ] Stage 13
- [ ] Stage 14
- [ ] Stage 15 (synthetic fixture + completion report)
