# Experiment Record

## Experiment ID

```text
EXP-005 (REVISION 5 -- FROZEN)
```

```text
Status: FROZEN
Revision: 5
Frozen before implementation and before viewing any real EXP-005 results.
```

## Title

Minimal Portfolio-Policy Feasibility Pilot -- does frozen Model 2's ranking carry
economically usable selection value under one simple, realistic, capacity-constrained
portfolio policy, relative to a neutral ranking control under identical mechanics --
with enough recorded evidence to audit *why* each BUY, HOLD, and SELL decision turned
out the way it did, after the fact?

## Status

**FROZEN, REVISION 5.** Approved 2026-07-19 (ChatGPT / Architecture Advisor review of
Revision 5: no remaining architectural or methodological blocker). This document is
now the canonical source of truth for implementation. **No design element --
variants, thresholds, feasibility criteria, diagnostics, MFE/MAE definitions,
censoring rules, horizons, fill rules, or portfolio configuration -- may change during
implementation** unless implementation reveals a genuine contradiction that prevents
the approved design from being built as specified; any such case is a documented
deviation, not a silent edit. **No real EXP-005 financial result of any kind has been
generated as of this freeze.**

Revision 4 was reviewed as architecturally sound with no remaining blocking issues
("kein fundamental problem anymore") -- two enhancement suggestions were offered to
push reproducibility and analytics purity from "very strong" to complete, both
incorporated in Revision 5: (1) a single, explicitly named **Experiment Manifest**
(Section 29) consolidating every hash/identity already scattered across Sections 5/12,
so a question like "why was NVDA bought on 2026-02-18" is answerable bit-for-bit years
later from one document; (2) the decision-quality diagnostics layer formalized as a
strict **pure function of frozen inputs only** (Section 30:
`Result = f(SQLite, prices.parquet, Experiment Manifest)`), with a proposed small,
single-purpose module layout. Neither changed scope, mechanics, or any threshold from
Revision 4.

## Date

```text
2026-07-19
```

## Owner

Claude (agent), instructions relayed from ChatGPT (research lead) via Meelis Kivimäe.
Independent review: ChatGPT / Architecture Advisor (Revision 4 reviewed and approved
architecturally, with two enhancement suggestions, 2026-07-19; this is the response).

## Related documents

Unchanged from Revision 2.

---

# Change summary (Revision 4 -> Revision 5)

1. **Added an explicit, consolidated Experiment Manifest** (Section 29) -- every
   reproducibility hash/identity that was previously scattered across Section 5 (data
   sources) and Section 12/Stage 9 (freeze list) is now one named artifact with one
   fixed field list, so full bit-for-bit provenance of any single decision can be
   reconstructed from one document rather than reassembled from several sections.
2. **Formalized the decision-quality diagnostics layer as a pure function**
   (Section 30): `Result = f(SQLite, prices.parquet, Experiment Manifest)`, no other
   inputs, with a proposed small, single-purpose module layout
   (`mfe_mae.py`/`sell_quality.py`/`hold_quality.py`/`opportunity_cost.py`/
   `report_generator.py`) replacing the earlier single-file description.

No scope, mechanics, threshold, or schema changed -- both additions are
consolidation/precision on an already-approved design.

# Change summary (Revision 3 -> Revision 4, retained for history)

1. **Fixed the `portfolio_admissions`/`slot_reservations` foreign-key cycle**
   (Section 8.1): `portfolio_admissions` is the sole parent, with no physical
   `reservation_id`/`order_id` columns; `slot_reservations` and `entry_orders` are
   found by querying on `admission_id`/`candidate_id`, never by a back-reference.
2. **Added an immutable per-fill execution audit record** (`executions`, Section 18)
   retaining both the raw ADR-007 market price and the slippage-adjusted effective
   price for every BUY and SELL fill.
3. **Added decision-quality diagnostic definitions** for BUY timing, HOLD outcomes,
   SELL efficiency, and `NO_CAPACITY` opportunity cost (Sections 19-25) -- all
   **derived, post-hoc, from the frozen OHLCV artifact and already-persisted
   decision-time facts**, not stored as new per-day future-price rows in SQLite.
4. **Classified every field/table as `DECISION_TIME`, `POST_HOC_OUTCOME`,
   `ACCOUNTING`, or `PROVENANCE`** (Section 26), with a required test proving the
   post-hoc diagnostics module is never imported by any decision-executing service.
5. **Defined censoring rules** (Section 27) so an incomplete forward-looking window
   (right-censored by `outcome_data_end_date`) is never silently treated as zero or
   failure.
6. **Extended the freeze manifest** (Section 12, Stage 8) with the diagnostic
   layer's own frozen definitions -- horizons, MFE/MAE window rules, censoring rules,
   calendar identity -- none of which may change after real Variant B results are
   visible.

# Change summary (Revision 2 -> Revision 3, retained for history)

1. **Portfolio admission is now a separate, append-only domain event
   (`portfolio_admissions`)**, never a mutation of `RankedCandidate`. `actionable`/
   `exclusion_reason` keep their existing, unchanged meaning (ranking + data-quality +
   rank-limit + already-open/pending result only). `NO_CAPACITY` is a portfolio-level
   decision recorded separately (Section 8).
2. **Accepted admission, its slot reservation, and its entry order now commit as one
   atomic transaction**, with an explicit repository-level transaction boundary
   replacing the three-independently-committing-methods design (Section 8/11).
3. **The admission seam is no longer a one-line list filter.** It is a documented
   orchestration method (`admit_and_create_orders`) with a real (if narrow, if
   backward-compatible) change to `CandidateService`, specified exactly (Section 11).
4. **Implementation order now builds and freezes Variant D (and all reporting) before
   any real, financial-result-revealing Variant B run** -- the runtime benchmark stage
   uses synthetic or non-experiment data and reports only runtime/row counts, never
   P&L (Section 12).
5. **Control scores are a stable pure function of `(experiment_seed, as_of_date,
   symbol)`**, not a sequentially-consumed RNG stream -- invariant to row order, call
   order, and parallelism (Section 11.4).
6. **"Largest trade removed" is an arithmetic diagnostic on the realized ledger**, not
   a portfolio counterfactual rerun (Section 10).
7. **Daily equity snapshot timing is fixed and singular**: exactly one snapshot per
   day, taken after that day's full processing sequence, with drawdown/quarterly
   returns reading from it rather than being recomputed with different timing
   assumptions (Section 8.5).

# 1. Research question

Unchanged.

# 2. Hypotheses

Unchanged.

# 3. Variants

Unchanged from Revision 2 in scope and mechanics (historical reference A, active
Variant B, active Variant D, Variant C deferred). Variant D's control-scoring
mechanism is now specified precisely in Section 11.4 rather than "seeded random."

# 4. Fixed portfolio assumptions

