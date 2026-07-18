# SWING_20 Dataset Specification v1.0 — Frozen

**Status:** FROZEN
**Project:** stock-analyzer
**Frozen on:** full-US snapshot `swing20_20260718T135238Z` (5,370 symbols)
**Audit decision:** `CONDITIONALLY_TRAINABLE`
**Related documents:**

- `docs/02_mvp/MVP_1_Specification.md` (aspirational audit requirements this specification satisfies)
- `docs/04_decisions/ADR-001-SWING20.md`, `ADR-002-Target20.md`, `ADR-003-NextOpen.md`, `ADR-004-DatasetAuditBeforeModel.md`
- `stock_analyzer/datasets/swing_20/` (implementation)
- `docs/00_goal/Research_Strategy_v2.md` (governing research philosophy adopted after this
  document was frozen — does not modify this contract)

This document freezes the concrete, as-built definitions behind the SWING_20 dataset that
produced a `CONDITIONALLY_TRAINABLE` audit decision. It exists so that feature and baseline
work references one frozen contract instead of chat history. Changing any definition here
requires a new version of this document and a re-run of the Dataset Audit, per
`docs/02_mvp/MVP_1_Specification.md` section 31 (no silent redefinition).

---

## 1. Universe

- **Source:** `full_us` (`stock_analyzer.data.universe_filter.build_full_universe`), resolved
  fresh at snapshot-build time — not point-in-time (see Limitations).
- **Eligibility filters** (date-specific, re-evaluated per signal date):
  - `minimum_price = 5.0`
  - `minimum_adv20 = 5,000,000` (20-day average dollar volume)
  - `minimum_history_days = 250`
- **Exchanges observed:** NASDAQ, NYSE, NYSE American.
- **Instrument type:** `COMMON_STOCK` only.
- **Exclusion reasons** (countable, reported in the audit): `INSUFFICIENT_HISTORY`,
  `LOW_PRICE`, `LOW_ADV20`.

## 2. Current-Day Cutoff

**Policy:** `EXCLUDE_CURRENT_NEW_YORK_DATE` (unconditional).

Any price bar dated on or after the current America/New_York calendar date is dropped before
labels/eligibility are computed. A same-day bar can be fetched mid-session with High/Low that
haven't caught up to a live Open/Close snapshot; excluding it unconditionally makes a snapshot
reproducible regardless of what time of day (or timezone) it was built, at the cost of at most
one day of freshness. Recorded per snapshot: `requested_end_date`, `effective_end_date`,
`rows_removed_as_incomplete_current_day`, `symbols_affected_by_current_day_removal`.

Implementation: `stock_analyzer/datasets/swing_20/prepare.py` (`_apply_current_day_cutoff`).

## 3. Entry Definition

Entry is the **next trading day's Open** after the signal date, never the signal-day Close.
This is a realistic-execution constraint: a live system cannot act on a signal before the
next session opens.

```text
signal_date = t
entry_date  = next trading day after t
entry_price = Open[entry_date]
```

## 4. Horizon

**20 actual ticker trading bars** after entry — a positional count (`iloc`-based), not a
calendar-day count. Holidays, halts, or missing bars for a specific ticker do not shrink or
stretch the window; the window is always exactly 20 rows of that ticker's own trading history.

## 5. Primary Target Definition

```text
target_price = entry_price * 1.20
positive     = any(High[d] >= target_price for d in the 20-bar horizon)
```

The signal-day High is never used for target detection — only future bars count. `close_return_20d`,
`mfe_20d`, `mae_20d`, and `days_to_target` are computed alongside the binary label as outcome
diagnostics, not as alternative labels.

## 6. Stop Diagnostics

`fixed_stop = -0.08` (relative to entry price) and `target_before_fixed_stop` are computed and
reported, but **do not alter the primary positive label**. They exist to give later strategy
design downside context, per `docs/02_mvp/MVP_1_Specification.md` section 4.4. ATR-based stop
diagnostics are not implemented in v1.

## 7. Event Deduplication

For the same ticker, positive observations whose **actual 20-bar outcome windows** overlap are
grouped into one deduplicated economic event. The window end used for overlap detection is each
label's own `window_end_date` (the actual date of its 20th future bar), not a
`signal_date + BDay(20)` calendar approximation — the latter silently drifts from the true
window whenever a holiday or missing bar falls inside the horizon.

Implementation: `stock_analyzer/datasets/swing_20/events.py`.

## 8. Data-Quality Quarantine

A symbol is dropped from the model-eligible universe (labels + eligibility), but **never from
the frozen raw snapshot**, if its price history contains any row with:

- a non-positive Open, High, Low, or Close;
- High below Low;
- a material OHLC deviation (Open/Close outside `[Low, High]` by more than
  `max(1e-6, Close * 1e-8)` — this tolerance separates real bad prints from
  adjusted-price float-rounding noise, which is reported separately and does not quarantine).

This is a general, auditable rule (`INVALID_PRICE_SERIES`), not a per-ticker exception. On the
full-US snapshot it quarantined 4 symbols (CBIO, CNL, DEC, SPRC — all had non-positive prices,
likely from botched reverse-split adjustments), removing 573 of 2,150,698 observations (0.027%)
and dropping `ohlc_material_inconsistency_count` from 297 to 0.

