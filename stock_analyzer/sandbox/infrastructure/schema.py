"""SQLite DDL for the Recommendation Sandbox. See
docs/04_decisions/ADR-006-Sandbox-Persistence-and-Audit-Trail.md.

Two categories of table:
  - mutable current-state: sandbox_runs, entry_orders, virtual_positions
    (entry_orders/virtual_positions only mutate their own lifecycle fields; entry-time
    facts are set once and never changed).
  - append-only: ranked_candidates, entry_order_attempts, position_snapshots,
    recommendations, virtual_transactions, data_quality_events.

Schema versioning: `init_db` compares the CURRENT physical schema_version (read from
schema_meta, not assumed) against SCHEMA_VERSION and runs an explicit, transactional
migration for any gap -- see _migrate_v1_to_v2. A brand-new database is created
directly at the latest schema by the DDL below and simply has its version recorded;
schema_meta is never blindly overwritten to SCHEMA_VERSION without either creating a
fresh database or completing a real migration.
"""

from __future__ import annotations

import sqlite3

# v2 changes (see _migrate_v1_to_v2 for the physical migration of an existing v1 db):
#   - ranked_candidates.signal_close is now nullable (a MISSING_MARKET_DATA candidate
#     has no signal close by definition; the v1 NOT NULL constraint was silently
#     swallowing that row via INSERT OR IGNORE -- see candidate_service.py).
#   - replay_metadata.last_completed_date (new column) -- a resume watermark so
#     ReplayService only ever reprocesses the one date that may have been partially
#     done when a process died, never already-completed history (see
#     application/replay_service.py).
SCHEMA_VERSION = 2

