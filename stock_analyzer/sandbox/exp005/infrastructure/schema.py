"""SQLite DDL for EXP-005's four wholly new tables (Revision 5, Sections 8.1, 8.5,
18). Every monetary/price/quantity/rate column is INTEGER, in the fixed-point units
defined by domain/units.py -- never REAL, never a float. This is the corrective-cycle
fix for the earlier float+Decimal+REAL+tolerance design, which could not actually
guarantee exact reconciliation or exact idempotency comparison.

None of these modify any EXISTING core-sandbox table's columns or constraints, so
core schema.py's own SCHEMA_VERSION is untouched (stays at 3). These four tables get
their own, SEPARATE, physically-verified version identity -- see
`decision_audit_schema_version` below -- recorded in the SAME `schema_meta` table the
core schema already uses (reusing that existing mechanism honestly, not bypassing it
with a bare Python constant nothing in the database actually checks).

Reuses the core project's ID/timestamp conventions: TEXT primary keys with
deterministic, application-derived IDs; TEXT ISO-8601 timestamps. `slot_reservations`
is a MUTABLE current-state table (its `status` column transitions on the same row,
the same category as the core schema's `entry_orders`/`virtual_positions`) -- the
other three tables (`portfolio_admissions`, `portfolio_equity_snapshots`,
`executions`) are genuinely append-only, matching the core schema's
`position_snapshots`/`recommendations`/`virtual_transactions` category. Append-only
is enforced at the repository layer (Stage 3), not via SQL triggers, exactly like the
rest of this project.
"""

from __future__ import annotations

import sqlite3

DECISION_AUDIT_SCHEMA_VERSION = 1

DECISION_AUDIT_DDL = """
-- Section 8.1: the sole parent of an admission decision. Deliberately has NO
-- reservation_id/order_id column -- see Section 8.1's foreign-key-cycle fix. The
-- reservation is found via slot_reservations.admission_id; the order via
-- entry_orders.candidate_id (== admission_id, the existing, already-unique FK).
-- Append-only: one row per admission_id, ever (see application/
-- admission_orchestrator.py -- there is exactly one production write path).
CREATE TABLE IF NOT EXISTS portfolio_admissions (
    admission_id TEXT PRIMARY KEY,
    replay_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL REFERENCES ranked_candidates(candidate_id),
    symbol TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('ACCEPTED','NO_CAPACITY')),
    rank_at_admission INTEGER NOT NULL,
    slot_budget_units INTEGER,
    reason TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (decision = 'ACCEPTED' AND slot_budget_units IS NOT NULL)
        OR (decision = 'NO_CAPACITY' AND slot_budget_units IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_portfolio_admissions_as_of_date ON portfolio_admissions(as_of_date);
CREATE INDEX IF NOT EXISTS idx_portfolio_admissions_replay_id ON portfolio_admissions(replay_id);

-- Section 8.1/8.2/8.3: the child. References its parent one-directionally only.
-- admission_id is UNIQUE, jointly enforcing "an admission owns at most one
-- reservation" and (since admission_id == candidate_id) "a candidate reserves at
-- most one slot, ever." NOT append-only -- status transitions RESERVED ->
-- CONVERTED/RELEASED on the same row (Section 8.3); see
-- infrastructure/repository.py's update_reservation_status for the one, narrow,
-- conflict-safe transition path.
CREATE TABLE IF NOT EXISTS slot_reservations (
    reservation_id TEXT PRIMARY KEY,
    replay_id TEXT NOT NULL,
    admission_id TEXT NOT NULL UNIQUE REFERENCES portfolio_admissions(admission_id),
    candidate_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    reserved_amount_units INTEGER NOT NULL CHECK (reserved_amount_units > 0),
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

-- Section 8.5: exactly one row per processed trading day. Append-only:
-- UNIQUE(replay_id, as_of_date) makes a second snapshot for the same day a
-- constraint violation, not a silent duplicate.
CREATE TABLE IF NOT EXISTS portfolio_equity_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    replay_id TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    cash_units INTEGER NOT NULL,
    reserved_capital_units INTEGER NOT NULL CHECK (reserved_capital_units >= 0),
    open_position_market_value_units INTEGER NOT NULL,
    total_equity_units INTEGER NOT NULL,
    open_position_count INTEGER NOT NULL CHECK (open_position_count >= 0),
    reserved_order_count INTEGER NOT NULL CHECK (reserved_order_count >= 0),
    cumulative_commissions_units INTEGER NOT NULL CHECK (cumulative_commissions_units >= 0),
    cumulative_slippage_cost_units INTEGER NOT NULL CHECK (cumulative_slippage_cost_units >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (replay_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_equity_snapshots_as_of_date ON portfolio_equity_snapshots(as_of_date);

-- Section 18: one immutable row per fill (BUY or SELL) -- append-only.
-- raw_market_fill_price_units and effective_fill_price_units are both mandatory and
-- neither is ever overwritten by the other. quantity/gross_notional/commission/
-- slippage_cost are non-negative MAGNITUDES; net_cash_flow_units carries the sign
-- (negative for BUY, positive for SELL) -- Stage 5's sign convention, enforced here
-- at the schema level too, not only in application code.
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
    raw_market_fill_price_units INTEGER NOT NULL CHECK (raw_market_fill_price_units > 0),
    effective_fill_price_units INTEGER NOT NULL CHECK (effective_fill_price_units > 0),
    quantity_units INTEGER NOT NULL CHECK (quantity_units > 0),
    gross_notional_units INTEGER NOT NULL CHECK (gross_notional_units > 0),
    commission_units INTEGER NOT NULL CHECK (commission_units >= 0),
    slippage_rate_units INTEGER NOT NULL CHECK (slippage_rate_units >= 0),
    slippage_cost_units INTEGER NOT NULL CHECK (slippage_cost_units >= 0),
    net_cash_flow_units INTEGER NOT NULL,
    fill_reason TEXT NOT NULL,
    market_data_snapshot_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (
        (side = 'BUY' AND net_cash_flow_units < 0)
        OR (side = 'SELL' AND net_cash_flow_units > 0)
    ),
    CHECK ((side = 'BUY' AND order_id IS NOT NULL) OR side = 'SELL')
);
CREATE INDEX IF NOT EXISTS idx_executions_order_id ON executions(order_id);
CREATE INDEX IF NOT EXISTS idx_executions_position_id ON executions(position_id);
CREATE INDEX IF NOT EXISTS idx_executions_candidate_id ON executions(candidate_id);
CREATE INDEX IF NOT EXISTS idx_executions_replay_id ON executions(replay_id);
"""

