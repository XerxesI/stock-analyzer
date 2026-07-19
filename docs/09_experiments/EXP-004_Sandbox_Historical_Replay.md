# Experiment Record

## Experiment ID

```text
EXP-004
```

## Title

SWING_20 Recommendation Sandbox -- Development Historical Replay (sequential,
day-by-day, full sandbox lifecycle).

**This document has two parts, committed separately** (same convention as EXP-003):

- **Part 1 -- Pre-Registration** (this commit): replay classification, period,
  isolation design, and the metrics to be reported, written before the replay runs.
- **Part 2 -- Result** (a later commit): the actual funnel/attribution numbers,
  appended once the replay has completed.

## Date

```text
2026-07-19
```

## Owner

Claude (agent), instructions relayed from ChatGPT (research lead) via Meelis Kivimäe.

## Related Experiments / Documents

- `docs/02_mvp/MVP_2_Recommendation_Sandbox_Specification.md`
- `docs/04_decisions/ADR-006-Sandbox-Persistence-and-Audit-Trail.md`
- `docs/04_decisions/ADR-007-Next-Day-Entry-Simulation.md`
- `docs/09_experiments/EXP-003_SWING20_Locked_Test.md` (Model 2's frozen identity and
  Locked Test PASS result -- unchanged, not reopened, by this replay)

---

# Part 1 -- Pre-Registration

## 1. Classification (mandatory framing)

```text
DEVELOPMENT_HISTORICAL_REPLAY
NOT INDEPENDENT MODEL VALIDATION
NOT FOR POLICY OPTIMIZATION
```

This is explicitly **not** a new Locked Test and **not** an independent validation of
Model 2. Reasons, stated plainly:

- Model 2 was developed and validated using historical periods (train/validation)
  already known to the project, and this replay's signal period overlaps the
  validation split.
- The sandbox's entry-price ceiling, candidate-count, target, and holding-horizon
  policies (ADR-007, spec sections 8-11) were all designed **after** Model 2's Locked
  Test (EXP-003) -- they were never subjected to their own out-of-sample test.
- This replay evaluates the sandbox's **operational behavior** -- does the pipeline
  run correctly end-to-end over hundreds of days, what does its own decision funnel
  look like -- not a new alpha hypothesis.

**No rule is tuned based on this replay's outcome.** The entry-price constants (2%
close cap, 0.25x ATR cap), the 3-candidate cap, the +20% target, the 20-session
holding horizon, and the 2-session entry validity window are frozen exactly as
specified in the MVP 2 spec and ADR-007, before and after this replay runs.

## 2. Replay period

```text
signal_start_date:      2024-11-18
signal_end_date:        2025-09-03
outcome_data_end_date:  2025-10-20
```

- `signal_start_date` / `signal_end_date` span the SWING_20 validation split exactly
  (the same split used in EXP-002/EXP-003) -- roughly 9.5 calendar months, well past
  the "at least 6 calendar months" target.
- `outcome_data_end_date` extends 33 additional real trading sessions past
  `signal_end_date` (confirmed against SPY's actual trading calendar, 2025-09-04
  through 2025-10-20) -- more than the 20 holding sessions + 2 entry-validity sessions
  a position opened on the very last signal date could need to fully resolve.
- No candidate is generated for any date after `signal_end_date`. Entry processing and
  position monitoring continue through `outcome_data_end_date` so already-created
  pending orders and open positions can reach a natural resolution (fill/expire,
  target/time exit) wherever possible.
- Any position still `OPEN` at `outcome_data_end_date` is reported explicitly as
  **unresolved** in Part 2 -- not scored as a win, loss, or dropped silently.

## 3. Isolation

