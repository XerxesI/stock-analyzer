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
        financial_performance.py                        Stage 11-15 closure (Section 10 --
                                                          missing from the original Stage 14;
                                                          see Status below)
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
| Report generation | `exp005/diagnostics/report_generator.py` (Stage 14, decision-quality) + `exp005/diagnostics/financial_performance.py` (Stage 11-15 closure, Section 10 financial feasibility) |
| Import-boundary enforcement | test: `tests/test_exp005_diagnostics_import_boundary.py` (Stage 11) |
| Deterministic-output validation | tests in Stage 8 (replay), Stage 9 (manifest), Stage 14 (reports) |
| Censoring | `exp005/diagnostics/_shared.py::compute_forward_horizon` (Stage 13), applied consistently to every fixed-horizon post-hoc outcome in Sections 21-24 (Section 20's MFE/MAE complete path is not fixed-horizon and is not censored the same way -- see mfe_mae.py's module docstring) |
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

      **Stage 9-10 independently reviewed and locked (2026-07-19), 4 closure
      cycles.** No real EXP-005 replay or P&L has been produced. The final
      manifest commit that actually runs a real replay may only be generated
      after Stage 15 completes and passes its own independent review.
- [x] Stage 11 -- diagnostics package skeleton (exp005/diagnostics/diagnostics.py:
      DiagnosticsContext, load_diagnostics_context -- Section 30's pure-function
      loading boundary, re-verifies the frozen prices artifact against the
      manifest) + import-isolation test (statically walks the import graph of
      every decision-time module, direct and transitive, per Section 26)
- [x] Stage 12 -- MFE/MAE diagnostics (exp005/diagnostics/mfe_mae.py::compute_mfe_mae).
      Holding window derived post-hoc from the frozen prices artifact, per
      Section 20's entry/exit-session ambiguity rule: FILLED_AT_OPEN includes
      the entry session, FILLED_AT_CEILING excludes it (window starts the next
      session); for closed positions SELL_TIME includes the exit session,
      SELL_TARGET includes it only when that session's own Open >= target_price
      (reconstructed via the same open-first branch MonitoringService.
      _check_target uses -- otherwise the exit session is excluded and the
      window ends the previous session); open positions run through
      manifest.outcome_data_end_date. effective_entry_price/effective_exit_price
      always come from EXP-005's own executions ledger, never core's raw
      entry_price/exit_price. An empty window (possible when a
      FILLED_AT_CEILING entry is immediately followed by an ambiguous
      SELL_TARGET exit on the very next session) raises
      MfeMaeComputationError rather than silently reporting zero/undefined
      values; exit_efficiency is None (not inf/NaN) when mfe_pct == 0.
      tests/test_exp005_mfe_mae.py: 10 hand-computed scenarios (unambiguous
      open+time exit baseline; ceiling-entry exclusion; unambiguous
      open-triggered target exit; ambiguous intraday-triggered target exit
      exclusion; open-position window to outcome_data_end_date; open position
      with no current_close falling back to entry price; empty-window
      MfeMaeComputationError; exit_efficiency=None at mfe_pct==0; missing
      BUY/SELL execution errors), all pass with exact hand-computed
      MFE/MAE/session-count values.
