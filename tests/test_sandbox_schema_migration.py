"""Tests for the sandbox's schema versioning and migrations:
  v1 (original)        -> v2 (published, commit db067d4/68b0c6f): ranked_candidates.
                           signal_close NOT NULL -> nullable.
  v2 (published)        -> v3 (current): replay_metadata gains last_completed_date
                           (the replay resume watermark).
  v1 -> v3 chains through both steps.

Builds database fixtures using LITERAL OLD DDL (not the current schema.py DDL) for
both v1 and the ACTUALLY PUBLISHED v2 (nullable signal_close, no watermark column),
so these tests exercise real physical databases rather than something that merely
claims to be a given version. An earlier version of schema.py bumped SCHEMA_VERSION
to 2 a second time to also mean "plus the watermark column" -- reusing a version
number already published for a physically different schema, which would have left
any real v2 database (nullable signal_close, no watermark) mistaken for a
fully-migrated database and never given the watermark column. These tests guard
against that regression by verifying against the LITERAL published v2 schema, not
just the v1 schema.

Never touches the real EXP-004 replay database -- see
docs/09_experiments/EXP-004_Sandbox_Historical_Replay.md and the project's explicit
instruction not to rerun or mutate it during this work.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.infrastructure.schema import (
    SCHEMA_VERSION,
    UnsupportedSchemaVersionError,
    connect,
    init_db,
)
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

assert SCHEMA_VERSION == 3, "these tests assume the current schema version is 3 -- update them if it changes"

_V1_DDL = """
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
    signal_close REAL NOT NULL,
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

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

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