This replay uses its own SQLite database file
(`artifacts/sandbox/replays/development_replay_2024_11_2025_10/replay.db`),
**never** the smoke-test database (`artifacts/sandbox/smoke_test.db`) or any future
forward/live sandbox database. A single `replay_metadata` row inside that database
records: `replay_id`, classification, code commit SHA, Model 2 version identifier,
feature snapshot id, signal/outcome date boundaries, full configuration + hash, and
run status (`ReplayService`, `docs/04_decisions/ADR-006...`). Re-running the same
`replay_id` after it has completed raises `ReplayAlreadyCompletedError` rather than
silently re-running or silently skipping (`stock_analyzer/sandbox/application/replay_service.py`).

## 4. Orchestration (unchanged from daily production path)

Per date T, in strict chronological order, using the *same* `CandidateService` /
`EntryService` / `MonitoringService` a normal `daily-run` uses (no vectorized
shortcut):

1. Process pending orders using date T's own OHLC.
2. Monitor positions already open before date T (target/time exits execute as part of
   monitoring itself).
3. If T `<=` signal_end_date: run frozen Model 2 inference, persist the date T shadow
   top-10, select at most 3 actionable candidates, create entry orders that cannot
   execute before the next observed trading session.
4. If T `>` signal_end_date: no new candidates -- outcome-only day.

No future date may influence a decision made on date T: `fetch_as_of` always truncates
to `<= T`, and every day only reads repository state already committed from prior
days.

## 5. Required metrics (Part 2 will report all of these)

**Funnel** (counts + conversion rate at each stage): eligible universe -> shadow
top-10 -> actionable top-3 -> pending entry orders -> filled positions -> closed
positions.

**Shadow ranking** (uses the frozen SWING_20 label, `target_20pct_20d`, joined back
onto persisted `ranked_candidates` rows -- not a redefinition): number of signal
dates, top-10 and top-3 target-hit-rate, hit-rate by rank, by ADV quintile, by market
regime.

**Candidate selection**: actionable candidates created, exclusions by reason
(`ALREADY_OPEN_POSITION`, `ALREADY_PENDING_CANDIDATE`, `MISSING_MARKET_DATA`,
`INVALID_PRICE`, `MISSING_ATR`).

**Entry policy**: orders created, fill rate, fills at open vs. at ceiling, no-fill
attempts, expired orders.

**Position lifecycle**: positions opened/closed/still-open, `SELL_TARGET` vs.
`SELL_TIME` count and %, holding-period mean/median, realized-return mean/median, win
rate, MFE/MAE means, best/worst realized return, total equal-notional virtual P&L.

**Operational**: maximum simultaneous open positions, missing-data event count.

**Unresolved positions**: full list of positions still `OPEN` at
`outcome_data_end_date`.

