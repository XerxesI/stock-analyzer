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
    admission_id TEXT NOT NULL PRIMARY KEY,
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
    reservation_id TEXT NOT NULL PRIMARY KEY,
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
    snapshot_id TEXT NOT NULL PRIMARY KEY,
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
    execution_id TEXT NOT NULL PRIMARY KEY,
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

_EXPECTED_TABLES = ("portfolio_admissions", "slot_reservations", "portfolio_equity_snapshots", "executions")

# Column name -> (declared SQLite storage type, NOT NULL). Mirrors DECISION_AUDIT_DDL
# exactly, so a physically-drifted table (wrong type, dropped NOT NULL, extra/missing
# column) is caught even though its recorded version label still claims v1.
_EXPECTED_COLUMNS: dict[str, dict[str, tuple[str, bool]]] = {
    "portfolio_admissions": {
        "admission_id": ("TEXT", True),
        "replay_id": ("TEXT", True),
        "candidate_id": ("TEXT", True),
        "symbol": ("TEXT", True),
        "as_of_date": ("TEXT", True),
        "decision": ("TEXT", True),
        "rank_at_admission": ("INTEGER", True),
        "slot_budget_units": ("INTEGER", False),
        "reason": ("TEXT", False),
        "created_at": ("TEXT", True),
    },
    "slot_reservations": {
        "reservation_id": ("TEXT", True),
        "replay_id": ("TEXT", True),
        "admission_id": ("TEXT", True),
        "candidate_id": ("TEXT", True),
        "symbol": ("TEXT", True),
        "reserved_amount_units": ("INTEGER", True),
        "status": ("TEXT", True),
        "created_at": ("TEXT", True),
        "resolved_at": ("TEXT", False),
    },
    "portfolio_equity_snapshots": {
        "snapshot_id": ("TEXT", True),
        "replay_id": ("TEXT", True),
        "as_of_date": ("TEXT", True),
        "cash_units": ("INTEGER", True),
        "reserved_capital_units": ("INTEGER", True),
        "open_position_market_value_units": ("INTEGER", True),
        "total_equity_units": ("INTEGER", True),
        "open_position_count": ("INTEGER", True),
        "reserved_order_count": ("INTEGER", True),
        "cumulative_commissions_units": ("INTEGER", True),
        "cumulative_slippage_cost_units": ("INTEGER", True),
        "created_at": ("TEXT", True),
    },
    "executions": {
        "execution_id": ("TEXT", True),
        "replay_id": ("TEXT", True),
        "variant_id": ("TEXT", True),
        "control_seed": ("INTEGER", False),
        "order_id": ("TEXT", False),
        "candidate_id": ("TEXT", True),
        "position_id": ("TEXT", False),
        "symbol": ("TEXT", True),
        "side": ("TEXT", True),
        "decision_date": ("TEXT", True),
        "execution_date": ("TEXT", True),
        "raw_market_fill_price_units": ("INTEGER", True),
        "effective_fill_price_units": ("INTEGER", True),
        "quantity_units": ("INTEGER", True),
        "gross_notional_units": ("INTEGER", True),
        "commission_units": ("INTEGER", True),
        "slippage_rate_units": ("INTEGER", True),
        "slippage_cost_units": ("INTEGER", True),
        "net_cash_flow_units": ("INTEGER", True),
        "fill_reason": ("TEXT", True),
        "market_data_snapshot_id": ("TEXT", True),
        "created_at": ("TEXT", True),
    },
}

_EXPECTED_PRIMARY_KEYS: dict[str, str] = {
    "portfolio_admissions": "admission_id",
    "slot_reservations": "reservation_id",
    "portfolio_equity_snapshots": "snapshot_id",
    "executions": "execution_id",
}

# Reverse-reference columns that would reintroduce the FK cycle Section 8.1's design
# deliberately avoids (the reservation/order are looked up via slot_reservations.
# admission_id / entry_orders.candidate_id instead) -- their presence on
# portfolio_admissions is a physical-schema defect, not a compatible superset.
_FORBIDDEN_COLUMNS: dict[str, set[str]] = {
    "portfolio_admissions": {"reservation_id", "order_id"},
}

_EXPECTED_UNIQUE_COLUMN_SETS: dict[str, list[tuple[str, ...]]] = {
    "slot_reservations": [("admission_id",)],
    "portfolio_equity_snapshots": [("replay_id", "as_of_date")],
}