Unchanged from Revision 2 (Section 4's table stands as-is).

# 5. Period and frozen data sources

Unchanged from Revision 2.

# 6. Execution semantics

Unchanged from Revision 2.

# 7. Financial accounting rules

Unchanged from Revision 2's equity/sizing/cost-basis definitions. Daily valuation
timing is now specified precisely -- see Section 8.5 (moved there because it is
inseparable from the admission/reservation event sequence).

# 8. Portfolio admission: domain model, atomicity, and daily valuation

## 8.1 Admission is a separate domain event, not a candidate mutation

**The Revision 2 defect:** the proposed admission policy ran just before order
creation, by which point `CandidateService` Phase 3 had already persisted the
candidate as an append-only `ranked_candidates` row with `actionable=True`. There is
no way to "un-actionable" that row afterward without violating the append-only
invariant (ADR-006) that every other part of this codebase relies on, and
`NO_CAPACITY` is not a ranking/data-quality fact about the candidate in the first
place -- it is a fact about the *portfolio's* state at the moment ranking finished.

**The fix:** `RankedCandidate.actionable`/`exclusion_reason` keep their existing
meaning exactly as today, completely untouched by portfolio capacity -- a candidate is
`actionable` if it is independently investable (data quality, rank-limit-to-3,
not-already-open/pending), full stop, regardless of whether the portfolio later has
room for it. A new, separate, append-only table records the portfolio-level decision:

```text
portfolio_admissions                       -- the sole parent; no forward references
  admission_id        TEXT PRIMARY KEY   -- = candidate_id (see 8.2: one candidate,
                                             at most one admission decision, ever)
  replay_id            TEXT NOT NULL      -- reuses the existing isolation key
                                             (one isolated database per variant/seed
                                             already means this is redundant with the
                                             file boundary, but is kept for the same
                                             reason replay_metadata.replay_id is kept
                                             on rows inside its own already-isolated
                                             database: auditability if ever combined)
  candidate_id         TEXT NOT NULL REFERENCES ranked_candidates(candidate_id)
  symbol               TEXT NOT NULL
  as_of_date           TEXT NOT NULL
  decision             TEXT NOT NULL CHECK (decision IN ('ACCEPTED','NO_CAPACITY'))
  rank_at_admission     INTEGER NOT NULL   -- the candidate's daily_rank at decision time
  slot_budget           REAL               -- $10,000 if ACCEPTED, NULL if NO_CAPACITY
  reason                TEXT               -- e.g. "10/10 slots reserved" for rejections
  created_at            TEXT NOT NULL

slot_reservations                          -- the child; references its parent only
  reservation_id   TEXT PRIMARY KEY
  replay_id         TEXT NOT NULL
  admission_id      TEXT NOT NULL UNIQUE REFERENCES portfolio_admissions(admission_id)
  candidate_id      TEXT NOT NULL
  symbol            TEXT NOT NULL
  reserved_amount   REAL NOT NULL          -- $10,000
  status            TEXT NOT NULL CHECK (status IN ('RESERVED','CONVERTED','RELEASED'))
  created_at        TEXT NOT NULL
  resolved_at       TEXT                    -- set when CONVERTED (fill) or RELEASED (expiry)
```

**Foreign-key cycle, fixed (Revision 4):** Revision 3's schema gave
`portfolio_admissions` its own `reservation_id`/`order_id` columns pointing forward at
`slot_reservations`/`entry_orders`, while `slot_reservations.admission_id` pointed
back with `NOT NULL` -- a mandatory two-way cycle that cannot be inserted in ordinary
order (neither row can be written first without violating the other's `NOT NULL` FK).
**`portfolio_admissions` is the sole parent; it carries no physical reference to its
reservation or order at all.** The reservation is found by querying
`slot_reservations WHERE admission_id = ?`; the order is found by querying
`entry_orders WHERE candidate_id = ?` (`candidate_id == admission_id`, so this is the
existing, already-unique `entry_orders.candidate_id` FK -- no new column needed on
`entry_orders`). Insert order inside the one atomic transaction (Section 8.2) is
therefore always well-defined: `portfolio_admissions` row first (no outstanding
references), then `slot_reservations` (its `admission_id` FK is now satisfiable), then
`entry_orders` (unchanged, existing FK to `ranked_candidates`).

**Integrity, enforced or validated as follows (no cyclic constraint required):**

- an accepted admission has exactly one reservation and one order -- guaranteed
  structurally, not just checked: the *only* code path that ever inserts a
  `slot_reservations` or matching `entry_orders` row is the single atomic
  `create_admission_acceptance` transaction (Section 8.2), which always writes
  exactly one of each per admission;
- a rejected (`NO_CAPACITY`) admission has neither -- `create_admission_rejection`
  never touches `slot_reservations`/`entry_orders`;
- a reservation belongs to exactly one admission -- `slot_reservations.admission_id
  NOT NULL UNIQUE`;
- an order belongs to the same candidate/admission -- trivial by construction
  (`entry_orders.candidate_id` is looked up using the same `candidate_id ==
  admission_id`, never a separately-chosen value);
- **no orphan rows survive commit** -- a repository-level consistency check function
  (callable standalone, and run in tests, Section 13) verifies: every
  `slot_reservations` row has a `portfolio_admissions` row with
  `decision='ACCEPTED'` and the same `admission_id`; every `entry_orders` row created
  via the EXP-005 path has a corresponding `ACCEPTED` admission for its
  `candidate_id`; no `NO_CAPACITY` admission has any matching `slot_reservations` row.

The attribution funnel becomes genuinely measurable at every stage:

```text
actionable -> admission accepted / no_capacity -> reservation created
-> order created -> filled / expired -> position opened -> exited
```

`NO_CAPACITY` never touches `ranked_candidates`. A `portfolio_admissions` row with
`decision='NO_CAPACITY'` exists alone -- no row is ever written to
`slot_reservations` or `entry_orders` for it (verified by the orphan-check in the
foreign-key discussion above).

## 8.2 Atomicity: accepted admission, reservation, and order commit together

For an **accepted** candidate, three writes must succeed or fail as one unit:
`portfolio_admissions(decision='ACCEPTED')`, `slot_reservations(status='RESERVED')`,
and the existing `entry_orders` row. **This is not achieved by calling three
independently-committing repository methods in sequence** (each of the sandbox
repository's existing `insert_*`/`create_*` methods calls `self._conn.commit()`
individually today -- calling three of them back to back leaves a window where the
process could die between commits, leaving an orphaned reservation without an order,
or vice versa).

**Repository transaction boundary, specified exactly:** a new
`SandboxRepository.create_admission_acceptance(admission, reservation, order)` method
owns all three writes inside one explicit transaction (`BEGIN IMMEDIATE ... COMMIT`,
the same pattern already used for the schema migrations in
`infrastructure/schema.py`), not three separate calls. Internally it reuses the exact
same INSERT logic `create_entry_order` already has for the `entry_orders` row -- that
INSERT body is factored into a small private, non-committing helper
(`_insert_entry_order_row(order)`) called by *both* the existing, unmodified
`create_entry_order` (still commits immediately on its own, still used by every
non-EXP-005 caller, behavior unchanged) *and* the new atomic method (which calls it
without an intervening commit, as one step inside its own larger transaction). This is
the one concrete, minimal refactor to existing code this proposal requires -- extracting
an insert body from behind its own commit, not rewriting `create_entry_order`'s
public behavior.

A separate, single-row method, `create_admission_rejection(admission)`, persists a
`NO_CAPACITY` admission alone (trivially atomic -- one INSERT).

**Idempotency / conflict / resume rules**, mirroring the pattern already established
for `insert_ranked_candidate`'s `RankedCandidateConflictError` (Section on Defect 1 of
the confirmed-defect repair stage) rather than inventing a new one:

- An identical repeat of `create_admission_acceptance` for the same `candidate_id`
  (same decision, same reservation content, same order content) is a safe no-op --
  returns the existing rows, does not re-insert or re-reserve. This is what makes a
  replay resume of a partially-processed boundary date safe: re-running admission for
  a candidate that was already accepted before an interruption must not consume a
  second slot.
- A conflicting repeat (same `candidate_id`, but a *different* decision, or an
  `ACCEPTED` admission with different reservation/order content than what is already
  persisted) raises `AdmissionConflictError` -- never silently overwritten, exactly
  the same posture as a genuine `ranked_candidates` conflict.
- **Structural invariants, guaranteed by the schema and the single-transaction write,
  not just by application-level discipline:** `slot_reservations.admission_id` is
  `UNIQUE` and `NOT NULL` -- a reservation cannot exist without exactly one accepted
  admission owning it, and an admission can own at most one reservation.
  `portfolio_admissions.admission_id = candidate_id` (primary key) -- one candidate can
  have at most one admission decision, ever, so it can reserve at most one slot, ever.
- The **default, non-EXP-005 sandbox path never calls either new method** -- see
  Section 11's orchestration seam. `CandidateService`'s existing behavior when no
  admission orchestrator is injected is completely unaffected by any of this.

## 8.3 Reservation lifecycle (unchanged in substance from Revision 2, restated in
terms of the new schema)