_EXPECTED_EXECUTION_UNIT_COLUMNS = (
    "raw_market_fill_price_units",
    "effective_fill_price_units",
    "quantity_units",
    "gross_notional_units",
    "commission_units",
    "slippage_rate_units",
    "slippage_cost_units",
    "net_cash_flow_units",
)
_EXPECTED_TABLES = ("portfolio_admissions", "slot_reservations", "portfolio_equity_snapshots", "executions")


class DecisionAuditSchemaIntegrityError(RuntimeError):
    """Raised when the database's physical decision-audit schema does not match
    what its recorded version claims -- never trust the label alone (the exact
    lesson learned from the core sandbox schema's own v1/v2/v3 mislabeling saga)."""


class UnsupportedDecisionAuditSchemaVersionError(RuntimeError):
    """Raised when the database's recorded decision_audit_schema_version is higher
    than this code's DECISION_AUDIT_SCHEMA_VERSION -- written by newer code than is
    running now. Refused outright rather than silently operated on."""


def _get_decision_audit_schema_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'decision_audit_schema_version'").fetchone()
    return int(row[0]) if row is not None else None


def _set_decision_audit_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('decision_audit_schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def _verify_v1_physical_invariants(conn: sqlite3.Connection) -> None:
    """Physical verification, not just trusting a label: confirms the four tables
    actually exist, and that `executions` actually has the corrected integer-unit
    columns (not the pre-corrective-cycle float columns) -- so a database mislabeled
    by some future bug is caught here, the same way the core sandbox schema's own
    physical-inspection fix (schema.py's mislabeled-v2 detection) works."""

    existing_tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    missing_tables = set(_EXPECTED_TABLES) - existing_tables
    if missing_tables:
        raise DecisionAuditSchemaIntegrityError(f"missing expected decision-audit tables: {sorted(missing_tables)}")

    execution_columns = {row["name"] for row in conn.execute("PRAGMA table_info(executions)").fetchall()}
    missing_columns = set(_EXPECTED_EXECUTION_UNIT_COLUMNS) - execution_columns
    if missing_columns:
        raise DecisionAuditSchemaIntegrityError(
            f"executions table is missing expected integer-unit columns: {sorted(missing_columns)} "
            "-- this looks like a pre-corrective-cycle (float-based) physical schema."
        )


def init_exp005_schema(conn: sqlite3.Connection) -> None:
    """Creates the four EXP-005 tables if they do not already exist (idempotent),
    then verifies AND records the physical decision-audit schema identity -- never
    relies only on a bare Python constant nothing in the database actually checks.

    Must be called AFTER stock_analyzer.sandbox.infrastructure.schema.init_db (or
    connect()), which creates the core tables these four reference. PRAGMA
    foreign_keys must already be ON on `conn` (the caller's responsibility, matching
    the core schema module's own convention).
    """

    conn.executescript(DECISION_AUDIT_DDL)

    current_version = _get_decision_audit_schema_version(conn)
    if current_version is not None and current_version > DECISION_AUDIT_SCHEMA_VERSION:
        raise UnsupportedDecisionAuditSchemaVersionError(
            f"database decision_audit_schema_version={current_version} is newer than this code "
            f"understands (DECISION_AUDIT_SCHEMA_VERSION={DECISION_AUDIT_SCHEMA_VERSION})."
        )

    _verify_v1_physical_invariants(conn)

    if current_version is None:
        _set_decision_audit_schema_version(conn, DECISION_AUDIT_SCHEMA_VERSION)

    conn.commit()