- [x] Stage 13 -- remaining decision-quality diagnostics. A new shared module,
      exp005/diagnostics/_shared.py, factors out symbol_sessions/next_session/
      previous_session (originally private to mfe_mae.py, Stage 12) plus
      Section 27's censoring primitive: full_market_calendar (the sorted union
      of session dates across every symbol in the frozen prices artifact) and
      compute_forward_horizon, which classifies every fixed-horizon post-hoc
      outcome (Sections 21-24) as not censored, MISSING_MARKET_DATA (a genuine
      gap in this symbol's own data within an otherwise in-window horizon --
      takes priority when both conditions hold), or END_OF_EXPERIMENT (the
      horizon's nominal sessions run past outcome_data_end_date or the
      calendar's own end). mfe_mae.py now imports these instead of its own
      private copies (10/10 tests unchanged after the refactor).
      - exp005/diagnostics/sell_quality.py (Section 21): compute_sell_quality
        for closed positions, 1/5/10/20-session forward horizons from the exit
        session -- close-to-close return, max High/min Low excursion (price +
        pct), target-reachability, is_censored, all relative to EXP-005's own
        effective_exit_price (executions ledger, never core's raw exit_price).
      - exp005/diagnostics/hold_quality.py (Section 22): compute_hold_quality
        for HOLD position_snapshots, 1/5/10-session horizons from the
        snapshot's own as_of_date/close_price, plus a per-snapshot
        eventual_outcome (PROFITABLE/ADVERSE from EXP-005's own effective
        entry/exit prices when the position later closed, UNRESOLVED when it
        never did within the frozen replay -- distinct from a genuinely
        adverse outcome). compute_hold_quality_for_position batches over one
        position's own HOLD snapshots.
      - exp005/diagnostics/entry_timing.py (Section 23): the largest module --
        compute_entry_timing_for_filled_order (signal-close entry gap against
        the next session's own open, independent of which session actually
        filled; raw/effective fill price and slippage from executions; fill
        percentile within the fill session's own range via
        entry_order_attempts; forward return/MFE/MAE at 1/5/10/20 sessions
        applying Section 20's entry-session-ambiguity rule to the horizon
        window itself, not just the complete-path case; whether the +20%
        target was reached within the position's actual holding horizon) and
        compute_entry_timing_for_expired_order (ceiling distance from
        entry_order_attempts; hypothetical MFE/MAE tracked forward from the
        order's own expiry date using the ceiling price as reference).
      - exp005/diagnostics/opportunity_cost.py (Section 24):
        compute_opportunity_cost for NO_CAPACITY portfolio_admissions rows --
        capacity state at rejection from that day's portfolio_equity_snapshots
        row; which reservations occupied a slot that day, reconstructed from
        slot_reservations' own created_at/resolved_at timestamps (its status
        column is current-only and cannot answer a question about a past
        date -- see the new PortfolioRepository.list_reservations_for_experiment,
        exp005/infrastructure/repository.py); subsequent 1/5/10/20-session
        returns/MFE/MAE from signal close; a read-only hypothetical ADR-007
        fill-rule replay. The hypothetical-fill check DUPLICATES (never
        imports) EntryService._evaluate_execution's rule, since importing a
        decision-time module from diagnostics would violate Section 26's
        import-isolation invariant (test_exp005_diagnostics_import_boundary.py).
        Never calls any repository write method -- strictly observational, no
        virtual_positions/slot_reservations/cash-ledger writes.
      tests: test_exp005_diagnostics_shared.py (7), test_exp005_sell_quality.py
      (5), test_exp005_hold_quality.py (5), test_exp005_entry_timing.py (7),
      test_exp005_opportunity_cost.py (6), plus one new
      test_exp005_repository.py case for list_reservations_for_experiment --
      31 new tests, every numeric scenario hand-computed.
- [x] Stage 14 -- report generation (exp005/diagnostics/report_generator.py).
      Two-tier design: compute_run_summary aggregates ONE completed replay
      database (one variant/seed) into BuyQualitySummary/HoldQualitySummary/
      SellQualitySummary/CapacityQualitySummary (Section 25's first four report
      sections), reducing the Stage 12-13 per-item diagnostic results (never
      re-deriving a decision) to means/rates/distributions/horizon-keyed dicts.
      compute_selection_quality (Section 25's fifth section) composes
      already-computed RunQualitySummary objects -- one for Variant B, one per
      Variant D seed, each necessarily its own isolated replay database since
      virtual_positions/friends have no replay_id column -- and reports Variant
      B's point value against percentile_rank(b_value, d_distribution) rather
      than a bare point comparison, mirroring Section 10's own
      control_percentile_threshold discipline. Compares 6 named metrics: entry
      gap, MFE captured at exit, realized return, exit efficiency, target-hit
      rate, and NO_CAPACITY hypothetical-fill rate.
      New read-only reporting queries added (all additive, no existing method
      changed): SandboxRepository.list_filled_orders/list_expired_orders/
      list_all_positions/list_hold_snapshots (infrastructure/
      sqlite_repository.py); PortfolioRepository.list_admissions_for_experiment
      (exp005/infrastructure/repository.py) -- mirroring the existing
      list_executions_for_experiment precedent.
      tests: test_exp005_report_generator.py (5) -- percentile_rank and
      compute_selection_quality get exact hand-computed unit tests (pure
      functions); compute_run_summary is exercised end-to-end against a real,
      FK-enforced SQLite fixture (a filled+closed position via an ambiguous
      intraday-touch SELL_TARGET exit, an expired order, a NO_CAPACITY
      admission, and equity snapshots) checking counts/rates/means -- the
      exhaustive per-horizon numeric coverage already lives in the Stage 12-13
      test files, so this fixture verifies wiring/joins/filters rather than
      re-deriving arithmetic. Plus 4 new repository-method tests (3 in
      test_sandbox_persistence.py, 1 in test_exp005_repository.py).
- [x] Stage 15 -- synthetic end-to-end fixture + completion report. See
      docs/09_experiments/EXP-005_Stage15_Completion_Report.md for full detail.
      tests/test_exp005_stage15_synthetic_end_to_end.py drives run_real_experiment
      for real (only the model adapter and feature-universe provider are faked,
      as in test_exp005_real_run.py's own integration test) against a small,
      fully synthetic two-symbol frozen price history: real CandidateService
      scoring/ranking/data-quality (the first test to exercise this with a
      non-empty universe -- every prior real-run test used an empty one), real
      capacity competition under max_slots=1 (AAA admitted, BBB NO_CAPACITY
      twice), real ADR-007 fill, real target-exit via a deliberately ambiguous
      intraday touch (exercising Section 20's exclusion rule through the whole
      pipeline), real accounting, then the real Stage 11 diagnostics loading
      boundary and Stage 12-14 diagnostics/report aggregation against the
      actual persisted database -- not a fixture built to match expected
      output. All predicted outcomes confirmed on the first corrected run (one
      hand-count fix, not a code defect: the fill day's own same-day HOLD
      snapshot was initially miscounted).

      **Stages 0-15 complete.**

      **Stage 11-15 independent review, first round (2026-07-20): 4 confirmed
      P1 findings, closed in one corrective cycle.**

      1. **The Section 10 financial-feasibility report was entirely missing.**
         `report_generator.py` only ever covered decision quality (Sections
         18-25) -- nothing computed net P&L, net return, max drawdown,
         quarterly returns, profit factor, trade-concentration diagnostics, or
         the Variant B vs. D percentile feasibility verdict, so the project's
         actual research question ("does Model 2's ranking make money?") had
         no computed answer. Fixed: new `exp005/diagnostics/
         financial_performance.py` (module docstring documents that Section
         10's exact Revision 2 prose is not independently recoverable from
         this repo's git history -- only one commit ever added the frozen
         doc, already at Revision 5 -- so the already-frozen, already-tested
         `exp005.config.FeasibilityCriteria` thresholds are the authoritative
         numeric source, not an invented one). `compute_financial_performance`
         (starting/ending equity, net P&L/return, drawdown with peak/trough
         dates, quarterly returns, closed-trade win/loss/profit-factor,
         largest-winner and largest-open-position concentration diagnostics,
         all from `portfolio_equity_snapshots`/paired BUY-SELL `executions` in
         exact integer units) and `compute_feasibility_verdict` (composes a
         Variant B report with Variant D seed reports into 5 explicit
         criteria plus one final verdict that is `None`, never a silent pass,
         whenever any criterion is undeterminable). 13 hand-computed tests
         (`tests/test_exp005_financial_performance.py`).
      2. **Censored (partial-window) observations were blended into headline
         horizon means/rates in `report_generator.py`.** Fixed:
         `horizon_mean_*`/`horizon_target_reached_rate` now compute from
         `is_censored=False` observations only, with `horizon_complete_count`
         plus separate `horizon_censored_end_of_experiment_count`/
         `horizon_censored_missing_market_data_count` reported alongside
         (Section 27's two reasons never merged). Target-hit rates use an
         asymmetric rule: a censored-but-already-reached-target observation
         counts as a resolved success; a censored-and-not-yet-reached one is
         excluded from both numerator and denominator entirely (`_target_
         reached_rate`). New fixture proves a single extreme censored
         observation cannot swing an otherwise-complete headline mean.
      3. **`opportunity_cost.py` reconstructed capacity occupancy from
         `slot_reservations.created_at`/`resolved_at` wall-clock timestamps
         compared against the historical `admission.as_of_date`** -- two
         unrelated clocks (a 2024 replay date has no relationship to a 2026
         diagnostics-run wall-clock write time), so the reconstruction never
         actually found anything, and open positions weren't returned at all.
         Fixed: occupancy is reconstructed purely from logical replay event
         dates -- a reservation occupies from its admission's `as_of_date`
         through its logical fill/expiry session (derived from
         `entry_order_attempts`, mirroring `entry_timing.py`); a position
         occupies for `entry_date <= as_of_date < exit_date`, honoring
         same-day rank ordering (Section 8.4) for tie-breaking. Returns both
         `occupying_reservations` and `occupying_open_positions`, reconciled
         against that day's own equity snapshot
         (`CapacityOccupancyReconciliationError` on mismatch, never silently
         accepted). Regression test uses 2024 replay dates with 2026
         wall-clock timestamps to prove the fix.
      4. **`mfe_mae.py` could report MFE below the position's own known
         realized return for an intraday-touch `SELL_TARGET` exit** (excluding
         the exit session's unknown High/Low also excluded the KNOWN exit
         price itself as a candidate), producing negative peak-to-exit
         giveback and exit efficiency over 1 -- both nonsensical. Fixed: the
         known, certain effective exit price is now always a boundary
         candidate for both MFE and MAE, alongside whatever the (possibly
         window-excluded) session data contributes; `mfe_pct >=
         realized_return_pct` now holds by construction. The prior empty-window
         `MfeMaeComputationError` is replaced by a degenerate 2-point
         (entry/exit) fallback instead of discarding an otherwise valid trade.
      5. **`load_diagnostics_context` never verified the replay it was
         analyzing actually existed, completed, or matched the manifest's own
         provenance** -- any connection could be passed off as "the" replay
         database. Fixed: now loads the manifest fresh from its persisted
         artifact file (never an in-memory object, mirroring `real_run.py`'s
         own boundary), and requires a `replay_metadata` row for `replay_id`
         that is `COMPLETED` and whose commit/model/feature-snapshot/
         market-data-snapshot/period AND `configuration_json`'s own recorded
         manifest-artifact-file hash all match.

      All five fixes are covered by dedicated regression tests reproducing
      the reviewer's exact failure scenarios, and by an extended
      `tests/test_exp005_stage15_synthetic_end_to_end.py` proving all five
      corrections hold together through the real pipeline: the corrected MFE
      boundary, historical (not wall-clock) capacity occupants, censored
      observations excluded from complete aggregates, the new financial
      performance report/verdict, and an explicit before/after row-level
      fingerprint proving no diagnostic call mutates any decision-time/
      accounting table. 600/600 (508 sandbox+exp005, 92 unrelated) tests
      pass; EXP-004's checksum is unchanged.

      **No real EXP-005 replay or P&L has been produced.** Per the standing
      authorization, this corrective cycle must pass ANOTHER independent
      review before Stages 11-15 can be closed; only after that passes may a
      final manifest be generated for the commit with which a real Variant B
      or Variant D run is actually executed.

      **Stage 11-15 independent review, second round (2026-07-22): the first
      round's five fixes were confirmed substantively correct; 6 further
      integrity findings, closed in a second corrective cycle.** All six were
      genuine implementation gaps, not test-only issues -- the reviewer noted
      59/59 targeted tests were passing beforehand precisely because none of
      them exercised these specific edge cases.

      1. **`compute_feasibility_verdict` accepted an arbitrary number of
         Variant D runs as "the" control group**, so a partial or even
         foreign/duplicated seed set could silently produce a determined
         percentile verdict. Fixed: `_validate_control_group` now requires
         `variant_b.variant_id == VARIANT_B` with no `control_seed`, and
         every D report to have `variant_id == VARIANT_D` with a
         `control_seed` drawn from `DEFAULT_CONTROL_SEEDS` with no
         duplicates or unknown seeds -- raising `ControlGroupValidationError`
         otherwise. A separate `_is_complete_control_group` check (exactly
         `len(DEFAULT_CONTROL_SEEDS)` valid reports) gates whether
         `beats_control_percentile` is computed at all; an incomplete group
         yields an undetermined (`None`) criterion, never a computed
         percentile from a partial sample. Regression tests cover exactly
         1/5/49/50/51 control reports, plus duplicated and unknown seeds.
      2. **The three-tier feasibility verdict could return `None` even when
         one criterion was a CONFIRMED failure**, because the old logic
         short-circuited on the first `None` it encountered regardless of
         whether a `False` also existed. Fixed: verdict is `False` if ANY
         criterion is `False` (checked first, unconditionally), else `None`
         if ANY criterion is `None`, else `True` only if ALL are `True` --
         a confirmed failure now always wins over an unrelated undetermined
         criterion. Regression test: net P&L negative (confirmed failure)
         with winner-concentration undeterminable (no closed trades) must
         yield `False`, not `None`.
      3. **Open-position cost basis used `gross_notional + commission`,
         omitting slippage** -- not the actual cash that left the portfolio.
         Fixed: `cost_basis_units = -buy_execution.net_cash_flow_units`, the
         ledger's own exact signed cash flow for the BUY (already negative,
         so negated for a cost basis), which folds in slippage automatically
         since it's part of `net_cash_flow_units` by construction. Also:
         "largest open unrealized gain" previously could select the
         *smallest loss* when every open position was underwater; now
         restricted to positions with `unrealized_gain_units > 0`, and is
         `None` (undetermined) when none qualify. Regression tests use
         non-zero commission and slippage and assert against a value derived
         from the real `compute_buy_accounting`/`compute_sell_accounting`
         functions, not hand-picked literals; a companion test proves an
         all-losses portfolio reports no largest-open-winner.
      4. **Profit factor summed pre-converted floats**, risking the same
         float-non-associativity class of bug documented in Stage 2-5's
         corrective cycle (`(0.1+0.2)/0.3 != 1.0` exactly). Fixed:
         `gross_wins_units`/`gross_losses_units` are summed in exact integer
         money units from each trade's own `net_pnl_units`; the division to
         a float `profit_factor` happens exactly once, at the end. Regression
         test uses sell prices producing exact +$0.10/+$0.20/-$0.30 trades
         and asserts `profit_factor == 1.0` with exact equality (not
         `pytest.approx`).
      5. **`compute_opportunity_cost` silently returned `None`-ish zero
         counts when a day's `portfolio_equity_snapshots` row was missing**,
         instead of treating a gap in a COMPLETED replay's supposedly
         complete daily snapshot record as the data-integrity violation it
         is. Fixed: raises `MissingEquitySnapshotError` immediately once
         `get_equity_snapshot` returns `None`, before any reconciliation or
         occupancy reconstruction runs. Regression test: a COMPLETED replay
         with a `NO_CAPACITY` admission whose day has no equity snapshot now
         fails closed with a clear error instead of silently continuing.
      6. **`load_diagnostics_context` verified the manifest-artifact hash
         embedded inside `configuration_json`, but never verified
         `configuration_json` and `configuration_hash` were still
         self-consistent with each other** -- either field could be edited
         independently after the replay was written (e.g. a stale hash left
         over from a since-edited json, or vice versa) and diagnostics would
         proceed regardless. Fixed: recomputes
         `hashlib.sha256(replay.configuration_json.encode("utf-8")).hexdigest()`
         -- the exact same relationship `real_run.py`'s own
         `_configuration_identity` establishes at write time, using the
         persisted json string verbatim, never re-serialized -- and requires
         equality with the persisted `configuration_hash` before the
         manifest-artifact-hash check (or anything else) runs. Regression
         tests cover configuration_json tampered independently (stale hash
         left behind) and configuration_hash tampered independently (json
         left correct); both fail closed with `DiagnosticsProvenanceError`
         before analysis.

      All six fixes are covered by dedicated regression tests reproducing
      the reviewer's exact scenarios (`tests/test_exp005_financial_
      performance.py`, `tests/test_exp005_opportunity_cost.py`,
      `tests/test_exp005_diagnostics_context.py`), plus the pre-existing
      `tests/test_exp005_stage15_synthetic_end_to_end.py` mutation-guard
      fixture re-verified against the corrected three-tier verdict logic
      (its single closed trade's 100% winner-concentration is now a
      CONFIRMED failure, so the overall verdict is `False`, not the old
      `None`) and confirmed to exercise finding 6's self-consistency check
      on its happy path (the real `run_real_experiment` pipeline already
      overwrites the placeholder `configuration_hash` with a genuinely
      matching one). 617/617 tests pass (525 sandbox+exp005, 92 unrelated);
      EXP-004's checksum is unchanged
      (`9f4d579df1c39f436ca28a35f768d201d89005fca36b43db3872fbf658c28882`).

      **No real EXP-005 replay or P&L has been produced.** Per the standing
      authorization, this second corrective cycle must also pass ANOTHER
      independent review before Stages 11-15 can be closed; only after that
      passes may a final manifest be generated for the commit with which a
      real Variant B or Variant D run is actually executed. The branch has
      not been pushed.

      **Stage 11-15 independent review, third round (2026-07-22): all six
      second-round findings confirmed correctly resolved (59/59 targeted
      tests passing); one further P1 provenance finding, with two related
      parts, closed in a third corrective cycle.** Both parts share one root
      cause: `compute_financial_performance`/`compute_feasibility_verdict`/
      `compute_run_summary` accepted `variant_id`/`control_seed`/
      `feasibility_criteria` as plain caller arguments, never verified
      against anything -- a Variant D run's report could be labeled Variant B
      (or assigned a different seed) by whoever called the function, and the
      thresholds a verdict was scored against could be silently swapped after
      results were already visible, entirely independent of what the replay's
      own frozen configuration actually recorded.

      1. **Report identity was caller-supplied, not derived from verified
         configuration.** Fixed: `load_diagnostics_context` now parses the
         SAME `exp005_config` sub-object it already hash-verifies as part of
         `configuration_json` (`real_run.py`'s own `_configuration_identity`
         payload) and derives `variant_id`/`control_seed`/
         `feasibility_criteria` from it -- never from a caller. These are new
         `DiagnosticsContext` fields. Every `executions` row for the replay is
         cross-checked against this same config-derived identity, failing
         closed on any disagreement; a replay with zero executions is not a
         special case -- its identity still comes entirely from the verified
         configuration, since there is simply nothing to cross-check in that
         case, never a fallback default. `compute_financial_performance` and
         `compute_run_summary` (`report_generator.py`, the same principle
         applied to Stage 14's report generator per the reviewer's explicit
         instruction) now take only a `context` (`compute_run_summary` also
         only a `calendar`) -- `replay_id`/`variant_id`/`control_seed` are no
         longer parameters at all, so there is no argument through which a
         Variant D report could be relabeled Variant B or reassigned to a
         different seed.
      2. **`FinancialPerformanceReport` carried no proof that a Variant B
         report and its Variant D control group came from the same frozen
         experiment.** Fixed: every report now carries
         `manifest_artifact_hash`/`configuration_hash`/`model_version`/
         `feature_snapshot_id`/`market_data_snapshot_id`/`signal_start_date`/
         `signal_end_date`/`outcome_data_end_date`/`feasibility_criteria`,
         all populated from the verified `DiagnosticsContext`, never a caller
         argument. `compute_feasibility_verdict` no longer accepts a
         `feasibility_criteria` dict at all -- it derives thresholds from
         `variant_b.feasibility_criteria` -- and a new
         `_validate_comparable_provenance` check requires every one of those
         fields to be IDENTICAL between Variant B and every Variant D report
         before any comparison runs (`variant_id`/`control_seed` are the only
         fields allowed to differ), raising the new
         `ExperimentIdentityMismatchError` on any mismatch, naming the
         specific field that disagreed.

      Regression tests (`tests/test_exp005_diagnostics_context.py`,
      `tests/test_exp005_financial_performance.py`) cover: a report's
      variant/seed cannot be supplied by a caller (no parameter exists to do
      so); a zero-transaction replay still gets its identity from
      configuration; an `executions` row whose variant or control_seed
      disagrees with the replay's own configuration fails closed; malformed
      `exp005_config` payloads (unsupported variant, B-with-seed,
      D-without-seed, empty feasibility_criteria) are rejected; a control
      report with a different `manifest_artifact_hash`, `model_version`,
      `feature_snapshot_id`, `market_data_snapshot_id`, or signal/outcome
      date is rejected; an altered `feasibility_criteria` threshold is
      rejected; and the full 50-seed control group with common provenance
      still produces a determined verdict exactly as before. The Stage 15
      end-to-end fixture (`tests/test_exp005_stage15_synthetic_end_to_end.py`)
      no longer passes variant/seed/criteria manually to any of the three
      functions, relying entirely on the real pipeline's own verified
      configuration. 637/637 tests pass (545 sandbox+exp005, 92 unrelated);
      EXP-004's checksum is unchanged
      (`9f4d579df1c39f436ca28a35f768d201d89005fca36b43db3872fbf658c28882`).

      **No real EXP-005 replay or P&L has been produced.** Per the standing
      authorization, this third corrective cycle must also pass ANOTHER
      independent review before Stages 11-15 can be closed; only after that
      passes may a final manifest be generated for the commit with which a
      real Variant B or Variant D run is actually executed. The branch has
      not been pushed.

      **Stage 11-15 independent review, fourth round (2026-07-22): all
      third-round fixes confirmed correct; one further P1 provenance finding
      closed in a fourth, narrow corrective cycle.** Root cause: the third
      closure's `configuration_hash` self-consistency check (second closure,
      finding 6) only ever proved `configuration_json`'s OWN text was not
      edited AFTER being persisted -- it says nothing about whether the
      `exp005_config`/`manifest` sub-objects embedded INSIDE that text were
      ever actually anchored to the real, on-disk manifest in the first
      place. A wholesale-regenerated `configuration_json`, hashed correctly
      from the start, could embed different feasibility thresholds (or a
      different manifest snapshot entirely) while still citing the correct
      `manifest_artifact_hash` -- `load_diagnostics_context` never actually
      compared the embedded objects' CONTENT against the manifest it had
      already loaded and verified.

      Fixed, all in `load_diagnostics_context`, immediately after the
      existing `configuration_hash`/`manifest_artifact_hash` checks:
      1. `configuration["manifest"]` must now equal `manifest.canonical_dict()`
         (the manifest freshly re-verified from its own persisted artifact
         file) byte-for-byte, as parsed dicts.
      2. `configuration["exp005_config"]["feasibility_criteria"]` must equal
         `manifest.feasibility_criteria` exactly.
      3. `DiagnosticsContext.feasibility_criteria` is now populated from a
         defensive copy of the MANIFEST's own `feasibility_criteria` (never
         the configuration dict, even though the two are now proven equal by
         (2) -- the manifest is the authoritative source, not a value that
         happens to currently agree with it).
      4. A Variant D `control_seed` must now be a genuine `int` (not a JSON
         boolean, which Python's `isinstance(x, int)` would otherwise accept
         since `bool` subclasses `int`) AND a member of
         `manifest.control_seed_list` -- not merely "not None."
      5. `exp005_config.experiment_id` must equal `manifest.experiment_id`,
         and `exp005_config`'s own `portfolio`/`admission_rules` sub-dicts
         must hash (via the same formula `Exp005Config.portfolio_
         configuration_hash()` uses) to `manifest.portfolio_configuration_hash`
         -- the same two checks `real_run.py`'s own `verify_real_run_
         preconditions` gate applies at run-start time, now re-verified when
         the persisted configuration is read back, since that gate only ever
         ran once, before this JSON was written.

      Five new regression tests (`tests/test_exp005_diagnostics_context.py`)
      reproduce the reviewer's exact scenarios: feasibility thresholds
      altered with a correctly recomputed `configuration_hash` still rejected
      (via the manifest-anchoring check, not the hash check); the embedded
      manifest object altered with a correctly recomputed hash still
      rejected; a Variant D replay with an unknown or non-integer seed AND
      zero executions rejected (proving this fails at load time, not merely
      via the execution cross-check); a genuinely correct, fully manifest-
      anchored configuration/manifest pair passes; and the Stage 15 real
      end-to-end pipeline explicitly asserts the final verdict's own
      thresholds equal `manifest.feasibility_criteria`. The four existing
      malformed-`exp005_config` tests were adjusted to build each bad payload
      from an otherwise-fully-valid base (rather than a minimal hand-built
      dict), since the new experiment_id/portfolio-hash checks would
      otherwise fire before the specific field each test targets. 642/642
      tests pass (550 sandbox+exp005, 92 unrelated); EXP-004's checksum is
      unchanged
      (`9f4d579df1c39f436ca28a35f768d201d89005fca36b43db3872fbf658c28882`).

      **No real EXP-005 replay or P&L has been produced.** Per the standing
      authorization, this fourth corrective cycle must also pass ANOTHER
      independent review before Stages 11-15 can be closed; only after that
      passes may a final manifest be generated for the commit with which a
      real Variant B or Variant D run is actually executed. The branch has
      not been pushed.

      **Stage 11-15 independent review, fifth round (2026-07-22): PASSED --
      Stages 11-15 are LOCKED.** The fifth review re-checked the actual diff
      against 72 targeted tests, found no further critical or P1 defects, and
      explicitly confirmed all of: the embedded configuration manifest
      matches the disk-verified manifest exactly; feasibility criteria are
      anchored to the manifest; the diagnostics context uses its own
      defensive copy of the manifest's criteria; a Variant D seed must be an
      approved integer; experiment ID and portfolio configuration hash are
      re-verified; the final verdict uses the manifest's frozen thresholds;
      and variant/seed identity plus cross-report provenance comparability
      are no longer caller-supplied.

      **Stages 0-15 of EXP-005's implementation are complete and closed.**
      Per the standing authorization, a real Variant B (and, contingent on
      Variant B's own result against the pre-registered absolute criteria,
      the 50 frozen Variant D control seeds) may now be executed against a
      freshly generated manifest tied to the exact commit at which Stages
      11-15 were locked -- see the real-run record appended to
      `docs/09_experiments/EXP-005_Stage15_Completion_Report.md` for that
      manifest's hash, the freeze-validation gate's result, and (once run)
      the actual replay outcome.