Implementation: `stock_analyzer/datasets/swing_20/quality.py`
(`evaluate_symbol_price_quality`), `audit.py` (`apply_data_quality_quarantine`).

## 9. Target-Already-Reached-at-Entry Treatment

**Policy:** `TARGET_ALREADY_REACHED_AT_ENTRY`.

A row where entry Open already sits at or above the target return relative to signal-day Close
(`entry_open >= signal_close * 1.20`, equivalently `large_gap_at_entry >= target_return`)
represents a move that happened **before** the modeled entry — a live user could never have
captured it by entering at the next Open. Such rows are:

- excluded from the primary label population used for splits, event deduplication, the
  baseline, and the trainability decision;
- **not** deleted from the underlying frame, and **not** recoded as negative labels;
- retained in diagnostics (`target_already_reached_at_entry` field on every label row;
  `excluded_by_split`, `excluded_by_symbol` counts in the audit).

On the full-US snapshot this excludes 1,438 of 2,150,125 observations (0.067%), reduces raw
positives by 659, and leaves the overall positive rate essentially unchanged (11.67% → 11.65%).
Impact is spread across all three temporal splits, concentrated in high-volatility microcap/meme
tickers (HOLO, MLGO, FFAI, SMX, SUNE).

Implementation: `stock_analyzer/datasets/swing_20/labels.py` (`label_at`), `audit.py`
(`exclude_target_already_reached_at_entry`).

**Verified equivalence:** `large_gap_at_entry >= target_return` and
`target_already_reached_at_entry` are proven identical by a parametrized regression test
(`tests/test_swing20_labels.py::test_target_already_reached_at_entry_matches_large_gap_at_entry_condition`)
across boundary and non-boundary entry prices, including the exact threshold. This equivalence
is what lets frozen snapshots that predate the `target_already_reached_at_entry` column (built
before this policy existed) still be audited correctly via the older `large_gap_at_entry` field.

## 10. Temporal Splits

Method: temporal, fractional (`train_fraction=0.60`, `validation_fraction=0.20`,
`locked_test_fraction=0.20`), computed on the primary (post-quarantine, post-gap-exclusion)
label population's date range.

Full-US snapshot split ranges (as of `swing20_20260718T135238Z`):

| Split | Start | End | Observations | Raw Positives | Positive Rate |
|---|---|---|---:|---:|---:|
| train | 2022-07-14 | 2024-11-15 | 1,206,131 | 123,231 | 10.23% |
| validation | 2024-11-18 | 2025-09-03 | 448,905 | 52,227 | 11.65% |
| locked_test | 2025-09-04 | 2026-06-17 | 493,651 | 74,842 | 15.19% |

The locked-test period must remain unexamined until one final model configuration is selected
(`docs/02_mvp/MVP_1_Specification.md` section 13).

## 11. Known Point-in-Time Limitations

Carried forward as audit warnings, not hard blockers:

- `SURVIVORSHIP_BIAS_PRESENT` — the universe is resolved from current symbol availability, not
  a point-in-time historical listing.
- `UNIVERSE_MEMBERSHIP_NOT_POINT_IN_TIME` — same root cause.
- `SECTOR_NOT_POINT_IN_TIME`, `MARKET_CAP_NOT_POINT_IN_TIME` — sector/market-cap metadata, if
  used, is not stamped with a point-in-time availability date in v1.
- `OHLC_ROUNDING_TOLERANCE_ARTIFACTS_PRESENT` — sub-cent adjusted-price float noise remains in
  the raw prices (not material, does not affect labels).
- `MISSING_ENTRY_PRICE_ROWS_EXCLUDED` — rows with a missing/non-positive next-day Open are
  dropped from labels entirely (never coded as a negative), 863 such rows on the full-US
  snapshot.
- yfinance adjusted OHLCV does not carry full corporate-action provenance (no independent
  confirmation of split/dividend adjustment correctness beyond the quarantine's structural
  checks).

## 12. Frozen Trainability Decision

```text
status: CONDITIONALLY_TRAINABLE
hard_blockers: []
warnings: [DATA_QUALITY_SYMBOLS_QUARANTINED, MARKET_CAP_NOT_POINT_IN_TIME,
           MISSING_ENTRY_PRICE_ROWS_EXCLUDED, OHLC_ROUNDING_TOLERANCE_ARTIFACTS_PRESENT,
           SECTOR_NOT_POINT_IN_TIME, SURVIVORSHIP_BIAS_PRESENT,
           TARGET_ALREADY_REACHED_AT_ENTRY_EXCLUDED, UNIVERSE_MEMBERSHIP_NOT_POINT_IN_TIME]
```

Per `docs/02_mvp/MVP_1_Specification.md` section 16.4, `CONDITIONALLY_TRAINABLE` means the
target is learnable but documented limitations remain — feature and baseline work may proceed
with those limitations in view, but the locked-test period stays untouched until one final
configuration is selected.

## 13. Change Policy

This specification changes only if:

- implementation discovers a contradiction with the frozen definitions;
- a new audit run discovers a hard blocker requiring redesign;
- a verified data-quality defect requires correction.

New features, model ideas, or indicator preferences are not sufficient reasons to change this
specification.