On accept (Section 8.2's atomic write): `slot_reservations.status = 'RESERVED'`. On
fill: `status -> 'CONVERTED'`, `resolved_at` set, and the reserved $10,000 is spent
exactly per the quantity formula already specified (Revision 2 Section 8, unchanged):
`quantity = (10,000 - entry_commission) / effective_entry_price`. On expiry (no fill
within the validity window): `status -> 'RELEASED'`, `resolved_at` set, full $10,000
returns to cash. On position close: exit proceeds return to cash per the existing
slippage/commission formula (unchanged). **Invariant, checked at every admission
decision and every fill/expiry/close event:**
`count(open positions) + count(reservations where status='RESERVED') <= 10`.
**Reconciliation invariant, checked after every event:** `cash + sum(reserved_amount
where status='RESERVED') + sum(open positions' current mark-to-market value) == total
portfolio equity`.

## 8.4 Deterministic ordering when several candidates compete for scarce capacity

Unchanged from Revision 2: processed strictly in rank order (`daily_rank`), ties
broken by symbol, ascending.

## 8.5 Daily valuation timing (fixed, singular)

Exactly one end-of-day portfolio equity snapshot is persisted per processed day,
taken **after** that day's full sequence: (1) pending entries processed (fills/
expiries/reservation releases), (2) open positions monitored and exits executed
(reservations already resolved by this point are irrelevant here; this step only
touches already-open positions), (3) new candidates ranked (Phase 1-3, unchanged),
(4) portfolio admissions/reservations/orders persisted (Phase 4, Section 8.1-8.2).
This exactly matches the existing day-loop order in `ReplayService._process_dates`
(entries -> monitoring -> candidates), simply extended: candidate generation's own
Phase 4 now ends with the admission step, and the snapshot is taken once that
returns.

```text
portfolio_equity_snapshots
  snapshot_id             TEXT PRIMARY KEY
  replay_id                TEXT NOT NULL
  as_of_date                TEXT NOT NULL UNIQUE (per replay_id)
  cash                       REAL NOT NULL
  reserved_capital           REAL NOT NULL   -- sum of RESERVED slot_reservations
  open_position_market_value REAL NOT NULL   -- mark-to-market, last valid frozen close
  total_equity                REAL NOT NULL   -- cash + reserved_capital + open_position_market_value
  open_position_count         INTEGER NOT NULL
  reserved_order_count        INTEGER NOT NULL
  cumulative_commissions      REAL NOT NULL
  cumulative_slippage_cost    REAL NOT NULL
  created_at                   TEXT NOT NULL
```

**Drawdown and quarterly returns are computed exclusively by reading this table** --
never recomputed from `virtual_transactions`/`virtual_positions` with independently
re-derived timing assumptions in the reporting layer, which is precisely the kind of
divergence that produced the holding-day/MFE/MAE staleness defect found in EXP-004's
own confirmed-defect repair stage. One source of truth for "what was equity on date
X," used by every downstream metric.

# 9. Transaction costs

Unchanged from Revision 2.

# 10. Metrics and feasibility criteria

Unchanged from Revision 2 except:

**Criterion 5 clarified as an arithmetic diagnostic, not a rerun:** "net return
remains positive after removing the largest winning trade" means exactly
`reported net P&L - largest closed winning trade's net P&L > 0`, computed once from
the actual, already-realized trade ledger. **The portfolio is never rerun with that
trade excluded** -- doing so would change capacity availability at the moment that
trade's slot would have freed, potentially admitting a different later candidate, and
would silently become a different experiment with a different admission history. This
is a concentration diagnostic on the realized outcome, nothing more.

**New required diagnostic, closing a gap the reviewer identified:** if unresolved
open positions materially contribute to a positive mark-to-market ending equity, the
**largest single open position's unrealized gain is also reported as a percentage of
total net P&L**, computed the same arithmetic way. This exists specifically so a
still-open, merely-lucky position cannot silently substitute for a closed winning
trade and dodge the concentration check -- it is reported alongside criterion 5's
closed-trade figure, not blended into it.

# 11. Architecture: the corrected seam

## 11.1 What changed from Revision 2

Revision 2 proposed a one-line `admission_policy.admit(candidates, as_of_date)` list
filter called just before order creation. That design cannot honestly record
`NO_CAPACITY` (Section 8.1) and provides no natural place for the atomic three-write
transaction (Section 8.2) to live. It is replaced by an explicit orchestration method
with a real, if narrow, transactional responsibility.

## 11.2 The seam, precisely

1. **Preserve** Model 2 scoring, `_build_candidate_draft`, and `_decide_selection`
   completely unchanged -- `actionable` candidates are computed exactly as today, with
   no awareness of portfolio capacity.
2. **Preserve** Phase 3 (persisting all `final_candidates` as append-only
   `ranked_candidates` rows) completely unchanged -- this happens before any admission
   decision and is untouched by this proposal.
3. **Change** the one line that is Phase 4 today:
   ```python
   orders = [self._create_entry_order(as_of_date, candidate) for candidate in actionable]
   ```
   becomes:
   ```python
   orders = self._admission_orchestrator.admit_and_create_orders(actionable, as_of_date)
   ```