_V2_DDL = """
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

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Exactly what commit db067d4/68b0c6f published: no last_completed_date column.
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


def _insert_representative_v1_rows(conn: sqlite3.Connection) -> None:
    """One row per table, wired together by real foreign keys, so the migration's
    FK-integrity check has something meaningful to verify."""

    conn.execute(
        "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, status, "
        " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("run-1", "2026-01-05", "generate-candidates", "2026-01-05T00:00:00+00:00", None, "COMPLETED",
         "v1", None, None, "hash", None),
    )
    conn.execute(
        "INSERT INTO ranked_candidates (candidate_id, run_id, as_of_date, symbol, daily_rank, "
        " model_score, signal_close, atr14, max_entry_price, shadow_top10, actionable, "
        " exclusion_reason, adv_quintile, market_regime, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-01-05:AAA", "run-1", "2026-01-05", "AAA", 1, 5.0, 100.0, 2.0, 101.0, 1, 1,
         None, "adv_q3", "Bull_Normal", "2026-01-05T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO entry_orders (order_id, candidate_id, symbol, signal_date, created_date, "
        " valid_until, max_entry_price, status, fill_date, fill_price, fill_reason, no_fill_reason, "
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("order-1", "2026-01-05:AAA", "AAA", "2026-01-05", "2026-01-05", "2026-01-07", 101.0,
         "FILLED", "2026-01-06", 100.5, "next_day_open<=max_entry_price", None,
         "2026-01-05T00:00:00+00:00", "2026-01-06T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO virtual_positions (position_id, symbol, candidate_id, order_id, signal_date, "
        " entry_date, entry_price, quantity, initial_rank, initial_model_score, signal_close, "
        " max_entry_price, initial_adv_quintile, initial_market_regime, status, "
        " current_holding_day_count, current_close, unrealized_return, mfe, mae, target_price, "
        " planned_time_exit_date, exit_date, exit_price, exit_reason, realized_return, created_at, "
        " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("AAA:2026-01-06", "AAA", "2026-01-05:AAA", "order-1", "2026-01-05", "2026-01-06", 100.5,
         100.0, 1, 5.0, 100.0, 101.0, "adv_q3", "Bull_Normal", "OPEN", 1, 100.5, 0.0, 0.0, 0.0,
         120.6, "2026-02-03", None, None, None, None, "2026-01-06T00:00:00+00:00",
         "2026-01-06T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO replay_metadata (replay_id, classification, code_commit_sha, model_version, "
        " feature_snapshot_id, market_data_snapshot_id, signal_start_date, signal_end_date, "
        " outcome_data_end_date, configuration_json, configuration_hash, status, started_at, "
        " completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("replay-old", "DEVELOPMENT_HISTORICAL_REPLAY", "sha", "v1", None, None, "2026-01-05",
         "2026-01-10", "2026-01-10", "{}", "hash", "COMPLETED", "2026-01-05T00:00:00+00:00",
         "2026-01-10T00:00:00+00:00"),
    )


def _build_v1_fixture(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(_V1_DDL)
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')")
    _insert_representative_v1_rows(conn)
    conn.commit()
    conn.close()


def _insert_representative_v2_rows(conn: sqlite3.Connection) -> None:
    """Same shape as _insert_representative_v1_rows, plus one candidate with a NULL
    signal_close -- only possible in v2's already-nullable column, and exactly the
    kind of row a real published-v2 database could contain."""

    conn.execute(
        "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, status, "
        " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("run-1", "2026-01-05", "generate-candidates", "2026-01-05T00:00:00+00:00", None, "COMPLETED",
         "v2", None, None, "hash", None),
    )
    conn.execute(
        "INSERT INTO ranked_candidates (candidate_id, run_id, as_of_date, symbol, daily_rank, "
        " model_score, signal_close, atr14, max_entry_price, shadow_top10, actionable, "
        " exclusion_reason, adv_quintile, market_regime, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-01-05:AAA", "run-1", "2026-01-05", "AAA", 1, 5.0, 100.0, 2.0, 101.0, 1, 1,
         None, "adv_q3", "Bull_Normal", "2026-01-05T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO ranked_candidates (candidate_id, run_id, as_of_date, symbol, daily_rank, "
        " model_score, signal_close, atr14, max_entry_price, shadow_top10, actionable, "
        " exclusion_reason, adv_quintile, market_regime, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-01-05:ZZZ", "run-1", "2026-01-05", "ZZZ", 2, 3.0, None, None, None, 1, 0,
         "MISSING_MARKET_DATA", None, None, "2026-01-05T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO entry_orders (order_id, candidate_id, symbol, signal_date, created_date, "
        " valid_until, max_entry_price, status, fill_date, fill_price, fill_reason, no_fill_reason, "
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("order-1", "2026-01-05:AAA", "AAA", "2026-01-05", "2026-01-05", "2026-01-07", 101.0,
         "FILLED", "2026-01-06", 100.5, "next_day_open<=max_entry_price", None,
         "2026-01-05T00:00:00+00:00", "2026-01-06T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO virtual_positions (position_id, symbol, candidate_id, order_id, signal_date, "
        " entry_date, entry_price, quantity, initial_rank, initial_model_score, signal_close, "
        " max_entry_price, initial_adv_quintile, initial_market_regime, status, "
        " current_holding_day_count, current_close, unrealized_return, mfe, mae, target_price, "
        " planned_time_exit_date, exit_date, exit_price, exit_reason, realized_return, created_at, "
        " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("AAA:2026-01-06", "AAA", "2026-01-05:AAA", "order-1", "2026-01-05", "2026-01-06", 100.5,
         100.0, 1, 5.0, 100.0, 101.0, "adv_q3", "Bull_Normal", "OPEN", 1, 100.5, 0.0, 0.0, 0.0,
         120.6, "2026-02-03", None, None, None, None, "2026-01-06T00:00:00+00:00",
         "2026-01-06T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO replay_metadata (replay_id, classification, code_commit_sha, model_version, "
        " feature_snapshot_id, market_data_snapshot_id, signal_start_date, signal_end_date, "
        " outcome_data_end_date, configuration_json, configuration_hash, status, started_at, "
        " completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("replay-v2", "DEVELOPMENT_HISTORICAL_REPLAY", "sha", "v2", None, None, "2026-01-05",
         "2026-01-10", "2026-01-10", "{}", "hash", "COMPLETED", "2026-01-05T00:00:00+00:00",
         "2026-01-10T00:00:00+00:00"),
    )


def _build_v2_fixture(db_path: str) -> None:
    """A physical database matching EXACTLY what commit db067d4/68b0c6f published:
    nullable signal_close, schema_version='2', no last_completed_date column."""

    conn = sqlite3.connect(db_path)
    conn.executescript(_V2_DDL)
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', '2')")
    _insert_representative_v2_rows(conn)
    conn.commit()
    conn.close()


def _schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    return row[0] if row else None


def test_v1_migrates_through_v2_to_v3_and_preserves_data(tmp_path):
    """A physical v1 database must chain through BOTH migration steps (v1->v2->v3)
    to reach the current schema -- not stop at v2, and not skip straight to v3
    without physically applying the v1->v2 table rebuild."""

    db_path = str(tmp_path / "v1_fixture.db")
    _build_v1_fixture(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)

    assert _schema_version(conn) == str(SCHEMA_VERSION) == "3"

    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(ranked_candidates)").fetchall()}
    assert cols["signal_close"]["notnull"] == 0  # NOT NULL -> nullable (v1->v2)

    row = conn.execute(
        "SELECT * FROM ranked_candidates WHERE candidate_id = ?", ("2026-01-05:AAA",)
    ).fetchone()
    assert row is not None
    assert row["signal_close"] == 100.0
    assert row["symbol"] == "AAA"
    assert row["daily_rank"] == 1

    assert conn.execute("SELECT COUNT(*) FROM ranked_candidates").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM entry_orders").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM virtual_positions").fetchone()[0] == 1

    indexes = conn.execute("PRAGMA index_list(ranked_candidates)").fetchall()
    index_names = {r["name"] for r in indexes}
    assert "idx_ranked_candidates_as_of_date" in index_names
    assert any(r["unique"] for r in indexes)  # UNIQUE(symbol, as_of_date) survived the rebuild

    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    replay_cols = {r["name"] for r in conn.execute("PRAGMA table_info(replay_metadata)").fetchall()}
    assert "last_completed_date" in replay_cols  # v2->v3
    stored_replay = conn.execute(
        "SELECT * FROM replay_metadata WHERE replay_id = 'replay-old'"
    ).fetchone()
    assert stored_replay["last_completed_date"] is None  # ADD COLUMN default for pre-existing rows
    assert stored_replay["status"] == "COMPLETED"  # untouched by the migration

    conn.close()


def test_v2_migrates_to_v3_and_preserves_data(tmp_path):
    """A physical v2 database -- EXACTLY what commit db067d4/68b0c6f published
    (nullable signal_close already, no watermark column) -- must gain
    last_completed_date and reach v3, without ranked_candidates being touched at all
    (it is already correctly shaped; only v1->v2 rebuilds that table)."""

    db_path = str(tmp_path / "v2_fixture.db")
    _build_v2_fixture(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)

    assert _schema_version(conn) == str(SCHEMA_VERSION) == "3"

    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(ranked_candidates)").fetchall()}
    assert cols["signal_close"]["notnull"] == 0

    # Both existing rows preserved exactly, including the pre-existing NULL
    # signal_close row that only a genuine v2 database could contain.
    assert conn.execute("SELECT COUNT(*) FROM ranked_candidates").fetchone()[0] == 2
    null_row = conn.execute(
        "SELECT * FROM ranked_candidates WHERE candidate_id = ?", ("2026-01-05:ZZZ",)
    ).fetchone()
    assert null_row["signal_close"] is None
    assert null_row["exclusion_reason"] == "MISSING_MARKET_DATA"

    assert conn.execute("SELECT COUNT(*) FROM entry_orders").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM virtual_positions").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    replay_cols = {r["name"] for r in conn.execute("PRAGMA table_info(replay_metadata)").fetchall()}
    assert "last_completed_date" in replay_cols
    stored_replay = conn.execute(
        "SELECT * FROM replay_metadata WHERE replay_id = 'replay-v2'"
    ).fetchone()
    assert stored_replay["last_completed_date"] is None
    assert stored_replay["status"] == "COMPLETED"  # untouched by the migration

    conn.close()


def test_v2_migration_rolls_back_and_does_not_relabel_on_failure(tmp_path):
    """A physically-broken v2 database (dangling FK) must make the v2->v3 migration
    fail its own integrity check, roll back completely, and stay labelled v2 -- not
    silently relabelled v3 without last_completed_date actually being safe/consistent
    with the rest of the (corrupt) database."""

    db_path = str(tmp_path / "v2_broken_fixture.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_V2_DDL)
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', '2')")
    conn.execute(
        "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, status, "
        " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("run-1", "2026-01-05", "generate-candidates", "2026-01-05T00:00:00+00:00", None, "COMPLETED",
         "v2", None, None, "hash", None),
    )
    conn.execute(
        "INSERT INTO ranked_candidates (candidate_id, run_id, as_of_date, symbol, daily_rank, "
        " model_score, signal_close, atr14, max_entry_price, shadow_top10, actionable, "
        " exclusion_reason, adv_quintile, market_regime, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-01-05:AAA", "run-1", "2026-01-05", "AAA", 1, 5.0, 100.0, 2.0, 101.0, 1, 1,
         None, "adv_q3", "Bull_Normal", "2026-01-05T00:00:00+00:00"),
    )
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO entry_orders (order_id, candidate_id, symbol, signal_date, created_date, "
        " valid_until, max_entry_price, status, fill_date, fill_price, fill_reason, no_fill_reason, "
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("order-broken", "2026-01-05:DOES_NOT_EXIST", "ZZZ", "2026-01-05", "2026-01-05",
         "2026-01-07", 101.0, "PENDING", None, None, None, None,
         "2026-01-05T00:00:00+00:00", "2026-01-05T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    with pytest.raises(RuntimeError, match="foreign key"):
        init_db(conn)

    assert _schema_version(conn) == "2"  # not relabelled v3
    replay_cols = {r["name"] for r in conn.execute("PRAGMA table_info(replay_metadata)").fetchall()}
    assert "last_completed_date" not in replay_cols  # ADD COLUMN was rolled back
    assert conn.execute("SELECT COUNT(*) FROM ranked_candidates").fetchone()[0] == 1

    conn.close()


def test_unsupported_future_schema_version_is_refused(tmp_path):
    """A database recorded as NEWER than this code's SCHEMA_VERSION (written by
    newer code than is currently running) must be refused outright, not silently
    opened and potentially misread or corrupted."""

    db_path = str(tmp_path / "future_fixture.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', '99')")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    with pytest.raises(UnsupportedSchemaVersionError, match="99"):
        init_db(conn)

    # Left completely untouched -- still recorded as '99', not silently changed.
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    assert row[0] == "99"
    conn.close()


def test_fresh_database_is_created_directly_at_v3(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    conn = connect(db_path)

    assert _schema_version(conn) == str(SCHEMA_VERSION) == "3"
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(ranked_candidates)").fetchall()}
    assert cols["signal_close"]["notnull"] == 0
    replay_cols = {r["name"] for r in conn.execute("PRAGMA table_info(replay_metadata)").fetchall()}
    assert "last_completed_date" in replay_cols

    conn.close()


def test_null_signal_close_can_be_persisted_after_migration(tmp_path):
    db_path = str(tmp_path / "v1_fixture.db")
    _build_v1_fixture(db_path)

    conn = connect(db_path)  # connect() calls init_db(), which migrates v1 -> v2
    repo = SandboxRepository(conn)

    run = SandboxRun(
        run_id="run-2",
        as_of_date=date(2026, 1, 6),
        command="generate-candidates",
        started_at=datetime.now(timezone.utc),
        configuration_hash="hash2",
        model_version="v1",
    )
    repo.create_run(run)

    candidate = RankedCandidate(
        candidate_id=RankedCandidate.make_id(date(2026, 1, 6), "ZZZ"),
        run_id=run.run_id,
        as_of_date=date(2026, 1, 6),
        symbol="ZZZ",
        daily_rank=1,
        model_score=1.0,
        signal_close=None,
        atr14=None,
        max_entry_price=None,
        shadow_top10=True,
        actionable=False,
        exclusion_reason="MISSING_MARKET_DATA",
        adv_quintile=None,
        market_regime=None,
    )
    assert repo.insert_ranked_candidate(candidate) is True
    stored = repo.get_candidate(candidate.candidate_id)
    assert stored is not None
    assert stored.signal_close is None

    conn.close()


def test_repeated_init_after_migration_is_idempotent(tmp_path):
    db_path = str(tmp_path / "v1_fixture.db")
    _build_v1_fixture(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    assert _schema_version(conn) == "3"

    # A second and third call (e.g. two more CLI invocations against the now-migrated
    # database) must not error, re-migrate, or alter data.
    init_db(conn)
    init_db(conn)

    assert _schema_version(conn) == "3"
    assert conn.execute("SELECT COUNT(*) FROM ranked_candidates").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM entry_orders").fetchone()[0] == 1
    conn.close()


def test_repeated_init_from_published_v2_is_idempotent(tmp_path):
    db_path = str(tmp_path / "v2_fixture.db")
    _build_v2_fixture(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    assert _schema_version(conn) == "3"

    init_db(conn)
    init_db(conn)

    assert _schema_version(conn) == "3"
    assert conn.execute("SELECT COUNT(*) FROM ranked_candidates").fetchone()[0] == 2
    conn.close()


def test_migration_rolls_back_and_does_not_relabel_on_failure(tmp_path):
    """A physically-broken v1 database (a dangling foreign key -- the kind of
    corruption an older, buggier code path or a manual edit could leave behind) must
    make the migration fail its own integrity check, roll back completely, and leave
    the database labelled v1 -- not silently relabelled v2 with a corrupt or
    half-migrated table."""

    db_path = str(tmp_path / "v1_broken_fixture.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_V1_DDL)
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')")
    conn.execute(
        "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, status, "
        " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("run-1", "2026-01-05", "generate-candidates", "2026-01-05T00:00:00+00:00", None, "COMPLETED",
         "v1", None, None, "hash", None),
    )
    conn.execute(
        "INSERT INTO ranked_candidates (candidate_id, run_id, as_of_date, symbol, daily_rank, "
        " model_score, signal_close, atr14, max_entry_price, shadow_top10, actionable, "
        " exclusion_reason, adv_quintile, market_regime, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-01-05:AAA", "run-1", "2026-01-05", "AAA", 1, 5.0, 100.0, 2.0, 101.0, 1, 1,
         None, "adv_q3", "Bull_Normal", "2026-01-05T00:00:00+00:00"),
    )
    # A dangling FK, only possible with foreign_keys OFF -- entry_orders.candidate_id
    # pointing at a candidate_id that does not exist.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO entry_orders (order_id, candidate_id, symbol, signal_date, created_date, "
        " valid_until, max_entry_price, status, fill_date, fill_price, fill_reason, no_fill_reason, "
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("order-broken", "2026-01-05:DOES_NOT_EXIST", "ZZZ", "2026-01-05", "2026-01-05",
         "2026-01-07", 101.0, "PENDING", None, None, None, None,
         "2026-01-05T00:00:00+00:00", "2026-01-05T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    with pytest.raises(RuntimeError, match="foreign key"):
        init_db(conn)

    assert _schema_version(conn) == "1"  # not relabelled
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(ranked_candidates)").fetchall()}
    assert cols["signal_close"]["notnull"] == 1  # physical table untouched -- still v1
    assert conn.execute("SELECT COUNT(*) FROM ranked_candidates").fetchone()[0] == 1
    replay_cols = {r["name"] for r in conn.execute("PRAGMA table_info(replay_metadata)").fetchall()}
    assert "last_completed_date" not in replay_cols  # ADD COLUMN was rolled back too

    conn.close()