These are **observational metrics only**. An unfilled candidate is not treated as a
model failure; a successful shadow candidate that was never filled is not treated as a
realized sandbox trade -- the funnel above exists specifically so a reader can tell
these apart (per review: "Ilma nende kihtide eristamiseta ei tea me, milline
komponent aitas või kahjustas").

## 6. Known limitations disclosed in advance

- **`HistoricalFeatureUniverseProvider` forward-use blocker**: this replay (like the
  smoke test) reads the symbol universe and Model 2 stock/context features from an
  existing frozen feature dataset. It cannot discover a live "today" universe or build
  fresh point-in-time features for a date outside that frozen dataset. A
  `DailyPointInTimeUniverseProvider` (or equivalent) capable of that, with a parity
  test against a frozen historical snapshot, is **not implemented in this cycle** and
  remains an explicit blocker before any `FORWARD_PAPER_EVALUATION` phase can begin.
  This replay does not attempt to work around that limitation -- it operates entirely
  within the frozen dataset's known date range.
- Shadow top-10 rows for `adv_quintile` are computed once at candidate-generation time
  using Model 2's train-fit ADV edges; they are not re-bucketed later, so
  `distribution_by_adv_quintile` reflects each candidate's ADV standing on its own
  signal date, not a replay-period-wide re-ranking.
- MFE/MAE are tracked from each position's own entry date forward (matching the
  frozen SWING_20 label's own definition), not from the earlier signal date.

## 7. Reproduction

```bash
python scripts/run_sandbox_historical_replay.py
```

Uses the frozen train+validation feature dataset already recorded in
`docs/09_experiments/SWING20_Phase1_Artifact_Manifest.md`
(`swing20_features_20260718T165654Z/features.parquet`) for both Model 2's train-only
fit and the historical universe/feature source. Writes its isolated database and a
JSON metrics report under `artifacts/sandbox/replays/` (generated output, excluded
from git).

---

# Part 2 -- Result

The replay ran twice, and its metrics were regenerated a third time from the same
database after a second bug was found by an independent Codex audit. Neither
correction changed Model 2, the entry-price policy, the candidate count, the target,
or the holding horizon.

**Bug 1 (found before this Part 2 was first written): `actionable` flag persisted
before selection.** `ranked_candidates.actionable` was set from per-symbol
data-quality checks only, before the top-3 rank cap and already-open/already-pending
exclusions were applied -- so every data-quality-clean shadow candidate (typically all
10/day) was stored as `actionable=True`, corrupting `actionable_candidates_total` and
any consumer reading that column directly. Fixed by restructuring
`CandidateService.generate_candidates` into build-drafts -> decide-selection ->
persist -> create-orders; the replay was re-run from a clean isolated database before
any result was used.

**Bug 2 (found by an independent Codex audit after Part 2 was first published,
confirmed by directly querying the replay database before any code changed):
holding-day-count/MFE/MAE were stale by one session on every closed position.**
`MonitoringService._handle_session` correctly computes and writes the closing
session's `holding_day_count`/`mfe`/`mae` to that position's final
`position_snapshots` row, but on `SELL_TARGET`/`SELL_TIME` it only called
`close_position()`, which updated `status`/`exit_date`/`exit_price`/`exit_reason`/
`realized_return` on `virtual_positions` -- never the current-state
`current_holding_day_count`/`mfe`/`mae` columns, which stayed at whatever the
*previous* day's `HOLD` update had left them. `build_replay_metrics` read those stale
`virtual_positions` columns instead of each position's own final `position_snapshots`
row. Verified directly against the (unmodified) EXP-004 database before fixing
anything: all 521 closed positions had a stale holding-day count (mean 15.9693
instead of the correct 16.9693 -- exactly one session low, as expected since the
bug always misses the closing session), 155 had a stale MFE (mean 8.5448% instead of
15.5179%), and 73 had a stale MAE (mean -16.0075% instead of -16.5151%). Realized
return and total P&L were unaffected -- those are computed from stored entry/exit
prices, a different code path that had no bug. Fixed in two places: `close_position()`
(repository and `MonitoringService`) now requires and persists the closing session's
final holding/MFE/MAE onto `virtual_positions` too (defense in depth), and
`build_replay_metrics` now reads each position's own final `position_snapshots` row
rather than `virtual_positions`' current-state columns (the architecturally correct
source per ADR-006's append-only design). Metrics below were regenerated from the
**existing, already-completed** replay database -- a full 230-day rerun was not
necessary, since `position_snapshots` had held the correct values all along; only the
current-state mirror and the metrics-reading code were wrong.

## Replay identity

```text
replay_id: development_replay_2024_11_2025_10
classification: DEVELOPMENT_HISTORICAL_REPLAY -- NOT INDEPENDENT MODEL VALIDATION -- NOT FOR POLICY OPTIMIZATION
signal_start_date: 2024-11-18
signal_end_date: 2025-09-03
outcome_data_end_date: 2025-10-20
signal dates processed: 197
outcome-only dates processed: 33
total dates processed: 230
unresolved positions at outcome end: 0
```

No rule was changed based on these results. Entry-price constants, candidate count,
target, and holding horizon are exactly as specified in the MVP 2 spec and ADR-007,
unchanged before and after this replay.

## Funnel

| Stage | Count | Conversion from prior stage |
|---|---|---|
| Shadow top-10 rows | 1,970 (197 dates x 10) | -- |
| Actionable (selected, <=3/day) | 525 | 26.6% |
| Entry orders created | 525 | 100% (1:1 with actionable) |
| Entry orders filled | 521 | 99.2% |
| Positions opened | 521 | 99.2% of orders |
| Positions closed | 521 | 100% (0 unresolved) |

Exclusions from the 1,445 shadow rows that did **not** become actionable:

| Reason | Count |
|---|---|
| `ALREADY_OPEN_POSITION` | 1,035 |
| `RANK_LIMIT_EXCEEDED` (data-quality clean, but beyond the top-3 cap) | 409 |
| `ALREADY_PENDING_CANDIDATE` | 1 |
| Data-quality (`MISSING_MARKET_DATA` / `INVALID_PRICE` / `MISSING_ATR`) | 0 |

## Shadow ranking (uses the frozen SWING_20 label, `target_20pct_20d`)

- **Top-10 (all shadow candidates) target-hit-rate: 33.5%** (n=1,970 labeled rows).
- **Top-3 actionable target-hit-rate: 23.4%** (n=525) -- **lower** than the full
  shadow set, not higher.
- By rank: hit-rate declines monotonically and cleanly with rank as expected from a
  working ranking signal -- rank 1: 49.7%, rank 2: 42.6%, rank 3: 34.5%, ..., rank 10:
  28.9% (n=197 per rank).
- By ADV quintile (train-fit edges): 1,786 of 1,970 shadow rows (90.7%) fall in
  `adv_q1` (smallest), consistent with EXP-001/002/003's finding that Model 2's edge
  concentrates in smaller/less-liquid names.
- By market regime: `Bull_Normal` dominates (1,250 rows, 34.2% hit-rate); `Bear_High`
  shows the highest hit-rate among regimes with meaningful sample size (180 rows,
  42.8%), consistent with EXP-003's Locked Test finding that the ranking's edge is not
  uniform across regimes.

**Policy-attribution finding (observational, not a basis for any rule change): the
by-rank breakdown shows the *ranking itself* is well-behaved (hit-rate falls
monotonically from rank 1's 49.7% down through rank 10), but the *realized actionable
set* (23.4%) is lower than even rank 10 alone.** The exclusion breakdown explains why:
1,035 of 1,445 exclusions (71.6%) are `ALREADY_OPEN_POSITION` -- the model's top
picks are "sticky" (the same names rank highly across many consecutive sessions), so
whenever a top-ranked name is still within an open position's holding period, the
already-open suppression policy correctly refuses to pyramid into it, but as a side
effect this pushes the *actual* selected candidate further down the day's ranking far
more often than the raw rank-1/2/3 hit-rates alone would suggest. This is exactly the
kind of effect the funnel/attribution layers were built to surface -- the ranking
engine and the selection policy must be evaluated separately, and this replay shows
the already-open suppression has a real, non-trivial cost in realized hit-rate. No
change is made to that policy based on this observation.

## Entry policy

| Metric | Value |
|---|---|
| Orders created | 525 |
| Orders filled | 521 (99.2%) |
| Filled at open | 453 (86.9% of fills) |
| Filled at ceiling (gap-then-pullback) | 68 (13.1% of fills) |
| No-fill attempts (session entirely above ceiling) | 15 |
| Orders expired (2 sessions, never filled) | 4 (0.8%) |

The entry-price ceiling policy did not meaningfully suppress opportunities in this
replay -- a 99.2% fill rate means the 2%/0.25x-ATR ceiling rarely rejected a signal
outright; most unfilled attempts still resolved on the second session.

## Position lifecycle

| Metric | Value |
|---|---|
| Positions opened / closed | 521 / 521 |
| Positions still open (unresolved) | 0 |
| `SELL_TARGET` | 124 (23.8%) |
| `SELL_TIME` | 397 (76.2%) |
| Holding days, mean / median | **16.97 / 20** (corrected; see Bug 2 above) |
| Realized return, mean / median | -1.76% / -0.32% |
| Win rate (realized return > 0) | 49.7% |
| MFE mean | **+15.52%** (corrected; see Bug 2 above) |
| MAE mean | **-16.52%** (corrected; see Bug 2 above) |
| Best realized return | +99.2% |
| Worst realized return | -87.2% |
| Total virtual P&L ($1,000 notional/position) | -$9,195.29 |

**This is an observational result, not a profitability claim, and MVP 2's hypothesis
(Section 2 of the MVP 2 spec) does not require a profitable outcome to be considered
validated -- it asks whether the process is reproducible, realistic, internally
consistent, executable, auditable, and stable.** On that narrower question, the
process ran cleanly across 230 real trading days with zero missing-data events, zero
unresolved positions, and fully reconstructable append-only history for all 521
positions. Whether the realized -1.76% mean / -$9,195 total virtual P&L reflects a
genuine absence of edge once the sandbox's realistic entry/exit frictions are applied,
versus this specific 9.5-month window, versus the already-open-suppression effect
noted above, is exactly the kind of question later, separately pre-registered research
should address -- not something to resolve by adjusting a policy constant after seeing
this number.

**Additional observation from the corrected MFE (+15.52%, not the originally-reported
+8.5%): positions moved substantially into profit quite often (mean favorable
excursion well above the eventual mean realized return of -1.76%), but that favorable
excursion was frequently not captured by exit.** With no stop-loss and no partial
take-profit in MVP 2 (spec section 11, intentional), a position that moves to +15%
unrealized and then drifts back down still exits via `SELL_TIME` at whatever the
20th-session close happens to be, or via `SELL_TARGET` only if the full +20% is
reached. This gap between MFE and realized return is a specific, falsifiable
observation about the **exit policy**, separable from the ranking and portfolio
questions above -- worth investigating before any new modeling work, not resolved
here.

## Operational

| Metric | Value |
|---|---|
| Maximum simultaneous open positions | 55 |
| Missing-data events | 0 |

## Test results

51/51 sandbox tests pass (`tests/test_sandbox_*.py`), including the actionable-flag
regression test (Bug 1) and two new tests for the holding/MFE/MAE staleness
regression (Bug 2): `test_closed_position_holding_mfe_mae_match_final_snapshot_not_stale`
(`tests/test_sandbox_monitoring_service.py`) and
`test_replay_metrics_holding_mfe_mae_use_final_snapshot`
(`tests/test_sandbox_replay_metrics.py`, new file).

## Commit SHAs

See git history: the actionable-flag fix, the replay engine/metrics (Part 1
pre-registration), this Part 2 result, and the holding/MFE/MAE staleness fix are each
separate commits.

## Post-hoc infrastructure fixes: replay resume + schema migration (2026-07-19)

A second independent audit round, following the Bug 1/Bug 2 fixes above, found that
the P0 fix's own defensive code (`candidate_service.py` Phase 3: raise on any `False`
return from `insert_ranked_candidate`) broke replay **resume** -- a replay with one
already-persisted signal date failed when resumed with the same configuration,
because `False` can legitimately mean "this exact candidate was already persisted in
a prior, interrupted attempt," not just "silently rejected." The audit separately
found that `SCHEMA_VERSION` had been bumped to `2` in code (for the Bug 1 nullable
`signal_close` fix) without any actual migration: `init_db()`'s
`CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so a pre-existing v1
database file would be relabelled `schema_version=2` in `schema_meta` while its
physical `ranked_candidates.signal_close` column stayed `NOT NULL`.

**Neither defect affects this experiment's results.** EXP-004's own replay ran to
completion in a single, uninterrupted pass (resume was never exercised), and its
database file was never re-opened by the fixed code (`schema_meta.schema_version`
there still correctly reads `'1'`, matching its actual, unmigrated physical schema --
verified by SHA-256 checksum before and after this work, unchanged:
`9f4d579df1c39f436ca28a35f768d201d89005fca36b43db3872fbf658c28882`).

Fixes, both scoped strictly to sandbox infrastructure (no ranking/entry/exit/
portfolio policy changed):

1. **Resume conflict handling** -- `SandboxRepository.insert_ranked_candidate` now
   explicitly checks any existing row's content before writing: an identical
   pre-existing row (the expected outcome of resuming an already-persisted signal
   date) is a safe no-op; a row with the same `candidate_id` but different content
   raises `RankedCandidateConflictError` rather than being silently accepted or
   mistaken for corruption.
2. **Resume watermark** -- `replay_metadata` gained `last_completed_date`, advanced
   only after a date's FULL processing (entries + monitoring + candidate generation)
   commits. `ReplayService.run()` uses it to skip every already-completed date on
   resume and reprocess only the one date that may have been left partially done.
   This was necessary, not optional: a full-history reprocess on resume let an
   *earlier* date's candidate selection see positions/orders opened on *later* dates
   from the pre-crash attempt (a genuine point-in-time leak), which a comparison test
   (below) caught directly. Full resume semantics are documented in
   `application/replay_service.py`'s module docstring.
3. **Real v1 -> v2 schema migration** -- `init_db()` now reads the database's actual
   recorded `schema_version` and, if it is `1`, runs an explicit, transactional
   migration (`_migrate_v1_to_v2` in `infrastructure/schema.py`): rebuilds
   `ranked_candidates` with `signal_close` nullable (SQLite cannot `ALTER` a `NOT
   NULL` constraint in place), and verifies `PRAGMA foreign_key_check` before
   committing. Any failure rolls back completely and leaves the database labelled
   `v1`, matching its true physical schema -- never silently relabelled. **(This
   step's original description here also said it added
   `replay_metadata.last_completed_date` -- that turned out to itself be a version-
   reuse bug, corrected below on 2026-07-19.)**

New tests (`tests/test_sandbox_replay_service.py`,
`tests/test_sandbox_schema_migration.py`):
`test_resume_after_genuine_partial_signal_day_succeeds`,
`test_interrupted_and_resumed_replay_matches_uninterrupted_replay` (byte-for-byte
comparison, excluding only timestamp columns, of every persisted table between an
uninterrupted replay and a realistically interrupted-then-resumed one),
`test_conflicting_ranked_candidate_content_is_rejected`,
`test_migration_produces_v2_schema_and_preserves_data`,
`test_null_signal_close_can_be_persisted_after_migration`,
`test_repeated_init_after_migration_is_idempotent`,
`test_migration_rolls_back_and_does_not_relabel_on_failure`. All 66 sandbox tests
pass (`tests/test_sandbox_*.py`).

## Post-hoc fix: honest v1/v2/v3 schema versioning + fail-closed legacy resume (2026-07-19)

A third independent audit round found the above fix had itself introduced two
further P1 compatibility defects, both stemming from the same root cause: `SCHEMA_VERSION`
was reused. Commit db067d4/68b0c6f had already published `SCHEMA_VERSION = 2` meaning
"`ranked_candidates.signal_close` nullable, no watermark." The round-2 fix above bumped
`SCHEMA_VERSION` to `2` *again* to also mean "plus `replay_metadata.last_completed_date`"
-- reusing a version number that had already been shipped for a physically different
schema. Any real database created by the originally-published v2 code (nullable
`signal_close`, no watermark) would be seen by the round-2 code as `schema_version=2`,
already at the target version, and never migrated -- leaving `last_completed_date`
referenced by `ReplayService`/`SandboxRepository` but physically absent from that
database.

**Fix 1 -- honest, non-reused versioning.** `infrastructure/schema.py` now defines
three permanent, physically distinct versions and never renumbers one after the fact:
  - **v1** (original): `ranked_candidates.signal_close NOT NULL`, no watermark.
  - **v2** (published in db067d4/68b0c6f, permanently frozen at this meaning):
    `signal_close` nullable, still no watermark.
  - **v3** (current, `SCHEMA_VERSION = 3`): adds `replay_metadata.last_completed_date`.

`_migrate_v1_to_v2` now touches only `ranked_candidates` (matching exactly what v2
originally shipped); a new `_migrate_v2_to_v3` adds only the watermark column via
`ALTER TABLE ... ADD COLUMN`. `init_db()` chains whichever steps are needed (v1 runs
both; a real, published v2 database runs only the second) and refuses outright
(`UnsupportedSchemaVersionError`) to open a database recorded at a version newer than
the running code understands, rather than silently operating on an unknown physical
schema.

**Fix 2 -- fail-closed resume for a legacy watermark.** Migrating a v1 or v2 database
to v3 adds `last_completed_date` as `NULL` for any pre-existing `replay_metadata` row
(`ALTER TABLE ADD COLUMN`'s default for existing rows). `ReplayService.run()`
previously (round 2) treated a `NULL` watermark as simply "nothing completed yet" and
reprocessed the full date list -- safe for a genuinely fresh `RUNNING` replay, but
NOT safe for a migrated `RUNNING`/`FAILED` replay that already has real, partially
persisted domain state: reprocessing history reopens exactly the point-in-time
contamination risk the watermark exists to prevent (see the round-2 comparison test
above), and guessing a watermark from e.g. `MAX(as_of_date)` cannot be proven safe --
that date might itself be a partially-processed boundary day, not a fully committed
one. The policy adopted (documented in full in `replay_service.py`'s module
docstring):
  - `COMPLETED` replay: rejected outright regardless of watermark (`ReplayAlreadyCompletedError`)
    -- a completed replay is never resumed, so the watermark is irrelevant.
  - `RUNNING`/`FAILED` replay, `NULL` watermark, **no** persisted domain state
    (`SandboxRepository.has_any_domain_state()` is `False`): safe to resume from the
    beginning -- this is the ordinary "died before finishing its first date" case.
  - `RUNNING`/`FAILED` replay, `NULL` watermark, **existing** persisted domain state:
    rejected outright with a new, specific `UntrustworthyResumeWatermarkError` --
    raised before the `try`/`except` around `_process_dates`, so the rejection itself
    never calls `fail_replay()` or otherwise mutates the replay's stored status. The
    only correct recovery is a new `replay_id` under a fresh isolated database.

New tests (`tests/test_sandbox_schema_migration.py`,
`tests/test_sandbox_replay_service.py`):
`test_v1_migrates_through_v2_to_v3_and_preserves_data`,
`test_v2_migrates_to_v3_and_preserves_data` (built from a LITERAL fixture matching
the actually-published v2 schema, not just a claim of being v2),
`test_v2_migration_rolls_back_and_does_not_relabel_on_failure`,
`test_unsupported_future_schema_version_is_refused`,
`test_fresh_database_is_created_directly_at_v3`,
`test_repeated_init_from_published_v2_is_idempotent`,
`test_resume_is_rejected_when_watermark_is_null_but_domain_state_exists`,
`test_migrated_completed_legacy_replay_rerun_still_rejected`,
`test_migrated_running_legacy_replay_with_no_domain_state_resumes_from_start`,
`test_migrated_failed_legacy_replay_with_domain_state_rejects_resume` (the last three
built from a real migrated v1-database file, not a fresh v3 database with the
watermark manually nulled). All 75 sandbox tests pass; EXP-004's own database
(untouched by any of this work) still reads `schema_meta.schema_version = '1'` and
its SHA-256 checksum is unchanged:
`9f4d579df1c39f436ca28a35f768d201d89005fca36b43db3872fbf658c28882`.

## Post-hoc fix: detect physically-mislabeled-v2 databases before migrating (2026-07-19)

A fourth independent audit round found one more P1 gap in the round-3 fix: `init_db()`
trusted a recorded `schema_meta.schema_version = 2` at face value to mean "correct
physical v2" (nullable `signal_close`, no watermark) and ran only `_migrate_v2_to_v3`
for it. But the version-reuse bug fixed in round 3 was itself a *live* bug for a
window of published commits (between 68b0c6f and the round-3 fix): during that
window, `init_db()` would blindly overwrite `schema_meta.schema_version` to `2` on
**any** existing database it opened -- including a genuinely physical v1 database it
never actually migrated. So a database recorded as `schema_version=2` could
legitimately be either state:
  1. correct physical v2 (`signal_close` nullable, no watermark), or
  2. a physically-v1 database that was only ever relabeled `2`, never migrated
     (`signal_close` still `NOT NULL`, no watermark).

Running only `_migrate_v2_to_v3` against state 2 would add the watermark column and
label the database `v3` while leaving `signal_close NOT NULL` -- a database that
*claims* to be fully migrated but is not. Reproduced independently with a fixture
matching exactly `schema_meta=2` + `signal_close NOT NULL` + no watermark.

**Fix.** `init_db()` no longer trusts `schema_meta=2` alone: for `current_version ==
2`, it inspects the ACTUAL physical schema before choosing a path.
`ranked_candidates.signal_close`'s real nullability (via `PRAGMA table_info`)
distinguishes state 1 from state 2 -- state 2 runs the same `_migrate_v1_to_v2`
physical repair (safe to call regardless of the stored label, since that function
operates purely on physical structure) before proceeding to `_migrate_v2_to_v3`. A
database that already has the watermark column while still labelled `2` (consistent
with neither known state) fails closed with a new `SchemaIntegrityError` rather than
guessing. Both migration functions also now explicitly verify their target version's
physical invariants (`_verify_v2_invariants` / `_verify_v3_invariants` --
`signal_close` nullability, required indexes/uniqueness, and for v3 the watermark
column) immediately before writing the new version label, so a migration that ran
without raising but somehow didn't achieve its physical goal is still caught before
the database is marked as reaching that version.

New tests (`tests/test_sandbox_schema_migration.py`), built from the exact reported
reproduction (`schema_meta=2`, `signal_close NOT NULL`, no watermark):
`test_mislabeled_v2_database_is_physically_repaired_and_reaches_v3` (all 8 required
checks: reaches a physically valid v3, `signal_close` nullable, watermark present,
existing rows/counts unchanged, indexes/uniqueness survive, FK integrity passes, a
NULL `signal_close` can subsequently be inserted, repeated init is idempotent),
`test_mislabeled_v2_repair_rolls_back_without_false_v3_label_on_failure` (a forced FK
failure during the repair rolls back completely, staying labelled `2` -- not falsely
`v3`, not left mid-repair), and
`test_v2_labeled_database_with_watermark_column_is_refused` (the "matches neither
known state" case fails closed with `SchemaIntegrityError`). All 78 sandbox tests
pass; EXP-004's own database remains untouched (`schema_meta.schema_version` still
`'1'`, SHA-256 unchanged:
`9f4d579df1c39f436ca28a35f768d201d89005fca36b43db3872fbf658c28882`).

## Remaining blockers before `FORWARD_PAPER_EVALUATION`

1. **`DailyPointInTimeUniverseProvider`** (MVP 2 spec Section 19) -- not implemented.
   This replay, like the smoke test, only replays dates already present in a frozen
   feature dataset; it cannot generate real recommendations for a new trading day.
2. A parity test for that provider against a frozen historical snapshot (exact/
   tolerance match on eligible symbols, feature values, ranking scores, rank order)
   -- depends on (1).
3. An `EXP-005_Sandbox_Forward_Paper_Evaluation.md` pre-registration, committed
   before the first forward-evaluation signal is generated -- not started.