4. **Add** one new optional `CandidateService.__init__` parameter,
   `admission_orchestrator: AdmissionOrchestrator | None = None`, defaulting to
   `DefaultAdmissionOrchestrator` -- an implementation whose `admit_and_create_orders`
   body is *exactly* today's list comprehension (`[self._create_entry_order(as_of_date, c)
   for c in actionable_candidates]`), touching `portfolio_admissions`/
   `slot_reservations` **not at all**. With the default, `generate_candidates`'s
   control flow, database writes, and output are unchanged from today -- every
   existing EXP-004-era regression test continues to exercise and protect exactly this
   path.
5. **For EXP-005 only**, inject a `CapacityAdmissionOrchestrator` implementing the
   same `AdmissionOrchestrator` interface: for each candidate in rank order, consult
   the `PortfolioLedger` for a free slot; if available, build the
   `PortfolioAdmission`/`SlotReservation`/`EntryOrder` triple and call
   `repo.create_admission_acceptance(...)` (Section 8.2, one atomic write); if not,
   call `repo.create_admission_rejection(...)` with `decision='NO_CAPACITY'`.

**Do not claim a simple list-filter alone is sufficient** -- it is not; the real
change is the orchestration method plus the two new repository methods plus the two
new tables, all specified above, not a one-line filter.

## 11.3 Corresponding hooks in `EntryService`/`MonitoringService`

Unchanged from Revision 2: optional, default-no-op lifecycle listeners at the existing
fill/expire (`EntryService.process_entries`) and close (`MonitoringService._close_position`)
call sites, so the ledger learns about reservation conversion/release without any
change to the fill or exit *decision* logic itself.

## 11.4 Deterministic control scoring (Variant D)

**The Revision 2 gap:** "seeded random scores" left open whether a single RNG stream
consumed in dataframe row order was intended -- which would make results depend on
row order, call sequence, or parallelism, none of which should matter for a
reproducibility-critical control.

**The fix:** `RankingControlAdapter.score(features_df)` computes each symbol's score
as a **stable pure function** of `(experiment_seed, as_of_date, symbol)` only:

```text
score(seed, as_of_date, symbol) = int(sha256(f"{seed}:{as_of_date.isoformat()}:{symbol}").hexdigest(), 16) / 2**256
```

This depends on nothing else -- not the dataframe's row order, not any process-global
RNG state, not previous calls, not how many symbols were scored on an earlier date,
not execution parallelism. Reordering the input `features_df` produces the identical
per-symbol score for every symbol (tested directly, Section 13). **Deterministic
collision handling:** in the (practically nil, but must be defined) event two symbols
hash to the exact same score value, the tie is broken by symbol name, ascending --
consistent with every other deterministic tie-break rule in this proposal (Section
8.4).

# 12. Implementation stages (revised: freeze before any real financial result)

**The Revision 2 defect:** Variant B's real, full-period run was scheduled (Stage 2)
before Variant D's ranking adapter and the reporting/seed/config freeze existed. That
sequencing would let Model 2's real financial result be seen before the control it
must be compared against was finalized -- even with frozen numeric thresholds, this
leaves undue analyst freedom in how the control gets built. Revised order:

1. Frozen OHLCV adapter (hash-verified at load, rejects mismatch/future bars).
2. Portfolio admission/reservation schema (`portfolio_admissions`, `slot_reservations`,
   `portfolio_equity_snapshots`, `executions` -- Section 18) and the atomic
   persistence methods (Section 8.2), with the foreign-key direction fixed per
   Section 8.1.
3. `PortfolioLedger` + the `CandidateService`/`EntryService`/`MonitoringService`
   lifecycle hooks (Section 11.2-11.3).
4. **Both** Variant B's Model 2 adapter path **and** Variant D's
   `RankingControlAdapter` (Section 11.4) -- built together, before either is run for
   real.
5. Financial accounting and reporting module (Section 10's metrics, the
   `portfolio_equity_snapshots`-driven drawdown/quarterly-return calculations).
6. **Decision-quality diagnostics module** (Sections 18-27) -- built and tested
   against synthetic fixtures now, since its formulas must be frozen (Stage 8) even
   though it does not run until after Stage 9. Includes the import-isolation test
   (Section 26).
7. Deterministic synthetic integration tests (Section 13) -- all pass before touching
   real frozen data.
8. **Runtime benchmark using synthetic data or a short, non-experiment technical
   slice.** If real frozen rows are needed to get a representative timing figure, use
   a short slice (e.g. a handful of trading days, or a small symbol subset) and report
   **only runtime and row counts -- never P&L, return, win rate, or any other
   financial outcome** from that slice. This stage answers "is 50 controls
   proportionate," nothing else.
9. **Freeze**, before any real comparison run -- generates the **Experiment
   Manifest** (Section 29, the single consolidated artifact) containing: code commit
   SHA; schema version; feature-dataset hash; OHLCV (`prices.parquet`) hash; a new
   portfolio-configuration hash (capital, slot count, per-slot budget, commission,
   slippage rate, admission tie-break rule -- analogous to
   `SandboxConfig.config_hash()`); the literal 50-seed list (or its deterministic
   generation rule, e.g. `range(1, 51)`); the exact feasibility-criteria thresholds
   (Section 10), as data, not just prose; **and, new in Revision 4, the
   decision-quality diagnostic layer's own frozen definitions**: the decision-audit
   schema version marker (Section 28); the diagnostic horizon lists (`[1,5,10,20]`
   for post-exit/entry-timing/`NO_CAPACITY`, `[1,5,10]` for HOLD); the MFE/MAE price
   basis and window rule (Section 20, including the
   entry/exit-session ambiguity treatment); the `NO_CAPACITY` counterfactual
   reference-price/hypothetical-fill rule (Section 24); the censoring rules (Section
   27); the market-calendar identity used for session counting. All recorded in one
   run-configuration manifest file before Stage 10 begins. **No diagnostic
   definition may change after real Variant B results become visible.**
10. Run Variant B once, and all 50 approved Variant D seeds, over the real, frozen,
    full period.
11. Generate decision-quality diagnostic reports (Section 25) from the completed,
    frozen replay databases plus the frozen OHLCV -- read-only against the databases
    written in Stage 10, using only the Stage 9 frozen formulas.
12. Write EXP-005 Part 2 (financial results plus decision-quality reports) without
    changing any code, constant, or diagnostic definition after Stage 9's freeze.

Stop-loss/Variant C: still not started this cycle (Section 3/15).

**No real EXP-005 financial result exists yet, and none will exist before Stage 8's
freeze is complete** -- this document itself contains no numbers from any real run.

# 13. Tests required before the real run

All of Revision 2's Section 13 tests remain required (reservation-capacity invariants,
cash-never-negative, expiry release, exit release, quantity-sizing equality, ledger
reconciliation, control-seed reproducibility, control-pool-scope regression, daily
mark-to-market/drawdown arithmetic against a hand fixture, unresolved-position
inclusion in headline equity, frozen-adapter hash/future-bar rejection), plus, newly:

- `NO_CAPACITY` leaves the candidate's `ranked_candidates` row completely unchanged
  (still `actionable=True`, `exclusion_reason` unaffected) and creates no
  `slot_reservations`/`entry_orders` row -- only a `portfolio_admissions` row with
  `decision='NO_CAPACITY'`.
- An accepted admission, its reservation, and its order commit together -- a single
  successful call produces all three rows.
- A forced failure injected partway through `create_admission_acceptance` (e.g. a
  simulated error before the final commit) rolls back all three writes -- none persist.
- Resume/retry: calling `create_admission_acceptance` again for an already-accepted
  `candidate_id` with identical content is a safe no-op and does not create a second
  reservation (does not consume a second slot).
- A conflicting repeat (different decision or content for an already-decided
  `candidate_id`) raises `AdmissionConflictError`.
- No orphan rows can exist: every `RESERVED`/`CONVERTED`/`RELEASED` reservation has
  exactly one owning `ACCEPTED` admission; every admission with `decision='ACCEPTED'`
  has exactly one reservation and one order; no `NO_CAPACITY` admission has a
  reservation or order.
- Control scores (and the resulting selection) are invariant to the input dataframe's
  row order, for a fixed `(seed, as_of_date)`.
- Exactly one `portfolio_equity_snapshots` row is written per processed day, after
  that day's full entry/monitoring/candidate/admission sequence -- verified by
  asserting snapshot content reflects post-admission state, not pre-admission state.
- Largest-closed-trade and largest-unresolved-open-position concentration diagnostics
  are computed correctly against a fixture with a known dominant trade, using
  arithmetic only (assert no portfolio rerun occurs -- e.g. by asserting the
  admission/reservation event log is untouched by computing the diagnostic).
- The real EXP-005 comparison run refuses to start unless every Stage 9 manifest
  field (commit SHA, schema version, feature hash, OHLCV hash, portfolio-config hash,
  seed list, criteria thresholds, and Revision 4's diagnostic-layer definitions --
  horizons, MFE/MAE rule, censoring rules, calendar identity) is present and
  populated -- a missing/empty field raises before any variant executes.

**Revision 4 additions:**

- Both `raw_market_fill_price` and `effective_fill_price` persist on every
  `executions` row and are never overwritten by each other.
- `commission`/`slippage_cost`/`gross_notional` reconcile exactly to
  `net_cash_flow` on every `executions` row.
- A BUY `executions` record reconciles to the resulting position's quantity and to
  the cash debit in `portfolio_equity_snapshots`.
- A SELL `executions` record reconciles to the position's realized P&L and to the
  cash credit in `portfolio_equity_snapshots`.
- Every open position, on every date it is monitored, has exactly one
  `position_snapshots` row (no gaps, no duplicates) -- reused/re-verified, not new
  behavior.
- MFE/MAE price, percentage, and date match a hand-computed path fixture, including
  the conservative entry/exit-session ambiguity exclusion (Section 20) applied
  correctly for both `FILLED_AT_OPEN`/`FILLED_AT_CEILING` and
  `SELL_TIME`/open-triggered-`SELL_TARGET`/intraday-triggered-`SELL_TARGET`.
- Post-exit horizon diagnostics compute correctly against a fixture with a known
  forward price path, including correct `is_censored` behavior near
  `outcome_data_end_date`.
- HOLD forward-path diagnostics compute correctly against a fixture, including the
  aggregate-bucket classification (Section 22).
- Expired-order opportunity diagnostics (Section 23) compute the ceiling-distance and
  hypothetical forward MFE/MAE correctly without creating any order/position.
- `NO_CAPACITY` missed-opportunity diagnostics (Section 24) compute correctly
  **without creating a `virtual_positions`, `slot_reservations`, or cash-ledger
  entry** -- asserted directly by checking those tables are unchanged after
  generating the diagnostic.
- Accepted-vs-rejected outcome comparison (Section 25, Capacity quality) produces a
  well-formed report against a fixture with both outcomes present.
- **Import-isolation test (Section 26):** `CandidateService`,
  `AdmissionOrchestrator`/`CapacityAdmissionOrchestrator`, `EntryService`,
  `MonitoringService`, and `ReplayService` do not import, directly or transitively,
  from the decision-quality diagnostics package.
- All Revision 4 diagnostic results are reproducible byte-for-byte from the frozen
  hashes/manifest -- rerunning the diagnostics module against the same completed
  replay database and the same frozen OHLCV produces identical output.

# 14. Known limitations

Unchanged from Revision 2.

# 15. Explicit non-goals

Unchanged from Revision 2.

# 16. Estimated new vs. reused components, and runtime

Unchanged from Revision 2's component table, plus (Revision 3) the two new tables
(`portfolio_admissions`, `slot_reservations`) and the new
`portfolio_equity_snapshots` table (Section 8.5), the two new repository methods
(Section 8.2), and (Revision 4) the new `executions` table (Section 18) and a new,
entirely separate **decision-quality diagnostics module** (Sections 18-27) -- all
additive, none modifying existing table schemas or existing methods' public behavior
except the one factored-out, non-committing insert helper (Section 8.2,
`_insert_entry_order_row`) behind `create_entry_order`'s unchanged public behavior.
See Section 28 for the full persisted-vs-derived breakdown.

Runtime estimate: unchanged, still explicitly hedged and unmeasured -- Section 12's
benchmark stage makes this concrete via a synthetic/technical-slice measurement
specifically so the 50-run commitment is based on a real number, not this narrative
estimate. The diagnostics module (Section 25's reports) runs once, read-only, after
Stage 10's real runs complete -- its own runtime is not gating the 50-control
decision and is not separately estimated here.

# 17. Resolved decisions

Unchanged from Revision 2's Section 17 table -- all 12 items remain resolved as
recorded. No new open decisions were introduced by Revision 3 or Revision 4; every
reviewer-flagged item across both rounds was a design/specification defect, not an
open choice requiring the project owner's input.

---

# Decision-quality observability (Revision 4)

**Guiding principle, stated once and applied throughout:** this layer adds *evidence*,
not *policy*. Every new field described below either (a) records a fact that already
existed at decision time but was not being persisted (Sections 18-19), or (b) is
computed strictly **after** the replay for that period has completed, from the frozen
OHLCV artifact and the already-persisted decision-time record (Sections 20-25).
Category-(b) values must never be read by, or influence, any decision made during the
replay -- Section 26 makes this a tested invariant, not just a design intent. No new
trading action, threshold, or rule is introduced anywhere in this part of the document.

# 18. Execution audit record

`virtual_transactions` (existing) already records that a BUY or SELL happened, at what
price and quantity -- but not the *raw* market price separately from the
*slippage-adjusted* price actually used for cash accounting (Revision 3's cost model
introduced that distinction; nothing currently persists both sides of it). A new,
append-only table, written once per fill (BUY or SELL) in the same event that already
updates the ledger (Section 8.3's fill/close handling):

```text
executions
  execution_id           TEXT PRIMARY KEY
  replay_id                TEXT NOT NULL
  variant_id                TEXT NOT NULL     -- 'B' or 'D'
  control_seed               INTEGER          -- NULL for Variant B
  order_id                    TEXT            -- FK to entry_orders, BUY side only
  candidate_id                 TEXT NOT NULL
  position_id                   TEXT          -- set once the position exists (BUY: at
                                                  this fill; SELL: already set)
  symbol                         TEXT NOT NULL
  side                             TEXT NOT NULL CHECK (side IN ('BUY','SELL'))
  decision_date                     TEXT NOT NULL  -- signal_date (BUY) / the as_of_date
                                                       the exit was decided (SELL, equals
                                                       execution_date for exits, since
                                                       exit decision and execution are
                                                       same-session)
  execution_date                     TEXT NOT NULL
  raw_market_fill_price                REAL NOT NULL  -- ADR-007's unadjusted price --
                                                          NEVER overwritten by slippage
  effective_fill_price                  REAL NOT NULL  -- slippage-adjusted, what cash
                                                          accounting actually uses
  quantity                                REAL NOT NULL
  gross_notional                           REAL NOT NULL  -- quantity * raw_market_fill_price
  commission                                REAL NOT NULL  -- $1.00
  slippage_rate                              REAL NOT NULL  -- 0.0005
  slippage_cost                               REAL NOT NULL  -- quantity * |effective - raw|
  net_cash_flow                                REAL NOT NULL  -- signed: negative for BUY
                                                                  (-(quantity*effective +
                                                                  commission)), positive
                                                                  for SELL (+(quantity*
                                                                  effective - commission))
  fill_reason                                    TEXT NOT NULL  -- reused vocabulary:
                                                                    FILLED_AT_OPEN /
                                                                    FILLED_AT_CEILING /
                                                                    SELL_TARGET / SELL_TIME
  market_data_snapshot_id                          TEXT NOT NULL  -- provenance, same
                                                                      value on every row
                                                                      within one replay
  created_at                                        TEXT NOT NULL