_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sandbox_runs (
    run_id TEXT PRIMARY KEY,
    as_of_date TEXT NOT NULL,
    command TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
    model_version TEXT,
    data_snapshot_id TEXT,
    code_commit_sha TEXT,
    configuration_hash TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS ranked_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES sandbox_runs(run_id),
    as_of_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    daily_rank INTEGER NOT NULL,
    model_score REAL NOT NULL,
    signal_close REAL,
    atr14 REAL,
    max_entry_price REAL,
    shadow_top10 INTEGER NOT NULL CHECK (shadow_top10 IN (0,1)),
    actionable INTEGER NOT NULL CHECK (actionable IN (0,1)),
    exclusion_reason TEXT,
    adv_quintile TEXT,
    market_regime TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(symbol, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_ranked_candidates_as_of_date ON ranked_candidates(as_of_date);

CREATE TABLE IF NOT EXISTS entry_orders (
    order_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES ranked_candidates(candidate_id),
    symbol TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    created_date TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    max_entry_price REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','FILLED','EXPIRED','SKIPPED')),
    fill_date TEXT,
    fill_price REAL,
    fill_reason TEXT,
    no_fill_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entry_orders_status ON entry_orders(status);

CREATE TABLE IF NOT EXISTS entry_order_attempts (
    attempt_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES entry_orders(order_id),
    symbol TEXT NOT NULL,
    attempt_date TEXT NOT NULL,
    session_open REAL,
    session_high REAL,
    session_low REAL,
    session_close REAL,
    max_entry_price REAL NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('FILLED_AT_OPEN','FILLED_AT_CEILING','NO_FILL')),
    fill_price REAL,
    reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(order_id, attempt_date)
);

CREATE TABLE IF NOT EXISTS virtual_positions (
    position_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    candidate_id TEXT NOT NULL REFERENCES ranked_candidates(candidate_id),
    order_id TEXT NOT NULL REFERENCES entry_orders(order_id),
    signal_date TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity REAL NOT NULL,
    initial_rank INTEGER NOT NULL,
    initial_model_score REAL NOT NULL,
    signal_close REAL NOT NULL,
    max_entry_price REAL NOT NULL,
    initial_adv_quintile TEXT,
    initial_market_regime TEXT,
    status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED')),
    current_holding_day_count INTEGER NOT NULL DEFAULT 0,
    current_close REAL,
    unrealized_return REAL,
    mfe REAL NOT NULL DEFAULT 0,
    mae REAL NOT NULL DEFAULT 0,
    target_price REAL NOT NULL,
    planned_time_exit_date TEXT NOT NULL,
    exit_date TEXT,
    exit_price REAL,
    exit_reason TEXT,
    realized_return REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(symbol, entry_date)
);
CREATE INDEX IF NOT EXISTS idx_virtual_positions_status ON virtual_positions(status);

CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL REFERENCES virtual_positions(position_id),
    symbol TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    close_price REAL,
    daily_return REAL,
    cumulative_unrealized_return REAL,
    holding_day_count INTEGER NOT NULL,
    mfe REAL NOT NULL,
    mae REAL NOT NULL,
    distance_to_target REAL,
    current_rank INTEGER,
    current_model_score REAL,
    rank_change_from_entry INTEGER,
    current_adv_quintile TEXT,
    current_market_regime TEXT,
    data_quality_status TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(position_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('candidate','position')),
    entity_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(entity_type, entity_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_recommendations_entity ON recommendations(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS virtual_transactions (
    transaction_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL REFERENCES virtual_positions(position_id),
    symbol TEXT NOT NULL,
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('BUY','SELL')),
    transaction_date TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    notional REAL NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(position_id, transaction_type, transaction_date)
);

CREATE TABLE IF NOT EXISTS data_quality_events (
    event_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(symbol, as_of_date, event_type)
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per Historical Sandbox Replay run (see application/replay_service.py and
-- docs/09_experiments/EXP-004_Sandbox_Historical_Replay.md). Each replay uses its own
-- isolated database file, so in practice this table has at most one row per DB, but it
-- is still a real table (not just a filename) so the metadata travels with the data
-- and a rerun of the same replay_id can be detected and rejected.
CREATE TABLE IF NOT EXISTS replay_metadata (
    replay_id TEXT PRIMARY KEY,
    classification TEXT NOT NULL,
    code_commit_sha TEXT,
    model_version TEXT,
    feature_snapshot_id TEXT,
    market_data_snapshot_id TEXT,
    signal_start_date TEXT NOT NULL,
    signal_end_date TEXT NOT NULL,
    outcome_data_end_date TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    last_completed_date TEXT
);
"""


def _get_schema_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row is not None else None


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


_RANKED_CANDIDATES_V2_COLUMNS = (
    "candidate_id",
    "run_id",
    "as_of_date",
    "symbol",
    "daily_rank",
    "model_score",
    "signal_close",
    "atr14",
    "max_entry_price",
    "shadow_top10",
    "actionable",
    "exclusion_reason",
    "adv_quintile",
    "market_regime",
    "created_at",
)


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Physically migrates an existing v1 database to v2:
      - ranked_candidates.signal_close NOT NULL -> nullable. SQLite cannot ALTER a
        column's NOT NULL constraint in place, so this rebuilds the table (SQLite's
        documented pattern for this kind of change): create a new table with the
        corrected schema, copy every existing row across unchanged, drop the old
        table, rename the new one into place, then verify referential integrity.
      - replay_metadata gains last_completed_date (ADD COLUMN is a normal, safe
        SQLite operation -- no rebuild needed for a new nullable column).

    The whole migration runs inside one explicit transaction (BEGIN is issued before
    any DDL, since relying on sqlite3's implicit-transaction behavior does not cover
    DDL statements): any failure -- including a failed foreign_key_check -- rolls
    back completely, and schema_meta.schema_version is only written to '2' after
    every step, including the integrity check, has succeeded. A database that fails
    migration is left exactly as it was: physically v1, still labelled v1.
    """

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")

        conn.execute(
            "CREATE TABLE ranked_candidates__v2migrate ("
            " candidate_id TEXT PRIMARY KEY,"
            " run_id TEXT NOT NULL REFERENCES sandbox_runs(run_id),"
            " as_of_date TEXT NOT NULL,"
            " symbol TEXT NOT NULL,"
            " daily_rank INTEGER NOT NULL,"
            " model_score REAL NOT NULL,"
            " signal_close REAL,"
            " atr14 REAL,"
            " max_entry_price REAL,"
            " shadow_top10 INTEGER NOT NULL CHECK (shadow_top10 IN (0,1)),"
            " actionable INTEGER NOT NULL CHECK (actionable IN (0,1)),"
            " exclusion_reason TEXT,"
            " adv_quintile TEXT,"
            " market_regime TEXT,"
            " created_at TEXT NOT NULL,"
            " UNIQUE(symbol, as_of_date)"
            ")"
        )
        columns = ", ".join(_RANKED_CANDIDATES_V2_COLUMNS)
        conn.execute(
            f"INSERT INTO ranked_candidates__v2migrate ({columns}) "
            f"SELECT {columns} FROM ranked_candidates"
        )
        before_count = conn.execute("SELECT COUNT(*) FROM ranked_candidates").fetchone()[0]
        after_count = conn.execute("SELECT COUNT(*) FROM ranked_candidates__v2migrate").fetchone()[0]
        if before_count != after_count:
            raise RuntimeError(
                f"v1->v2 migration row count mismatch for ranked_candidates: "
                f"before={before_count}, after={after_count}."
            )

        conn.execute("DROP TABLE ranked_candidates")
        conn.execute("ALTER TABLE ranked_candidates__v2migrate RENAME TO ranked_candidates")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ranked_candidates_as_of_date ON ranked_candidates(as_of_date)"
        )

        conn.execute("ALTER TABLE replay_metadata ADD COLUMN last_completed_date TEXT")

        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError(f"v1->v2 migration would violate foreign key integrity: {fk_errors}")

        _set_schema_version(conn, 2)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_db(conn: sqlite3.Connection) -> None:
    """Create all sandbox tables if they do not already exist, and migrate an
    existing older database up to SCHEMA_VERSION. Idempotent -- safe to call on every
    CLI invocation.

    CREATE TABLE IF NOT EXISTS never alters an existing table's physical schema, so a
    pre-existing database is migrated explicitly (see _migrate_v1_to_v2) based on its
    ACTUAL recorded schema_version -- never by blindly overwriting schema_meta to
    SCHEMA_VERSION, which would just relabel an unmigrated database without changing
    its physical schema."""

    conn.executescript(_DDL)

    current_version = _get_schema_version(conn)
    if current_version is None:
        # Brand-new database: the DDL above already created every table at the
        # current (v2) schema, so there is nothing to migrate -- just record it.
        _set_schema_version(conn, SCHEMA_VERSION)
        conn.commit()
        return

    if current_version < 2:
        _migrate_v1_to_v2(conn)
        current_version = 2

    conn.commit()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn
