# MVP 2 Specification v1.0 — Recommendation Sandbox and Forward Simulation

**Status:** FROZEN (before implementation begins)
**Project:** stock-analyzer
**Document owner:** project research process
**Related documents:**

- `docs/09_experiments/EXP-001_SWING20_Feature_Replication_and_Context_Mechanics.md`
- `docs/09_experiments/EXP-002_SWING20_Logistic_Baseline.md`
- `docs/09_experiments/EXP-003_SWING20_Locked_Test.md` (Model 2 PASSED; this MVP treats
  that result as frozen input, not something to re-derive or improve)
- `docs/04_decisions/ADR-006-Sandbox-Persistence-and-Audit-Trail.md`
- `docs/04_decisions/ADR-007-Next-Day-Entry-Simulation.md`

---

## 1. Purpose

Research (MVP 1 + EXP-001/002/003) established that a frozen Logistic Regression
ranking model ("Model 2") produces a daily cross-sectional ranking of SWING_20
candidates with a genuine, out-of-sample-validated edge (Locked Test: top-10-symbol
mean daily lift 2.74, 95% CI lower bound 2.26; see EXP-003).

That result answers a narrower question than "should we trade this." It says nothing
about whether the ranking can be turned into a *process*: a deterministic, repeatable,
auditable sequence of daily decisions that could actually be executed, with realistic
entry timing, realistic entry prices, and mechanical exit rules, without hindsight or
manual cherry-picking.

MVP 2 builds and tests that process, in a fully virtual ("sandbox") environment. It
does not touch real money, a broker, or portfolio-level risk management -- those are
explicitly later phases.

## 2. Hypothesis

> Frozen Model 2 ranking outputs can be converted into a deterministic, point-in-time-
> correct, auditable daily recommendation process that generates realistically
> executable virtual entries and manages them through the original 20-trading-day
> SWING_20 horizon without hindsight or manual candidate substitution.

MVP 2 does **not** claim the resulting virtual portfolio is profitable. It tests
whether the operational recommendation process is: reproducible, realistic, internally
consistent, executable, auditable, and stable enough to justify later portfolio and
risk research. Whether it makes money is an output to observe, not a pass/fail
criterion of this MVP (see Section 12, Acceptance Criteria -- none of the 18
conditions there are about realized returns).

## 3. Scope

### 3.1 In scope

- Point-in-time market-data cutoff handling for a given `--as-of` date.
- Frozen Model 2 inference via an adapter (Section 6) -- no re-implementation of its
  formulas.
- Daily cross-sectional ranking and shadow top-10.
- Deterministic selection of 1-3 actionable candidates from that top-10.
- Maximum acceptable entry price (provisional, non-optimized policy, Section 8).
- Next-trading-day virtual entry simulation with a 2-session validity window
  (Section 9).
- Pending-entry lifecycle (`BUY_PENDING` -> filled / skipped / expired).
- Open virtual positions with a fixed per-position virtual notional (Section 10).
- Daily `BUY_*` / `HOLD` / `SELL_*` / `SKIP_*` recommendations (Section 11).
- +20% target exit and 20-trading-day time exit, mechanically applied (Section 11).
- Daily close-based monitoring, MFE/MAE tracking (Section 12).
- Immutable (append-only) recommendation and transaction history (Section 13).
- CLI execution (`python -m stock_analyzer.sandbox ...`, Section 15).
- SQLite persistence with idempotent daily runs (Section 13, ADR-006).
- Automated tests (Section 16).
- Daily human-readable (Markdown) and machine-readable (JSON) reports (Section 14).

### 3.2 Explicitly out of scope

Not solved in MVP 2 -- these are deliberately deferred to a later risk/portfolio phase:

- Optimal stop-loss (no stop-loss rule exists in MVP 2 at all -- intentional, see
  Section 11).
- Optimal take-profit (target is fixed at the frozen SWING_20 +20% definition).
- Position sizing beyond a fixed equal virtual notional.
- Maximum concurrent position count.
- Portfolio capital allocation, sector exposure limits, portfolio optimization.
- Transaction-cost and slippage modeling/calibration.
- Broker integration or real order placement.
- Intraday monitoring (all decisions are end-of-day, using daily OHLC bars only).
- Model retraining, feature changes, or calibration repair.
- Live automated scheduling (the CLI is invoked manually or by an external scheduler
  not built here).

### 3.3 Frozen dependencies -- must not change in this MVP

The following are inputs to MVP 2, not subjects of it. None of them may be modified,
retrained, or re-tuned as part of this work:

- Model 2's feature list and order (`scripts/train_swing_20_logistic_baseline.py`,
  `make_design_matrix(..., "model2")`).
- Model 2's preprocessing (`fit_on_train`): standardization parameters, ADV-quintile
  and RVOL-decile bucket edges, all fit on train only.
- Model 2's Logistic Regression hyperparameters and coefficients (train-only fit,
  reproduced deterministically -- see EXP-003 Part 1).
- The SWING_20 target definition (`target_20pct_20d`, next-day-Open entry, 20-trading-
  day horizon, `stock_analyzer/datasets/swing_20/labels.py`).
- The Locked Test data and result (EXP-003) -- **the Locked Test is not reopened by
  MVP 2 for any reason.** MVP 2 runs entirely on data at or after Model 2's Locked
  Test period, or is replayed historically over data already seen in
  train/validation/locked_test for integration-testing purposes only (Section 17) --
  never to re-derive or challenge the EXP-003 verdict.
- The conclusions of EXP-001, EXP-002, and EXP-003.

MVP 2 must not: add or remove Model 2 features; retrain or recalibrate Model 2;
optimize any sandbox threshold (entry ceiling, holding horizon, target return) against
validation or Locked Test data; introduce LightGBM, XGBoost, a neural network, or any
"Model 3"; or present Model 2's raw output as a calibrated probability of success. Per
EXP-002/EXP-003, Model 2's calibration is not trustworthy as a probability (validation
slope 0.85, Locked Test slope 0.54) -- only its *ranking* was validated. The output is
called `model_score` (an ordinal ranking score) everywhere in this system, never
"probability" or "confidence."

## 4. Architecture

The sandbox is a strict pipeline, not a monolith. Each stage has a single
responsibility and reads/writes through the persistence layer, so a later stage can be
re-run, replaced, or inspected independently:

```
Research  →  Prediction  →  Decision  →  Recommendation  →  Virtual Portfolio  →  Monitoring  →  Feedback
(frozen)     (Model2         (candidate    (BUY_PENDING/     (positions,           (daily          (reports;
              adapter,        selection,    HOLD/SELL_*)      transactions)         snapshots,       NOT model
              daily rank)     entry price)                                          rank drift)      retraining)
```

Package layout (see `stock_analyzer/sandbox/`):

```
stock_analyzer/sandbox/
  domain/            # Plain dataclasses: Candidate, EntryOrder, Position, Recommendation, Transaction
  application/        # Services: CandidateService, EntryService, MonitoringService,
                       # RecommendationService, DailyRunService (orchestration)
  infrastructure/      # SqliteRepository, schema.py, Model2PredictionAdapter,
                       # a thin market-data + data-cutoff wrapper around get_stock_data
  reporting/           # daily_json_report.py, daily_markdown_report.py
  cli.py               # argparse CLI, python -m stock_analyzer.sandbox
```

The sandbox never duplicates Model 2's math. `infrastructure/model2_prediction_adapter.py`
imports `fit_on_train`, `make_design_matrix`, `train_logistic`, `compute_logit` from
`scripts/train_swing_20_logistic_baseline.py` (via the same `sys.path.insert(PROJECT_ROOT)`
pattern already used by `scripts/analyze_swing_20_context_target_mechanics.py` to import
from a sibling `scripts/*.py` module) and calls them, rather than re-implementing the
formulas.

## 5. Point-in-time rules

- An `--as-of DATE` argument always means "the last completed US trading day whose
  close is known." The CLI does not verify market-calendar validity of that date
  beyond checking that price data exists for it; the caller is responsible for passing
  a real, completed trading day.
- Feature computation for `as-of DATE` uses only price bars with date `<= DATE`. This
  differs from `stock_analyzer.datasets.swing_20.prepare._apply_current_day_cutoff`
  (which drops bars dated *on or after* "today" because today's bar is still
  incomplete) -- here, `DATE` is asserted by the caller to already be a *closed* day,
  so its own bar is included, and only bars strictly after it are excluded. This is
  implemented in `infrastructure/market_data_adapter.py` as
  `fetch_as_of(symbol, as_of_date)`.
- A newly generated candidate (built from `DATE`'s close) is never filled at `DATE`'s
  close. The earliest possible fill is `DATE`'s next trading session (Section 9).
- Monitoring for `DATE` uses only OHLC bars dated `<= DATE`.
- No sandbox stage may read a bar dated after the `as-of` date being processed, at any
  point in the pipeline.

## 6. Daily candidate-generation policy

After each completed US trading day (`as-of DATE`):

1. Build point-in-time Model 2 features for every eligible symbol using data available
   at `DATE`'s close (reusing the same feature computation the frozen model was fit
   and Locked-Test-evaluated against -- `stock_analyzer.datasets.swing_20.features`).
2. Apply the frozen Model 2 (train-only fit, reproduced deterministically per
   EXP-003 Part 1) to get each symbol's `model_score`.
3. Rank all eligible symbols by `model_score` descending -- this is the daily
   cross-sectional ranking, computed exactly as in EXP-002/EXP-003's
   `daily_rank_metrics`.
4. Persist the full top-10 as the **shadow candidate set**, regardless of how many
   become actionable. This lets later analysis separate ranking-engine performance
   from decision-policy effects.
5. Select at most 3 **actionable** candidates from that top-10, in rank order, skipping
   a symbol only for one of these objective, machine-recorded reasons:
   - `MISSING_MARKET_DATA`
   - `INVALID_PRICE` (non-positive or missing close)
   - `MISSING_ATR`
   - `STALE_DATA`
   - `ALREADY_OPEN_POSITION`
   - `ALREADY_PENDING_CANDIDATE`
   - `CORPORATE_ACTION_OR_SYMBOL_INTEGRITY_FAILURE`
   - `NO_NEXT_SESSION_DATA` (historical replay only, when there is no future bar to
     execute against)

No discretionary alpha filters (chart appearance, sector, liquidity preference beyond
what Model 2 already encodes, analyst opinion, news sentiment, extra RSI/technical
conditions) are applied in MVP 2. Every excluded top-10 symbol gets an explicit
`exclusion_reason`; every actionable candidate gets an `entry_order`.

## 7. Price precision policy

No project-wide price-rounding convention existed before this MVP (existing code
rounds ad hoc, e.g. `stock_analyzer/backtesting/backtest.py` uses 4 decimal places for
prices and 2 for currency/portfolio totals). MVP 2 formalizes, for its own use only:

- **Prices** (signal close, ATR14, max entry price, fill price, target price, exit
  price): rounded to **4 decimal places**, matching the existing backtest module's
  convention.
- **Virtual notional and realized/unrealized P&L**: rounded to **2 decimal places**.
- **Returns/ratios** (unrealized return, MFE, MAE): stored unrounded as floats;
  rounded only at report-rendering time (4 decimal places in JSON, percentage with 2
  decimal places in the Markdown report).

Rounding uses Python's `round()` with banker's rounding (the language default),
applied once, at the point a value is finalized (e.g. at fill time, not recomputed on
every read).

## 8. Provisional maximum-entry-price policy

```
max_entry_price = min(
    signal_close * 1.02,
    signal_close + 0.25 * ATR14
)
```

- `signal_close` is the close of the completed signal day (`as-of DATE`).
- `ATR14` is `stock_analyzer.core.indicators.calculate_indicators(...)["ATR14"]`
  (pandas_ta Average True Range, 14-period, Wilder smoothing), computed using only
  bars `<= DATE` -- reused as-is, not reimplemented.
- Constants are configurable (`SandboxConfig.max_close_extension_pct = 0.02`,
  `SandboxConfig.atr_extension_multiple = 0.25`) but frozen at these defaults for MVP
  2. **These values are not claimed to be optimal.** They are deliberately
  conservative, non-optimized operational defaults, chosen for forward testing, not
  fit to validation or Locked Test data, and must not be tuned against either.
- All inputs (`signal_close`, `ATR14`, both candidate ceiling terms) and the final
  `max_entry_price` are persisted on the `ranked_candidates` row.

## 9. Entry execution policy

Signals are generated only after the signal-day close; the sandbox never fills at the
signal-day closing price. The earliest possible fill is the next trading session.

```
If next_day_open <= max_entry_price:
    fill at next_day_open
Else if next_day_low <= max_entry_price < next_day_open:
    fill at max_entry_price
Else:
    no fill for that day
```

- Entry validity window: **at most 2 trading sessions** after the signal date. If
  unfilled after both attempts, the order becomes `EXPIRED_ENTRY`.