_EXPECTED_INDEXES: dict[str, set[str]] = {
    "portfolio_admissions": {"idx_portfolio_admissions_as_of_date", "idx_portfolio_admissions_replay_id"},
    "slot_reservations": {"idx_slot_reservations_status", "idx_slot_reservations_replay_id"},
    "portfolio_equity_snapshots": {"idx_portfolio_equity_snapshots_as_of_date"},
    "executions": {
        "idx_executions_order_id",
        "idx_executions_position_id",
        "idx_executions_candidate_id",
        "idx_executions_replay_id",
    },
}

# table -> {expected outbound FK column: expected referenced table}. Confirms
# one-directional-only references (the child points at portfolio_admissions/
# entry_orders/ranked_candidates/virtual_positions; none of those point back).
_EXPECTED_OUTBOUND_FKS: dict[str, dict[str, str]] = {
    "portfolio_admissions": {"candidate_id": "ranked_candidates"},
    "slot_reservations": {"admission_id": "portfolio_admissions"},
    "portfolio_equity_snapshots": {},
    "executions": {"order_id": "entry_orders", "candidate_id": "ranked_candidates", "position_id": "virtual_positions"},
}

_TABLES_REQUIRING_CHECK_CONSTRAINT = (
    "portfolio_admissions",
    "slot_reservations",
    "portfolio_equity_snapshots",
    "executions",
)


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


