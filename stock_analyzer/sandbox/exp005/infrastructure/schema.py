"""SQLite DDL for EXP-005's four wholly new, additive tables (Revision 5, Sections
8.1, 8.5, 18): portfolio_admissions, slot_reservations, portfolio_equity_snapshots,
executions.

None of these modify any existing sandbox table's columns or constraints, so -- per
Section 28's explicit reasoning -- no schema version bump or migration path is needed
in the CORE sandbox schema (infrastructure/schema.py's own SCHEMA_VERSION stays at 3;
CREATE TABLE IF NOT EXISTS creates these identically on a fresh database or an
existing v3 one). They get their own, separate DECISION_AUDIT_SCHEMA_VERSION marker
(exp005.config.DECISION_AUDIT_SCHEMA_VERSION) recorded in the Experiment Manifest --
not because a migration mechanism is needed today, but so a later reviewer can tell
which shape of these four tables a given run's database has, using the project's
normal schema-identification convention rather than bypassing it.

Reuses the core project's conventions throughout: TEXT primary keys with
deterministic, application-derived IDs; TEXT ISO-8601 timestamps; REAL for
money/quantity columns, matching every existing sandbox table (entry_orders,
virtual_positions, virtual_transactions all already use REAL, not a SQLite NUMERIC
affinity or a string-encoded decimal column -- SQLite has no native arbitrary-
precision decimal type, so declaring NUMERIC would not itself buy exactness; the
exactness guarantee instead comes from the application-level Decimal-based
computation and a documented rounding boundary, implemented in the repository layer,
Stage 5). Append-only is enforced at the repository layer (Stage 3), not via SQL
triggers -- the same convention the core schema already uses for
position_snapshots/recommendations/virtual_transactions/data_quality_events (no
UPDATE method exists for those tables in SandboxRepository).
"""

from __future__ import annotations

import sqlite3

DECISION_AUDIT_DDL = """
-- Section 8.1: the sole parent of an admission decision. Deliberately has NO
-- reservation_id/order_id column -- see Section 8.1's foreign-key-cycle fix. The
-- reservation is found via slot_reservations.admission_id; the order via
-- entry_orders.candidate_id (== admission_id, the existing, already-unique FK).
CREATE TABLE IF NOT EXISTS portfolio_admissions (
    admission_id TEXT PRIMARY KEY,
    replay_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL REFERENCES ranked_candidates(candidate_id),
    symbol TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('ACCEPTED','NO_CAPACITY')),
    rank_at_admission INTEGER NOT NULL,
    slot_budget REAL,
    reason TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (decision = 'ACCEPTED' AND slot_budget IS NOT NULL)
        OR (decision = 'NO_CAPACITY' AND slot_budget IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_portfolio_admissions_as_of_date ON portfolio_admissions(as_of_date);
CREATE INDEX IF NOT EXISTS idx_portfolio_admissions_replay_id ON portfolio_admissions(replay_id);

-- Section 8.1/8.2: the child. References its parent one-directionally only.
-- admission_id is UNIQUE, jointly enforcing "an admission owns at most one
-- reservation" and (since admission_id == candidate_id) "a candidate reserves at
-- most one slot, ever."
CREATE TABLE IF NOT EXISTS slot_reservations (
    reservation_id TEXT PRIMARY KEY,
    replay_id TEXT NOT NULL,
    admission_id TEXT NOT NULL UNIQUE REFERENCES portfolio_admissions(admission_id),
    candidate_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    reserved_amount REAL NOT NULL CHECK (reserved_amount > 0),
    status TEXT NOT NULL CHECK (status IN ('RESERVED','CONVERTED','RELEASED')),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    CHECK (
        (status = 'RESERVED' AND resolved_at IS NULL)
        OR (status IN ('CONVERTED','RELEASED') AND resolved_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_slot_reservations_status ON slot_reservations(status);
CREATE INDEX IF NOT EXISTS idx_slot_reservations_replay_id ON slot_reservations(replay_id);

-- Section 8.5: exactly one row per processed trading day, taken after that day's
-- full entry/monitoring/candidate/admission sequence. UNIQUE(replay_id, as_of_date)
-- makes a second snapshot for the same day a constraint violation, not a silent
-- duplicate.
CREATE TABLE IF NOT EXISTS portfolio_equity_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    replay_id TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    cash REAL NOT NULL,
    reserved_capital REAL NOT NULL CHECK (reserved_capital >= 0),
    open_position_market_value REAL NOT NULL,
    total_equity REAL NOT NULL,
    open_position_count INTEGER NOT NULL CHECK (open_position_count >= 0),
    reserved_order_count INTEGER NOT NULL CHECK (reserved_order_count >= 0),
    cumulative_commissions REAL NOT NULL CHECK (cumulative_commissions >= 0),
    cumulative_slippage_cost REAL NOT NULL CHECK (cumulative_slippage_cost >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (replay_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_equity_snapshots_as_of_date ON portfolio_equity_snapshots(as_of_date);

-- Section 18: one immutable row per fill (BUY or SELL). raw_market_fill_price and
-- effective_fill_price are both mandatory and neither is ever overwritten by the
-- other. quantity/gross_notional/commission/slippage_cost are stored as
-- non-negative MAGNITUDES; net_cash_flow carries the sign (negative for BUY,
-- positive for SELL) -- Stage 5's sign convention, enforced here at the schema
-- level too, not only in application code.
CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    replay_id TEXT NOT NULL,
    variant_id TEXT NOT NULL CHECK (variant_id IN ('B','D')),
    control_seed INTEGER,
    order_id TEXT REFERENCES entry_orders(order_id),
    candidate_id TEXT NOT NULL REFERENCES ranked_candidates(candidate_id),
    position_id TEXT REFERENCES virtual_positions(position_id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    decision_date TEXT NOT NULL,
    execution_date TEXT NOT NULL,
    raw_market_fill_price REAL NOT NULL CHECK (raw_market_fill_price > 0),
    effective_fill_price REAL NOT NULL CHECK (effective_fill_price > 0),
    quantity REAL NOT NULL CHECK (quantity > 0),
    gross_notional REAL NOT NULL CHECK (gross_notional > 0),
    commission REAL NOT NULL CHECK (commission >= 0),
    slippage_rate REAL NOT NULL CHECK (slippage_rate >= 0),
    slippage_cost REAL NOT NULL CHECK (slippage_cost >= 0),
    net_cash_flow REAL NOT NULL,
    fill_reason TEXT NOT NULL,
    market_data_snapshot_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (
        (side = 'BUY' AND net_cash_flow < 0)
        OR (side = 'SELL' AND net_cash_flow > 0)
    ),
    CHECK ((side = 'BUY' AND order_id IS NOT NULL) OR side = 'SELL')
);
CREATE INDEX IF NOT EXISTS idx_executions_order_id ON executions(order_id);
CREATE INDEX IF NOT EXISTS idx_executions_position_id ON executions(position_id);
CREATE INDEX IF NOT EXISTS idx_executions_candidate_id ON executions(candidate_id);
CREATE INDEX IF NOT EXISTS idx_executions_replay_id ON executions(replay_id);
"""


def init_exp005_schema(conn: sqlite3.Connection) -> None:
    """Creates the four EXP-005 tables if they do not already exist. Idempotent --
    safe to call on every invocation, exactly like the core
    stock_analyzer.sandbox.infrastructure.schema.init_db it complements. Must be
    called AFTER that function (or connect()), which creates the core tables
    (ranked_candidates, entry_orders, virtual_positions, ...) these four reference.
    PRAGMA foreign_keys must already be ON on `conn` (the caller's responsibility,
    matching the core schema module's own convention)."""

    conn.executescript(DECISION_AUDIT_DDL)
    conn.commit()