- If the very first checked session's price is entirely above the ceiling, the
  candidate-level outcome for that attempt is `SKIP_PRICE_TOO_HIGH` for that session
  (the order itself remains pending until the 2-session window is exhausted or a fill
  occurs).
- Every simulated execution attempt records: signal date, intended execution date,
  actual execution date, signal close, that session's OHLC, `max_entry_price`, fill
  price (if any), fill reason, and no-fill reason (if applicable).
- The execution engine reads only that session's own OHLC bar -- point-in-time
  correct and fully deterministic (no randomness anywhere in this MVP).

See `docs/04_decisions/ADR-007-Next-Day-Entry-Simulation.md` for the rationale.

## 10. Virtual-position policy

- **Equal fixed virtual notional per filled position**: `virtual_notional = 1000`
  (monetary units; not real currency, not calibrated to any account size).
- `quantity = virtual_notional / fill_price` -- fractional virtual shares are
  explicitly allowed; this is a normalization mechanism for comparable virtual P&L,
  not position-sizing research.
- No maximum concurrent-position limit is imposed. MVP 2 observes, but does not
  constrain, how many positions the policy naturally opens at once.
- Each `virtual_positions` row stores (see ADR-006 for the full schema): identity
  (`position_id`, `symbol`, `candidate_id`), entry facts (`signal_date`,
  `entry_date`, `entry_price`, `quantity`, initial rank, initial `model_score`,
  `signal_close`, `max_entry_price`, initial ADV quintile, initial market regime),
  live state (`status`, `current_holding_day_count`, `current_close`,
  `unrealized_return`, `mfe`, `mae`), and exit facts (`target_price`,
  `planned_time_exit_date`, `exit_date`, `exit_price`, `exit_reason`,
  `realized_return`).

## 11. Recommendation policy

Fixed, deterministic vocabulary for MVP 2 -- no free-text or scored recommendations:

**Candidate-level:** `BUY_PENDING`, `BUY_FILLED`, `SKIP_PRICE_TOO_HIGH`,
`EXPIRED_ENTRY`, `SKIP_DATA_QUALITY`, `SKIP_ALREADY_OPEN`.

**Open-position-level:** `HOLD`, `SELL_TARGET`, `SELL_TIME`, `MONITORING_BLOCKED`,
`SELL_DATA_FAILURE` (defined but never emitted automatically in MVP 2 -- see "Data
failure" below).

No stop-loss recommendation exists in MVP 2. This is intentional -- stop-loss design
is risk-management research, deferred to a later phase (Section 3.2).

### Holding-day counting convention

**The entry day is holding day 1**, not holding day 0. This exactly mirrors the frozen
SWING_20 label's own window definition (`stock_analyzer/datasets/swing_20/labels.py`,
`label_at`): `future = df.iloc[entry_pos:horizon_end_exclusive]` includes the entry
bar itself as the first (`offset=1`) day of the 20-day window, so the label's own
"day 20" is the entry day plus 19 further trading sessions. MVP 2 uses the identical
convention: `planned_time_exit_date` is the entry date's own trading session plus 19
further trading sessions (the 20th session counting the entry session as session 1).
A position's target can therefore be checked -- and hit -- on its own entry day, the
same as the frozen label allows. This convention is asserted in tests (Section 16).

### Target exit

```
target_price = entry_price * 1.20
```

Using each day's own OHLC, starting with the entry day itself and continuing through
`planned_time_exit_date`:

```
If daily_open >= target_price:
    exit at daily_open,  recommendation = SELL_TARGET
Else if daily_high >= target_price:
    exit at target_price,  recommendation = SELL_TARGET
```

### Time exit

If the target has not been reached by `planned_time_exit_date`'s close, exit at that
close. Recommendation: `SELL_TIME`.

### Data failure

**Revised per review (see EXP-004 discussion): a missing price bar never mechanically
sells a position, regardless of how many consecutive days it persists.** An earlier
version of this spec allowed a calendar-days-since-last-snapshot proxy to trigger
`SELL_DATA_FAILURE` after a configured threshold. That proxy is not a reliable enough
signal of a genuine terminal event to justify a virtual sale, and has been removed.