```

**Both prices are mandatory and neither is ever overwritten by the other**: the raw
price is what determines whether ADR-007's ceiling logic decided the order was
fillable at all (Section 6, unchanged); the effective price is what the cash ledger,
position quantity, and every financial report actually use. `net_cash_flow` must
reconcile exactly against `commission`/`slippage_cost`/`gross_notional` (tested,
Section 13) -- this is the single source of truth the cash ledger and
`portfolio_equity_snapshots` are built from, so a `virtual_transactions` row and its
corresponding `executions` row can never silently disagree.

# 19. Daily position-decision evidence

The requested evidence set (recommendation, entry facts, current OHLC, holding-session
count, target/planned-exit dates, running MFE/MAE, rank/score/regime context,
data-quality status) turns out to be **already fully reconstructable without any
schema change to `position_snapshots`**, once `executions` (Section 18) exists:

| Required field | Source (no new schema) |
|---|---|
| `position_id`, `symbol`, `as_of_date`, `recommendation`, `holding_session_count` (`holding_day_count`), `current_unrealized_return` (`cumulative_unrealized_return`), `running_mfe`/`running_mae` (`mfe`/`mae`), `current_rank`, `current_model_score`, `rank_change_from_entry`, `market_regime` (`current_market_regime`), `data_quality_status` | existing `position_snapshots` columns, unchanged |
| `recommendation_reason` | existing `recommendations` table, joined on `(entity_type='position', entity_id=position_id, as_of_date)` |
| `entry_date`, `target_price`, `planned_time_exit_date` | existing `virtual_positions` columns, unchanged |
| `raw_entry_price`, `effective_entry_price` | the position's BUY-side `executions` row (Section 18), joined on `position_id` |
| current session's Open/High/Low (Close already in `position_snapshots`) | the frozen `prices.parquet` itself, looked up by `(symbol, as_of_date)` -- **not persisted redundantly**, since the frozen source is immutable ground truth and always available |
| `market_data_snapshot_id` | `replay_metadata.market_data_snapshot_id` (existing field, one value per replay, not duplicated per row) |

**No schema change to `position_snapshots` is proposed.** This is a direct instance
of "prefer deriving... rather than duplicating every future price into SQLite" --
applied here to *current-day* OHLC too, not just future prices, since the frozen
source already has it. `MonitoringService`'s existing decision logic
(`_check_target`/time-exit) is unchanged; it still only produces
`HOLD`/`SELL_TARGET`/`SELL_TIME`/`MONITORING_BLOCKED` (confirmed: no `ADD`/`REDUCE`
this cycle, matching Section 3/15's scope).

# 20. MFE/MAE: complete path definitions and the entry/exit-session ambiguity rule

For every position (open or closed), computed post-hoc from the frozen OHLCV over its
holding window:

```text
MFE = (max observed High during the holding window - effective_entry_price) / effective_entry_price
MAE = (min observed Low during the holding window - effective_entry_price) / effective_entry_price
```

reported together with: the MFE/MAE **price and date** (not percentage alone -- needed
to answer "when," not just "how much"), sessions from entry to MFE and to MAE, the
realized-or-mark-to-market return, **peak-to-exit giveback** (`MFE% - realized_return%`
-- how much of the best favorable move was given back by the time of exit), and
**exit efficiency** (`realized_return / MFE` -- the fraction of the available favorable
move actually captured).

**Holding-window boundary rule (the ambiguity the reviewer flagged):** daily OHLC
cannot establish whether a session's High or Low happened before or after an
intraday-threshold fill/exit within that same session. The general, conservative
principle applied uniformly: **a session's own High/Low are included in MFE/MAE
attribution only when the executable moment within that session is unambiguous (the
session's own open or its own close); they are excluded when the executable moment was
an intraday threshold touch**, since ordering cannot be established and the position
must not be credited with a favorable excursion, or blamed for an adverse one, it may
not actually have experienced.

- **Entry session:** `FILLED_AT_OPEN` (executed at the session's open, the earliest
  possible point) -> that session's full High/Low **are included**.
  `FILLED_AT_CEILING` (executed when an intraday touch reached the ceiling, order
  within the session unknown) -> that session's High/Low **are excluded**; MFE/MAE
  tracking begins from the next session, using the known fill price as the starting
  reference.
- **Exit session:** `SELL_TIME` (executed at the session's close, the latest possible
  point, unambiguous) -> full High/Low **included**. `SELL_TARGET` via `open >=
  target` (executed at the session's open, unambiguous) -> full High/Low **included**.
  `SELL_TARGET` via an intraday `high >= target` touch (order within the session
  unknown) -> that session's High/Low **excluded** from MFE/MAE beyond the realized
  exit price itself (cannot claim a more extreme MFE/MAE happened that day without
  knowing if it preceded or followed the exit).

This rule is frozen (Section 12, Stage 8) before any real run and applied identically
to Variant B and every Variant D seed.

# 21. Post-exit diagnostics (was the SELL early or late?)

For every closed position, at fixed forward horizons of **1, 5, 10, and 20 trading
sessions after the exit session**, computed from frozen OHLCV starting the session
after exit:

close-to-close return from `effective_exit_price`; maximum subsequent High and minimum
subsequent Low relative to `effective_exit_price`; whether the position's own
`target_price` would have been reached after exit, within the horizon; the best and
worst post-exit excursion over the horizon; `is_censored` (Section 27) if the horizon
extends past `outcome_data_end_date` or the symbol's own available data ends first.

**These are diagnostic fields only and never feed replay decisions** (they cannot, by
construction -- they are computed after the replay for that period is complete).
**Positive post-exit performance is never automatically labelled "wrong sell"** -- a
sale can be rational under capacity, risk, and time constraints even if the stock kept
rising afterward. These fields are reported and labelled explicitly as **post-exit
opportunity/regret evidence**, a description of what happened next, not a verdict on
whether the sale was correct.

# 22. HOLD-decision diagnostics

For every daily `HOLD` snapshot (a `position_snapshots` row with
`recommendation='HOLD'`), at forward horizons of **1, 5, and 10 trading sessions**,
computed from frozen OHLCV: forward close return; maximum future High; minimum future
Low; whether the +20% target was subsequently reached within that specific horizon
(and sessions-until-target if so); `is_censored`. Additionally, per HOLD snapshot (not
horizon-specific): whether the position, as actually replayed, eventually exited
profitably.

**Aggregate summary buckets across all HOLD snapshots:** profitable continuation;
adverse continuation; target eventually reached; time exit eventually reached;
unresolved/censored. **HOLD correctness is explicitly not treated as binary** -- the
report shows the subsequent path and trade-off (e.g. "continued favorably for 5
sessions, then reversed before the eventual time exit"), not a single right/wrong
label. These are post-hoc diagnostics, never training labels, never new policy rules.

# 23. Entry-timing diagnostics (was the BUY well-executed?)

For every filled BUY: signal close (`ranked_candidates.signal_close`); the next
session's open (frozen OHLCV); raw and effective fill price (`executions`); entry gap
versus signal close (`(next_session_open - signal_close) / signal_close`); slippage
cost (`executions`); the fill price's location within the execution session's own
range (`(raw_fill_price - session_low) / (session_high - session_low)`, a 0-1
percentile -- 0 means filled at the session low, 1 at the session high); forward
return, MFE, and MAE (Section 20's rule, applied per horizon) at 1/5/10/20 sessions;
time to MFE/MAE within each horizon; whether the +20% target was reached within the
position's actual 20-session holding horizon.

For every unfilled/expired order: the ceiling (`max_entry_price`); the opens/lows/
highs of the sessions actually attempted (`entry_order_attempts`, already persisted,
reused unchanged); the closest distance the price came to the ceiling without
triggering (`min over attempted sessions of (session_low - ceiling) / ceiling`);
subsequent MFE/MAE computed from the ceiling price as the hypothetical reference,
tracked forward from the order's expiry date, at the same four horizons;
`is_censored`.

This distinguishes "the ranking picked a bad stock" from "the ranking picked a good
stock the entry rule couldn't reach at the permitted price" -- two different failure
modes that a raw fill-rate number alone cannot tell apart.

# 24. `NO_CAPACITY` opportunity-cost evaluation

For every `portfolio_admissions` row with `decision='NO_CAPACITY'`: the candidate's
rank and score at admission (`rank_at_admission`, already persisted); signal close and
max entry price (`ranked_candidates`); the portfolio's capacity state at rejection
(from the day's `portfolio_equity_snapshots` row -- open/reserved counts); which
specific open positions or reservations occupied the 10 slots that day (a lookup
against `slot_reservations`/`virtual_positions` state on that date); subsequent
1/5/10/20-session returns and MFE/MAE from the signal close (Section 20's rule);
**whether the existing ADR-007 entry rule would have filled**, computed as a read-only
hypothetical replay of that exact rule against the frozen OHLCV for the candidate's own
2-session validity window (no portfolio state touched); the hypothetical fill date and
raw price if so; `is_censored`.

**This is strictly observational.** It must not, under any circumstance, create a
`virtual_positions` row, a `slot_reservations` row, or any cash-ledger entry -- the
hypothetical-fill check reuses ADR-007's pure fill-decision logic against frozen
prices only, writing its result to a diagnostic output, never to the replay database's
decision-time or accounting tables. This is what makes it possible to judge whether
the capacity policy turned away opportunities better than what it actually held,
without contaminating the real portfolio's history.

# 25. Decision-quality report sections (pre-registered)

Generated once, after Stage 9's runs complete (Section 12), from the persisted
decision-time/accounting facts plus the frozen OHLCV, using the frozen definitions
above:

- **BUY quality:** fill rate; entry-gap and slippage distributions; post-entry returns
  at fixed horizons; MFE/MAE distributions; time to MFE/MAE; target-hit rate;
  entry-session ambiguity count (how many fills were `FILLED_AT_CEILING`, i.e. how
  often Section 20's exclusion rule applied).
- **HOLD quality:** HOLD-decision count; forward return/MFE/MAE after HOLD;
  target-reached-after-HOLD rate; adverse-continuation rate; broken down by holding
  age and current-unrealized-return bucket.
- **SELL quality:** realized return; MFE captured at exit; peak-to-exit giveback;
  exit efficiency; post-exit returns/excursions at fixed horizons; target-exit vs.
  time-exit comparison; count of censored post-exit observations.
- **Capacity quality:** `NO_CAPACITY` count; hypothetical-fill rate among them;
  missed-opportunity MFE/MAE distribution; accepted-vs-rejected outcome comparison;
  capital-utilization and idle-cash periods (from `portfolio_equity_snapshots`).
- **Selection quality (B vs. D):** Variant B compared against the Variant D
  distribution across entry quality, MFE/MAE, realized return, exit efficiency,
  target-hit rate, and `NO_CAPACITY` opportunity cost -- the same
  percentile-not-point-comparison discipline as Section 10's headline feasibility
  criteria.

# 26. Fact classification and decision/observation isolation

Every persisted field or computed report is classified as exactly one of:

- **`DECISION_TIME`** -- known and usable at the moment a decision is made:
  `ranked_candidates`, `portfolio_admissions`, `slot_reservations`, `entry_orders`,
  `entry_order_attempts`, `virtual_positions` (current-state columns as of the day
  being processed), `position_snapshots` (each day's own row), `recommendations`.
- **`ACCOUNTING`** -- a mechanical consequence of a decision/fill, not itself a
  forward-looking judgment: `executions`, `virtual_transactions`,
  `portfolio_equity_snapshots`.
- **`PROVENANCE`** -- fixed run identity/configuration, not date-varying:
  `replay_metadata`, the portfolio-configuration hash, the seed list, the freeze
  manifest (Section 12, Stage 8).
- **`POST_HOC_OUTCOME`** -- computed strictly after the replay completes, using data
  dated after the decision point: everything in Sections 20-25 (MFE/MAE full paths,
  post-exit diagnostics, HOLD forward diagnostics, entry-timing forward returns,
  `NO_CAPACITY` opportunity cost, the B-vs-D selection-quality comparison).

**Isolation, enforced structurally and tested:** the `POST_HOC_OUTCOME` diagnostics
live in their own module/package (e.g.
`stock_analyzer/sandbox/analytics/exp005_diagnostics.py`), separate from
`CandidateService`, `AdmissionOrchestrator`/`CapacityAdmissionOrchestrator`,
`EntryService`, `MonitoringService`, and `ReplayService`. A required test statically
verifies none of those five modules import from the diagnostics package (an
import-graph check, not just a code-review convention) -- so it is not merely
documented that post-hoc outcomes cannot influence decisions, it is mechanically
impossible for them to, and that impossibility is what the test suite checks on every
run.

# 27. Censoring and missing-data rules

For every fixed-horizon post-hoc outcome (Sections 21, 22, 23, 24): record the number
of trading sessions actually observed; record `is_censored` (true whenever the nominal
horizon extends past `outcome_data_end_date`, or past the last date the symbol has
frozen price data); **never silently substitute zero or "failure" for an incomplete
horizon** -- an incomplete observation is retained with its actual partial data and
its `is_censored` flag, not discarded or faked. `is_censored` further distinguishes
`END_OF_EXPERIMENT` (the window legitimately runs past `outcome_data_end_date`) from
`MISSING_MARKET_DATA` (a genuine gap in the frozen source within an otherwise
in-window horizon) -- these are different situations and must not be merged into one
flag. Aggregate statistics (means, distributions in Section 25's reports) either
exclude censored observations or report them in a clearly separate row -- never
blended into an aggregate as if complete. Horizons are counted in **trading sessions
from the frozen market calendar** (the same calendar `_outcome_only_dates`/SPY-based
convention already used to build EXP-004's date list, Section 5), never calendar days.

# 28. Persisted vs. derived: summary

| | |
|---|---|
| **New persisted SQLite tables (all append-only, all additive -- no existing table's columns change)** | `portfolio_admissions`, `slot_reservations`, `portfolio_equity_snapshots` (Revision 3), `executions` (Revision 4, Section 18) |
| **Existing tables reused unchanged** | `ranked_candidates`, `entry_orders`, `entry_order_attempts`, `virtual_positions`, `position_snapshots`, `recommendations`, `virtual_transactions`, `replay_metadata` |
| **Computed post-hoc, never persisted as new per-day SQLite rows** | every field in Sections 20-25: MFE/MAE full paths and price/dates, post-exit diagnostics, HOLD forward diagnostics, entry-timing forward returns and ceiling-distance, `NO_CAPACITY` hypothetical fills and opportunity cost, all five Section 25 report sections -- computed once, after Stage 9, by a dedicated read-only diagnostics module against the completed replay database plus the frozen `prices.parquet`, written out as diagnostic report artifacts (JSON/parquet, alongside the existing `replay_metrics.json` convention), not as new SQLite tables |

**Schema-versioning note, learned directly from this project's earlier v1/v2/v3 schema
saga:** `portfolio_admissions`, `slot_reservations`, `portfolio_equity_snapshots`, and
`executions` are all **wholly new tables**, not modifications to any existing table's
columns or constraints. `CREATE TABLE IF NOT EXISTS` creates them identically whether a
database is brand new or a pre-existing v3 database being reopened -- **no schema
version bump and no migration path are required** for any of this, unlike the
`ranked_candidates.signal_close` change that required the careful v1->v2->v3 discipline
already built and tested. A "decision-audit schema version" marker is still recorded
in the Experiment Manifest (Section 29) for future-cycle reference, but it gates
nothing this cycle.

# 29. Experiment Manifest

**One consolidated, explicitly named artifact** -- not scattered across the data-source
and freeze-list mentions in Sections 5 and 12, but one document/config object,
generated once at Stage 9's freeze and never edited afterward. Its purpose: given any
single recorded decision (e.g. "NVDA was admitted and bought on 2026-02-18"), this
manifest alone must be sufficient to reconstruct the exact universe, prices, features,
signals, calendar, code, schema, and configuration that decision was made under --
bit-for-bit, without guessing which snapshot or version was "probably" in effect.

```text
Experiment Manifest
  universe_hash              -- SWING_20's eligible-universe artifact hash, ALREADY
                                 recorded as source_swing20_artifact_hashes.universe
                                 in the existing features-snapshot manifest.json
                                 (6941b0d48153a59d7f9a768d83719955eaded62ae383f6db...)
  ohlc_hash                    -- prices.parquet's hash (Section 5) -- identical to
                                    that same manifest's
                                    source_swing20_artifact_hashes.prices
                                    (4cf0b9263eaec1022c635ef584f3e86a6c5003b3381...)
  feature_hash                   -- features.parquet's own feature_dataset_hash
                                      (Section 5) (5266a9f7cf4894b214c337af638d03c...)
  signal_hash                      -- the SWING_20 label artifact's hash
                                        (source_swing20_artifact_hashes.labels --
                                        target_20pct_20d and the rest of the frozen
                                        signal/outcome definition Model 2 was trained
                                        and this replay's positions are scored
                                        against)
  eligibility_hash                   -- source_swing20_artifact_hashes.eligibility,
                                          included for completeness since it gates
                                          which symbols ever entered the universe hash
                                          above
  calendar_version                     -- the sorted, hashed set of distinct trading
                                            dates present in prices.parquet over
                                            [signal_start_date, outcome_data_end_date]
                                            -- derived from the SAME already-frozen
                                            OHLC artifact, not a separate live SPY
                                            pull (closes the calendar-identity gap
                                            noted in Section 27)
  code_commit_sha                        -- git commit this run executed at
  schema_version                           -- core sandbox schema.py SCHEMA_VERSION
                                              (currently 3)
  decision_audit_schema_version              -- version marker for the Revision 3/4
                                                 additive tables (portfolio_admissions,
                                                 slot_reservations,
                                                 portfolio_equity_snapshots,
                                                 executions) -- see Section 28's note
                                                 on why this needs no migration path
  portfolio_configuration_hash                 -- capital, slot count, per-slot
                                                   budget, commission, slippage rate,
                                                   admission tie-break rule (Section 4)
  control_seed_list                              -- the literal 50 seeds (or generation
                                                       rule), Section 3
  feasibility_criteria                             -- Section 10's exact thresholds,
                                                        as data
  diagnostic_definitions                             -- Section 12/Stage 9's frozen
                                                          horizon lists, MFE/MAE rule,
                                                          NO_CAPACITY reference-price
                                                          rule, censoring rules
                                                          (Sections 20, 24, 27)
  spy_benchmark_snapshot_id                          -- if/when the one-time frozen
                                                          SPY pull (Section 5) is
                                                          completed; contextual only,
                                                          does not gate the primary
                                                          comparison
  generated_at                                         -- timestamp this manifest was
                                                            frozen
