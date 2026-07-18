"""SQLite DDL for the Recommendation Sandbox. See
docs/04_decisions/ADR-006-Sandbox-Persistence-and-Audit-Trail.md.

Two categories of table:
  - mutable current-state: sandbox_runs, entry_orders, virtual_positions
    (entry_orders/virtual_positions only mutate their own lifecycle fields; entry-time
    facts are set once and never changed).
  - append-only: ranked_candidates, entry_order_attempts, position_snapshots,
    recommendations, virtual_transactions, data_quality_events.
"""

from __future__ import annotations

import sqlite3

# v2: signal_close is now nullable (a MISSING_MARKET_DATA candidate has no signal
# close by definition; the NOT NULL constraint was silently swallowing that row via
# INSERT OR IGNORE -- see the candidate-persistence fix in candidate_service.py).
# CREATE TABLE IF NOT EXISTS does not retroactively migrate existing database files;
# this only affects databases created after this change.
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
    completed_at TEXT
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create all sandbox tables if they do not already exist. Idempotent -- safe to
    call on every CLI invocation."""

    conn.executescript(_DDL)
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn
