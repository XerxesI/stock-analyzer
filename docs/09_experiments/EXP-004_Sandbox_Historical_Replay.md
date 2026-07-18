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
