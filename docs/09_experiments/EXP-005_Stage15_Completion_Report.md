# EXP-005 Stage 15 Completion Report

## Status

**Stage 15 (synthetic end-to-end fixture; completion report) is complete.**
Stages 0-15 of the implementation checklist are now done. **No real EXP-005
replay or P&L has been produced.** Per the standing authorization for this
implementation phase, an independent review of Stages 11-15 is required next.
Only after that review passes may a final manifest be generated specifically
for the commit with which a real Variant B or Variant D run is actually
executed -- not before.

## What Stage 15 tested

`tests/test_exp005_stage15_synthetic_end_to_end.py` drives the entire EXP-005
pipeline in one test, against a small, fully synthetic two-symbol frozen price
history (two symbols, ~40 synthetic trading sessions: 25 warm-up sessions so
real ATR14 is well-defined, plus 15 "active" sessions). Only two components are
faked: the model adapter and the feature-universe provider (`Model2PredictionAdapter`
/ `HistoricalFeatureUniverseProvider`), exactly as `test_exp005_real_run.py`'s
own existing integration test already does, since a real Model 2 fit needs real
SWING_20 training data this fixture has no reason to reproduce. Every other
component runs for real, unmodified, against the persisted database it actually
produces:

- `CandidateService`'s real scoring/ranking/data-quality logic (`_build_candidate_draft`,
  `_decide_selection`) -- this is the first test in the whole implementation to
  exercise this path with a non-empty universe; every prior real-run test used an
  empty universe provider and only tested the gate/provenance/schema boundary.
- The real `CapacityAdmissionOrchestrator` / `PortfolioLedger`, under a
  deliberately tight `max_slots=1` so capacity competition is forced: two
  symbols (AAA ranked above BBB, deterministically) compete for one slot on both
  of the fixture's two signal days.
- `EntryService`'s real ADR-007 fill rule.
- `MonitoringService`'s real target/time-exit logic, including a deliberately
  engineered **ambiguous intraday target touch** (the exit session's own open is
  below the +20% target but its high reaches it) -- exercising Section 20's
  entry/exit-session ambiguity exclusion rule through the full pipeline, not
  just a Stage 12 unit test.
- The real `Exp005AccountingSeam` / `PortfolioRepository` execution ledger
  (atomic fill/close writes, effective-price/slippage accounting).
- Stage 11's real, unpatched `load_diagnostics_context` loading boundary,
  called separately after the replay completes, against the actual database.
- Stage 12-13's real per-item diagnostics (`compute_mfe_mae`, `compute_sell_quality`,
  `compute_hold_quality`, `compute_opportunity_cost`) and Stage 14's real
  `compute_run_summary` aggregation, all invoked against genuine persisted
  facts -- not fixtures constructed to match expected diagnostic output.

## Predicted vs. actual outcome

The synthetic price series was engineered so the outcome is fully predictable,
not just "does it crash": AAA fills `FILLED_AT_OPEN`, holds for 5 monitoring
sessions (the fill day counts as holding day 1 and is itself monitored the same
day, alongside 4 more `HOLD` sessions), then exits `SELL_TARGET` via the
ambiguous intraday touch described above. BBB is rejected `NO_CAPACITY` on both
signal days it competes on. Every one of these predicted facts was confirmed
against the real database and the real diagnostics output on the first
corrected run (one test assertion needed a same-day fix: the entry day's own
`HOLD` snapshot was initially miscounted by hand as absent -- the code was
correct, the manual expectation was off by one). No other defect was found.

## Verification

- 1 new end-to-end test passes (`test_synthetic_end_to_end_pipeline`).
- 475/475 (was 474) sandbox+exp005 suite passes.
- 92/92 unrelated tests unaffected.
- Section 26's import-isolation boundary (`test_exp005_diagnostics_import_boundary.py`)
  still holds.
- EXP-004's locked replay database checksum
  (`9f4d579df1c39f436ca28a35f768d201d89005fca36b43db3872fbf658c28882`) confirmed
  byte-for-byte unchanged.
- Working tree is clean; the branch has not been pushed.
- No real EXP-005 replay or P&L has been produced -- every price, symbol, and
  outcome in this stage's fixture is synthetic and fabricated for testing only.

## What is explicitly NOT covered by this stage (by design)

- Variant D / `RankingControlAdapter` seed scoring -- covered separately and
  exhaustively by `test_exp005_variant_runner.py`; not re-exercised here to keep
  this fixture's capacity-competition outcome deterministic and simple to
  verify by hand.
- Expired (never-filled) orders -- covered exhaustively by
  `entry_timing.py`'s own unit tests (Stage 13); adding a third competing
  symbol to also produce an `EXPIRED` order in this fixture would have required
  a second capacity slot or a second signal window, adding complexity without
  adding coverage this stage doesn't already get from the dedicated unit tests.
- `report_generator.compute_selection_quality` (Variant B vs. D seed
  comparison) -- this requires multiple isolated replay databases (one per
  seed); it is unit-tested directly against constructed `RunQualitySummary`
  objects in Stage 14's own tests, and will be exercised for real only once an
  actual multi-seed run is authorized.

## Next step

Per the standing authorization: **an independent review of Stages 11-15 must
happen next.** Only once that review passes may a final Experiment Manifest be
generated specifically for the commit with which a real Variant B or Variant D
replay is actually run -- no real run before that.