The current, conservative policy: a missing daily bar for an open position produces
(1) a `data_quality_events` row and (2) a `MONITORING_BLOCKED` recommendation event for
that day -- both persisted, so the gap is explicit and auditable. No snapshot is
recorded for that day (never fabricate a price), and the position's status stays
`OPEN` and unresolved, however long the gap lasts. `SELL_DATA_FAILURE` remains defined
in the recommendation vocabulary for a future, formally confirmed terminal-event
detector (delisting, cash merger, or other documented instrument-lifecycle
termination) -- MVP 2 has no such detector, so this recommendation is never emitted
automatically. Until one exists, an unresolved position is reported explicitly (see
EXP-004's "unresolved positions" requirement) rather than closed with an invented exit
price.

## 12. Daily monitoring

For every open position, on every `as-of DATE` on or after its entry date, compute and
append (never overwrite) a `position_snapshots` row: close price, daily return,
cumulative unrealized return, holding-day count (per the convention above), MFE/MAE
since entry, distance to target, current model rank and `model_score` (when the symbol
remains in that day's eligible universe -- `null` otherwise, not fabricated), rank
change from entry, current ADV quintile, current market regime, data-quality status,
and the day's recommendation. The full recommendation history for a position must be
reconstructable purely from persisted, append-only rows (e.g. `Day 1: BUY_FILLED / Day
2: HOLD / Day 3: HOLD / Day 4: SELL_TARGET`).

## 13. Persistence model

SQLite (no existing persistence/repository abstraction exists in this codebase to
reuse -- confirmed by repo survey before this MVP). A repository interface separates
domain logic from SQLite specifics so the storage engine could later be swapped
without touching `application/` or `domain/`. See
`docs/04_decisions/ADR-006-Sandbox-Persistence-and-Audit-Trail.md` for the full schema
(tables: `sandbox_runs`, `ranked_candidates`, `entry_orders`, `virtual_positions`,
`position_snapshots`, `recommendations`, `virtual_transactions`,
`data_quality_events`) and idempotency-constraint rationale.

Idempotency requirement: running the same CLI command twice for the same `as-of DATE`
with the same data and configuration must not create duplicate candidates, positions,
fills, snapshots, recommendations, or exits -- the second run either returns the
already-completed run's result or verifies the output is identical. Every run records
git commit SHA, frozen model version identifier, feature snapshot identifier (where
applicable), configuration and its hash, data-cutoff timestamp, and run timestamp
(`sandbox_runs` table). No absolute local filesystem paths appear in any durable
report or database row -- only repo-relative paths.

## 14. Reports

Per `as-of DATE`, two reports are generated under `artifacts/sandbox/daily/DATE/`
(generated output -- excluded from git like every other `artifacts/` path, per the
existing `.gitignore` policy):

- `sandbox_daily_report.json` -- machine-readable: run status, data cutoff, shadow
  top-10, selected 1-3 candidates, exclusions and reasons, pending entries, entries
  filled today, entries skipped or expired, open positions with current
  recommendation, exits today, virtual realized P&L, unrealized P&L, MFE/MAE,
  data-quality alerts.
- `sandbox_daily_report.md` -- the same content, human-readable.

## 15. CLI design

```
python -m stock_analyzer.sandbox generate-candidates --as-of YYYY-MM-DD
python -m stock_analyzer.sandbox process-entries     --as-of YYYY-MM-DD
python -m stock_analyzer.sandbox monitor             --as-of YYYY-MM-DD
python -m stock_analyzer.sandbox daily-run           --as-of YYYY-MM-DD
```

No `python -m stock_analyzer.*` CLI pattern existed in this repo before MVP 2
(confirmed by repo survey -- all prior entry points are standalone `scripts/*.py`
files); `stock_analyzer/sandbox/cli.py` + `stock_analyzer/sandbox/__main__.py`
introduce this pattern for the first time, scoped to the sandbox package only.

There is deliberately no `execute-recommendations` command. An earlier draft had one
as a documented no-op (BUY fills execute inside `process-entries`, SELL exits inside
`monitor`, so there was never a separate queue of "approved but unexecuted"
recommendations to apply) -- removed after review, since a command that appears to
execute something but does nothing is misleading regardless of how clearly it is
documented.

`daily-run`'s orchestration order is fixed and tested:

1. Process pending entries (using `DATE`'s own OHLC for orders created on prior days).
2. Monitor currently open positions (including any position that just got filled in
   step 1 -- consistent with the entry-day-is-holding-day-1 convention, Section 11).
3. Execute exit recommendations from step 2.
4. Generate new candidates from `DATE`'s completed close.
5. Create new pending entry orders for the actionable candidates from step 4.
6. Generate the daily JSON + Markdown reports.

A candidate generated in step 4 (from `DATE`'s close) is never processed as an entry
on `DATE` itself -- its earliest possible fill is `DATE`'s next session, handled by
step 1 of a *later* `daily-run` invocation.

## 16. Testing requirements

Minimum coverage (see Section 18 for which commit introduces each group):

- **Entry price**: 2% cap binds; 0.25*ATR binds; missing ATR; invalid close;
  deterministic rounding.
- **Entry execution**: next open below ceiling; next open exactly at ceiling; gap
  above ceiling but intraday low reaches it; entire day above ceiling; first session
  no fill then second session fills; two sessions pass with no fill (`EXPIRED_ENTRY`);
  no same-signal-day fill.
- **Candidate lifecycle**: full shadow top-10 retained even when fewer become
  actionable; only top-3-eligible become actionable; already-open symbol excluded;
  already-pending symbol excluded; exclusion reason persisted for every skip.
- **Position lifecycle**: target reached at open; target reached intraday; no target
  -> `HOLD`; time exit fires on exactly the right trading-day count (holding day 20,
  entry day = holding day 1); no premature time exit; no duplicate exits.
- **Persistence**: duplicate daily run is idempotent; duplicate recommendation
  prevented; duplicate fill prevented; snapshots are append-only; database constraints
  enabled (foreign keys, uniqueness).
- **Point-in-time correctness**: signal date uses no next-day values; max-entry
  calculation uses signal-day information only; entry execution uses only the later
  execution day's own OHLC; monitoring for `DATE` uses no bar dated after `DATE`.
- **Frozen-model protection**: a regression test/manifest assertion that the sandbox's
  Model 2 feature list and adapter identity match the frozen implementation
  (`scripts/train_swing_20_logistic_baseline.py` at the commit recorded in EXP-003).

## 17. First historical smoke test

After implementation, one deterministic replay over a small historical date window
(not chosen to make any rule look good, and not later used to tune anything) verifies
integration only: daily ordering, no same-day fills, pending-entry behavior, position
creation, target/time exits, report generation, and idempotent reruns. Its result is
explicitly labeled:

```
INTEGRATION_REPLAY — NOT MODEL VALIDATION
```

No rule is optimized based on this replay's outcome.

## 18. Known provisional assumptions

These are operational defaults needed to make the sandbox runnable, not optimized
investment rules, and are explicitly labeled as such wherever they appear in code and
reports:

- Entry ceiling constants (2% close extension, 0.25x ATR extension).
- 2-trading-session entry validity window.
- Fixed $1000 virtual notional per position, no concurrent-position cap.
- No stop-loss.
- No terminal-event detector for `SELL_DATA_FAILURE` exists yet -- a missing bar
  always produces `MONITORING_BLOCKED` and leaves the position open/unresolved.

## 19. Future extension points (explicitly not built in MVP 2)

- Stop-loss policy research.
- Position sizing / capital allocation / portfolio-level risk limits.
- Transaction-cost and slippage calibration.
- Paper-trading integration with a real (simulated-broker) feed.
- `MANUAL_OVERRIDE` events -- manual interventions may eventually be supported, but
  must be stored as explicit override events and excluded from the mechanical-strategy
  evaluation. Not implemented in MVP 2.
- Live automated scheduling.

## 20. Acceptance criteria

MVP 2 is complete when all of the following hold:

1. Frozen Model 2 generates a daily ranking for a specified completed trading day.
2. The full shadow top-10 is persisted.
3. At most 3 actionable candidates are created deterministically.
4. Maximum entry prices are calculated using the frozen provisional policy.
5. Entries are simulated no earlier than the next trading session.
6. Pending entries fill, skip, or expire deterministically.
7. Filled entries create virtual positions.
8. Open positions receive one daily recommendation.
9. Target and time exits are executed mechanically.
10. Position snapshots and recommendations are append-only.
11. Re-running a completed date does not duplicate or alter results.
12. Every decision contains a reason code.
13. Daily JSON and Markdown reports are generated.
14. Tests demonstrate point-in-time correctness.
15. Model 2 and Locked Test artifacts remain unchanged.
16. `git status` remains clean except for intended source and documentation changes.
17. Generated sandbox state and reports remain excluded under `artifacts/`.
18. No manual substitution, retroactive candidate removal, or retroactive fill-price
    change occurs anywhere in the mechanical pipeline (Section 19's durable
    principle).