```

**Reproducibility guarantee this enables:** every hash above traces back to an
artifact that was already independently produced and hash-verified for SWING_20/Model
2's own frozen pipeline (`SWING20_Phase1_Artifact_Manifest.md`) -- EXP-005 adds no new
upstream data collection beyond the one-time SPY pull, only a consolidated pointer to
what already exists. Recomputing any field and comparing it against this manifest is
how a later reviewer verifies "this really is the run that produced these results,"
without re-running anything.

# 30. Analytics as a pure function of frozen inputs

**Formalized contract:** the entire decision-quality diagnostics layer (Sections
18-25) is a pure function --

```text
Result = f(SQLite replay database, prices.parquet, Experiment Manifest)
```

-- and nothing else. No live network access, no hidden global state, no dependency on
wall-clock time, no dependency on the order diagnostics are requested in, no access to
any database or artifact outside the three named inputs. Given the same three inputs,
the output is byte-identical on every invocation (already a required test, Section
13's "reproducible from frozen hashes" item) -- this is what "pure function" buys:
trivial testability (every diagnostic is a fixture-in, fixture-out unit test with no
setup beyond constructing the three inputs) and an unambiguous audit boundary (Section
26's isolation test verifies the *decision* side never reaches into this layer; this
section's purity contract verifies the *analytics* side never reaches outside its three
declared inputs).

**Proposed module layout** (small, single-purpose files, each independently testable):

```text
stock_analyzer/sandbox/analytics/
  diagnostics.py         -- shared loading/joining of the three inputs; nothing
                             variant-specific lives here
  mfe_mae.py              -- Section 20's MFE/MAE path computation and the entry/
                              exit-session ambiguity rule
  entry_timing.py           -- Section 23's BUY-quality diagnostics
  hold_quality.py             -- Section 22's HOLD forward-path diagnostics
  sell_quality.py                -- Section 21's post-exit diagnostics, peak-to-exit
                                     giveback, exit efficiency
  opportunity_cost.py              -- Section 24's NO_CAPACITY hypothetical-fill and
                                       missed-opportunity evaluation
  report_generator.py                -- Section 25's five report sections, assembled
                                         from the above modules' outputs
```

Each module's public functions take already-loaded, already-validated data (never the
raw file paths themselves, which `diagnostics.py` alone resolves against the
Experiment Manifest) and return plain, serializable structures -- keeping every
individual diagnostic formula testable in isolation, exactly matching Section 13's
per-diagnostic test list.