def _has_unique_constraint(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> bool:
    """True iff `table` has a UNIQUE index (explicit or the implicit one SQLite
    creates for a column-level UNIQUE/PRIMARY KEY constraint) covering exactly
    `columns`, in any order of declaration but as an exact set match."""

    for idx in conn.execute(f"PRAGMA index_list({table})").fetchall():
        if not idx["unique"]:
            continue
        idx_columns = {row["name"] for row in conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()}
        if idx_columns == set(columns):
            return True
    return False


def _verify_v1_physical_invariants(conn: sqlite3.Connection) -> None:
    """Physical verification, not just trusting a label: a database recorded as
    decision_audit_schema_version=1 must ACTUALLY have the v1 physical shape --
    every table's columns/types/NOT NULL/primary key, one-directional foreign keys,
    the two uniqueness constraints Section 8.1/8.5 depend on for correctness
    (slot_reservations.admission_id, portfolio_equity_snapshots(replay_id,
    as_of_date)), the required indexes, absence of the forbidden reverse-reference
    columns on portfolio_admissions, a CHECK constraint on every table, and
    referential integrity -- so a database mislabeled or drifted by some future bug
    is caught here, the same way the core sandbox schema's own physical-inspection
    fix (schema.py's mislabeled-v2 detection) works. This function only ever
    verifies and raises; it never attempts to repair an unrecognized physical shape.
    """

    existing_tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    missing_tables = set(_EXPECTED_TABLES) - existing_tables
    if missing_tables:
        raise DecisionAuditSchemaIntegrityError(f"missing expected decision-audit tables: {sorted(missing_tables)}")

    for table, expected_columns in _EXPECTED_COLUMNS.items():
        info_rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        actual_by_name = {row["name"]: row for row in info_rows}
        actual_column_set = set(actual_by_name)
        expected_column_set = set(expected_columns)

        missing_columns = expected_column_set - actual_column_set
        if missing_columns:
            raise DecisionAuditSchemaIntegrityError(f"{table} is missing expected columns: {sorted(missing_columns)}")

        # Strict equality, not just "no columns missing": this is a frozen pilot's
        # physical schema, not a compatible superset -- an unrecognized extra
        # column (whether a forbidden reverse-reference or anything else) means the
        # database does not actually have the exact v1 shape its label claims.
        extra_columns = actual_column_set - expected_column_set
        if extra_columns:
            forbidden_present = _FORBIDDEN_COLUMNS.get(table, set()) & extra_columns
            if forbidden_present:
                raise DecisionAuditSchemaIntegrityError(
                    f"{table} has forbidden reverse-reference column(s) {sorted(forbidden_present)} -- "
                    "this would reintroduce the foreign-key cycle Section 8.1's design deliberately avoids."
                )
            raise DecisionAuditSchemaIntegrityError(
                f"{table} has unexpected column(s) not part of the frozen v1 physical shape: "
                f"{sorted(extra_columns)}"
            )

        for column, (expected_type, expected_not_null) in expected_columns.items():
            row = actual_by_name[column]
            actual_type = (row["type"] or "").upper()
            if actual_type != expected_type:
                raise DecisionAuditSchemaIntegrityError(
                    f"{table}.{column} has declared type {actual_type!r}, expected {expected_type!r} "
                    "-- money/price/quantity/rate fields must be INTEGER, never REAL, per the "
                    "corrective-cycle exact-numerics fix."
                )
            if bool(row["notnull"]) != expected_not_null:
                raise DecisionAuditSchemaIntegrityError(
                    f"{table}.{column} has NOT NULL={bool(row['notnull'])}, expected {expected_not_null}"
                )

        actual_pk_columns = [row["name"] for row in info_rows if row["pk"] == 1]
        expected_pk = _EXPECTED_PRIMARY_KEYS[table]
        if actual_pk_columns != [expected_pk]:
            raise DecisionAuditSchemaIntegrityError(
                f"{table} has primary key columns {actual_pk_columns}, expected [{expected_pk!r}]"
            )

        actual_indexes = {row["name"] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}
        missing_indexes = _EXPECTED_INDEXES.get(table, set()) - actual_indexes
        if missing_indexes:
            raise DecisionAuditSchemaIntegrityError(f"{table} is missing expected index(es): {sorted(missing_indexes)}")

        for unique_columns in _EXPECTED_UNIQUE_COLUMN_SETS.get(table, []):
            if not _has_unique_constraint(conn, table, unique_columns):
                raise DecisionAuditSchemaIntegrityError(
                    f"{table} is missing a UNIQUE constraint on {unique_columns}"
                )

        actual_fks = {row["from"]: row["table"] for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()}
        expected_fks = _EXPECTED_OUTBOUND_FKS.get(table, {})
        if actual_fks != expected_fks:
            raise DecisionAuditSchemaIntegrityError(
                f"{table} has outbound foreign keys {actual_fks}, expected exactly {expected_fks} -- an "
                "unexpected or missing/reversed foreign key breaks the one-directional reference design."
            )

    for table in _TABLES_REQUIRING_CHECK_CONSTRAINT:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone()
        # Best-effort structural check only: SQLite exposes no PRAGMA for CHECK
        # constraints, so this inspects the table's own recorded DDL text for the
        # CHECK keyword rather than verifying the constraint's semantics.
        if row is None or "CHECK" not in row["sql"].upper():
            raise DecisionAuditSchemaIntegrityError(
                f"{table}'s recorded definition does not appear to declare any CHECK constraint"
            )

    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_violations:
        raise DecisionAuditSchemaIntegrityError(f"foreign key integrity check failed: {list(fk_violations)}")


def init_exp005_schema(conn: sqlite3.Connection) -> None:
    """Creates the four EXP-005 tables if they do not already exist (idempotent),
    then verifies AND records the physical decision-audit schema identity -- never
    relies only on a bare Python constant nothing in the database actually checks.

    Version is read and validated BEFORE any DDL runs (schema-init review,
    corrective cycle): SQLite DDL statements executed via `executescript()`
    auto-commit as they run and cannot be rolled back after the fact, so a database
    recorded at an unsupported future version must be rejected with the connection
    touched by nothing -- not even an idempotent `CREATE INDEX IF NOT EXISTS` that
    happens to be missing from that database's actual (newer, unknown-to-us) shape.
    Only once the version is confirmed supported does the DDL execute, followed by
    physical verification and (for a genuinely fresh database) recording the
    version.

    Must be called AFTER stock_analyzer.sandbox.infrastructure.schema.init_db (or
    connect()), which creates the core tables these four reference. PRAGMA
    foreign_keys must already be ON on `conn` (the caller's responsibility, matching
    the core schema module's own convention).
    """

    current_version = _get_decision_audit_schema_version(conn)
    if current_version is not None and current_version > DECISION_AUDIT_SCHEMA_VERSION:
        raise UnsupportedDecisionAuditSchemaVersionError(
            f"database decision_audit_schema_version={current_version} is newer than this code "
            f"understands (DECISION_AUDIT_SCHEMA_VERSION={DECISION_AUDIT_SCHEMA_VERSION}) -- refused "
            "before executing any decision-audit DDL, so this database is left byte-for-byte unchanged."
        )

    conn.executescript(DECISION_AUDIT_DDL)

    _verify_v1_physical_invariants(conn)

    if current_version is None:
        _set_decision_audit_schema_version(conn, DECISION_AUDIT_SCHEMA_VERSION)

    conn.commit()
