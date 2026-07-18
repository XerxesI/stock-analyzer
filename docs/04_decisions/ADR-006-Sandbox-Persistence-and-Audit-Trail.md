# ADR-006: SQLite Repository with Append-Only Audit Tables for the Recommendation Sandbox

**Status:** Accepted for MVP 2
**Date:** 2026-07-18
**Related documents:**

- `docs/02_mvp/MVP_2_Recommendation_Sandbox_Specification.md`

---

## Context

MVP 2 (Recommendation Sandbox) needs to persist daily rankings, candidate decisions,
simulated order fills, virtual positions, and daily monitoring snapshots, and must be
able to prove -- after the fact -- exactly what was recommended on each day and why,
without any possibility of silent retroactive edits.

No persistence or repository abstraction exists anywhere in this codebase today
(confirmed by repo survey before this ADR): no `sqlite3` usage, no ORM, no
repository-pattern module. This is a genuinely new concern for the project, not a
reuse decision.

Two requirements drive the design:

1. **Idempotency**: running the same CLI command twice for the same `--as-of` date
   must not duplicate or mutate anything.
2. **Auditability**: a position's full day-by-day recommendation history must be
   reconstructable purely from persisted rows, and historical rows must never be
   overwritten once written.

---

## Decision

Use **SQLite** via Python's standard-library `sqlite3` module for MVP 2's persistence,
behind a repository interface (`stock_analyzer/sandbox/infrastructure/sqlite_repository.py`)
that the `application/` layer depends on only through that interface -- so the storage
engine could later be swapped (e.g. for Postgres) without touching domain or
application code. `stock_analyzer/sandbox/infrastructure/schema.py` owns the DDL and a
`init_db()` / migration entry point.

### Table design

Two categories of table, with different mutation rules:

**Mutable-until-terminal (single current-state row per entity):**

- `virtual_positions` -- current position identity and lifecycle state. Updated in
  place only for fields that describe "current state" (`status`,
  `current_holding_day_count`, `current_close`, `unrealized_return`, running `mfe`/
  `mae`); entry-time fields (`entry_price`, `quantity`, initial rank/score/regime/ADV
  quintile) are set once at creation and never modified. Once a position's `status`
  becomes a terminal exit state, no further field on that row changes -- the
  day-by-day history lives in `position_snapshots`, not by mutating this row
  repeatedly.

**Append-only (one row per event, never updated or deleted):**

- `sandbox_runs` -- one row per CLI invocation attempt.
- `ranked_candidates` -- one row per (run, symbol) in a day's shadow top-10.
- `entry_orders` -- one row per candidate's order; `status` transitions
  (`PENDING` -> `FILLED`/`EXPIRED`/`SKIPPED`) are still row *updates* here (an order is
  a single lifecycle, not a stream of events) but every execution *attempt* is also
  recorded, append-only, in a per-attempt log column set (session date, that session's
  OHLC, fill/no-fill reason) so a `FILLED` order's full two-session attempt history is
  never lost to the final update.
- `position_snapshots` -- append-only, one row per (position, as-of date). This table,
  not `virtual_positions`, is the source of truth for "what did we say on day N."
- `recommendations` -- append-only, one row per (entity, as-of date, recommendation).
  Reconstructing a position's full history (`Day 1: BUY_FILLED`, `Day 2: HOLD`, ...,
  `Day 4: SELL_TARGET`) means reading this table in date order, never `virtual_positions`.
- `virtual_transactions` -- append-only BUY/SELL execution log.
- `data_quality_events` -- append-only findings log.

### Idempotency enforcement

Enforced with database constraints, not just application-level checks (an
application-level-only check is a race and a maintenance hazard):

- `UNIQUE(run_id, as_of_date)` on `sandbox_runs` keyed by a deterministic
  `configuration_hash` component, so re-running the same `as-of` with the same config
  resolves to the same `run_id` rather than inserting a new run row.
- `UNIQUE(symbol, as_of_date)` on `ranked_candidates`.
- `UNIQUE(candidate_id)` on `entry_orders` (one order per candidate).
- `UNIQUE(position_id, as_of_date)` on `position_snapshots`.
- `UNIQUE(entity_type, entity_id, as_of_date)` on `recommendations`.
- `UNIQUE(order_id)` / one-fill-per-order enforced by `entry_orders.status` transition
  logic plus a `CHECK`-backed application invariant (SQLite's `CHECK` support is
  sufficient for simple invariants; more complex ones are enforced in the repository
  layer inside a single transaction).
- All multi-row writes for one `as-of` step happen inside one SQLite transaction, so a
  partially-applied day is never visible.

A repeated command for an already-completed `(as_of_date, step)` returns the prior
run's persisted result rather than re-computing and re-inserting.

### Manifest fields on every run

`sandbox_runs` records: `run_id`, `as_of_date`, `started_at`, `completed_at`, `status`,
`model_version` (a fixed string identifying the frozen Model 2 implementation, tied to
the commit recorded in EXP-003), `data_snapshot_id` (where applicable),
`code_commit_sha` (reusing the existing `_git_commit()` /
`_provenance()` helper pattern from `stock_analyzer.datasets.swing_20.prepare`),
`configuration_hash`, `error_message`. Only repo-relative paths are ever stored --
never an absolute local filesystem path.

---

## Rationale

- SQLite requires no new service dependency, ships in the Python standard library, and
  is more than sufficient for a single-user local sandbox with a few hundred rows per
  trading day.
- Separating "current state" tables from "append-only event" tables directly encodes
  the audit requirement: you cannot lose history by design, because the row that
  changes (`virtual_positions`) is never the row that is read to prove what happened
  on a given day (`position_snapshots` / `recommendations`).
- Database-level uniqueness constraints make idempotency a property of the schema, not
  just of careful application code -- a bug in the service layer cannot silently
  double-insert.
- A repository interface (rather than SQL scattered across `application/`) keeps
  `domain/` and `application/` testable without a real database file where convenient,
  and keeps the storage engine swappable later without a rewrite.

---

## Consequences

- Every new sandbox feature that touches history must go through the repository's
  append-only methods for event tables -- direct `UPDATE`/`DELETE` against those tables
  from application code is a design violation and should be caught in review.
- `virtual_positions`' "current state" convenience row can technically be
  reconstructed from `position_snapshots` alone; keeping it is a deliberate
  read-performance and query-simplicity choice, not a second source of truth --
  reconciliation between the two is a natural addition to the idempotency test suite
  (Section 16 of the MVP 2 spec).
- SQLite's single-writer-at-a-time model is fine for one local CLI process; if the
  sandbox is later run concurrently (e.g. by an external scheduler with overlapping
  invocations), this ADR would need revisiting.
