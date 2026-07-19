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
migration for any gap, one version at a time -- see _migrate_v1_to_v2 and
_migrate_v2_to_v3. A brand-new database is created directly at the latest schema by
the DDL below and simply has its version recorded; schema_meta is never blindly
overwritten to SCHEMA_VERSION without either creating a fresh database or completing
every intervening real migration. A schema_version recorded HIGHER than
SCHEMA_VERSION (a database written by newer code than is currently running) is
refused outright -- see UnsupportedSchemaVersionError.

Version history (each version number denotes an ACTUAL, distinct physical schema that
was published at some point -- never reused, even when a later fix needs "one more"
change than the previous bump anticipated):
  v1: original schema -- ranked_candidates.signal_close NOT NULL, no
      replay_metadata.last_completed_date.
  v2: published in commit db067d4/68b0c6f -- ranked_candidates.signal_close nullable
      (a MISSING_MARKET_DATA candidate has no signal close by definition; the v1 NOT
      NULL constraint was silently swallowing that row via INSERT OR IGNORE -- see
      candidate_service.py). Still no last_completed_date: a v2 database predates the
      resume watermark entirely.
  v3: current -- adds replay_metadata.last_completed_date, the replay resume
      watermark so ReplayService only ever reprocesses the one date that may have
      been partially done when a process died, never already-completed history (see
      application/replay_service.py). A v1 or v2 database migrated to v3 gets this
      column added as NULL for any existing replay_metadata rows -- see
      application/replay_service.py's UntrustworthyResumeWatermarkError for how a
      RUNNING/FAILED replay with a NULL watermark AND already-persisted domain state
      is handled (resume is refused outright rather than guessed at).

An earlier version of this module bumped SCHEMA_VERSION to 2 a second time to mean
"nullable signal_close AND the watermark column" -- reusing a version number that had
already been published for a physically different schema. Any v2 database created by
the originally published code (nullable signal_close, no watermark) would then be
mistaken for a fully up-to-date v3-equivalent database and never migrated, leaving
`replay_metadata.last_completed_date` referenced by code but physically absent. This
file now treats "v2" as permanently meaning exactly what commit 68b0c6f shipped.

A second, related trap: the version-reuse bug above also existed as a live bug for a
window of published commits (between 68b0c6f and the fix for the trap above) whose
`init_db()` would blindly overwrite `schema_meta.schema_version` to `2` on ANY
existing database it opened -- including a genuinely physical v1 database it never
actually migrated (`ranked_candidates.signal_close` still `NOT NULL`). That means a
database recorded as `schema_version=2` is NOT reliably "correct physical v2": it
could equally be a physically-v1 database that was only ever relabeled, never
migrated. `init_db` therefore never trusts the recorded version 2 at face value --
it inspects `ranked_candidates.signal_close`'s actual nullability to decide whether a
"v2"-labeled database needs the v1->v2 physical repair before proceeding to v3 (see
init_db's handling of current_version == 2). A database whose physical structure
matches neither known state at a given recorded version fails closed with
SchemaIntegrityError rather than guessing.
"""

from __future__ import annotations

import sqlite3


class UnsupportedSchemaVersionError(RuntimeError):
    """Raised when a database's recorded schema_version is HIGHER than this code's
    SCHEMA_VERSION -- i.e. the database was written by newer code than is currently
    running. Operating on it anyway could silently misinterpret or corrupt a physical
    schema this code has never seen."""


class SchemaIntegrityError(RuntimeError):
    """Raised when a database's physical structure does not match any state this
    code knows how to migrate from, for its recorded schema_version -- e.g. a
    "v2"-labeled database whose ranked_candidates.signal_close is neither the
    original v1 shape (NOT NULL) nor the genuine v2 shape (nullable), or one that
    already has replay_metadata.last_completed_date despite being labeled v2 (which
    should never exist before v3). Never trust schema_meta's label alone when a
    historical code path is known to have written an incorrect one -- inspect the
    physical schema and fail closed rather than guess a migration path."""


SCHEMA_VERSION = 3

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


def _ranked_candidates_signal_close_is_nullable(conn: sqlite3.Connection) -> bool:
    for col in conn.execute("PRAGMA table_info(ranked_candidates)").fetchall():
        if col[1] == "signal_close":  # (cid, name, type, notnull, dflt_value, pk)
            return col[3] == 0
    raise SchemaIntegrityError(
        "ranked_candidates.signal_close column not found -- corrupt or unrecognized schema."
    )


def _replay_metadata_has_watermark_column(conn: sqlite3.Connection) -> bool:
    return any(
        col[1] == "last_completed_date"
        for col in conn.execute("PRAGMA table_info(replay_metadata)").fetchall()
    )


def _verify_ranked_candidates_indexes(conn: sqlite3.Connection) -> None:
    indexes = conn.execute("PRAGMA index_list(ranked_candidates)").fetchall()
    index_names = {row[1] for row in indexes}  # (seq, name, unique, origin, partial)
    if "idx_ranked_candidates_as_of_date" not in index_names:
        raise SchemaIntegrityError(
            "Post-migration check failed: idx_ranked_candidates_as_of_date index is missing "
            "from ranked_candidates."
        )
    if not any(row[2] for row in indexes):
        raise SchemaIntegrityError(
            "Post-migration check failed: UNIQUE(symbol, as_of_date) constraint is missing "
            "from ranked_candidates."
        )


def _verify_v2_invariants(conn: sqlite3.Connection) -> None:
    """Checked before every commit that would label a database v2 (or v3, which
    implies v2): ranked_candidates.signal_close must actually be nullable and its
    indexes/uniqueness constraint must actually exist -- never just assumed because
    the migration ran without raising."""

    if not _ranked_candidates_signal_close_is_nullable(conn):
        raise SchemaIntegrityError(
            "Post-migration check failed: ranked_candidates.signal_close is still NOT NULL."
        )
    _verify_ranked_candidates_indexes(conn)


def _verify_v3_invariants(conn: sqlite3.Connection) -> None:
    """Checked before every commit that would label a database v3."""

    _verify_v2_invariants(conn)
    if not _replay_metadata_has_watermark_column(conn):
        raise SchemaIntegrityError(
            "Post-migration check failed: replay_metadata.last_completed_date is missing."
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
    """Physically migrates an existing v1 database to v2 -- exactly what commit
    db067d4/68b0c6f published: ranked_candidates.signal_close NOT NULL -> nullable.
    SQLite cannot ALTER a column's NOT NULL constraint in place, so this rebuilds the
    table (SQLite's documented pattern for this kind of change): create a new table
    with the corrected schema, copy every existing row across unchanged, drop the old
    table, rename the new one into place, then verify referential integrity. Does NOT
    touch replay_metadata -- a v2 database predates the resume watermark entirely
    (see _migrate_v2_to_v3).

    Runs inside one explicit transaction (BEGIN is issued before any DDL, since
    relying on sqlite3's implicit-transaction behavior does not cover DDL
    statements): any failure -- including a failed foreign_key_check -- rolls back
    completely, and schema_meta.schema_version is only written to '2' after every
    step, including the integrity check, has succeeded. A database that fails
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

        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError(f"v1->v2 migration would violate foreign key integrity: {fk_errors}")

        # Never label this database v2 because the rebuild ran without raising --
        # verify the physical result actually has the v2 shape first.
        _verify_v2_invariants(conn)

        _set_schema_version(conn, 2)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Physically migrates an existing v2 database to v3: replay_metadata gains
    last_completed_date, the replay resume watermark (see
    application/replay_service.py). A v2 database's ranked_candidates.signal_close is
    ALREADY nullable (that was v1->v2's job, see _migrate_v1_to_v2) -- this step only
    adds the new column, which ADD COLUMN handles safely without a table rebuild.

    Existing replay_metadata rows get last_completed_date = NULL (SQLite's default
    for a new column on pre-existing rows) -- meaning "unknown," not "nothing
    completed yet." ReplayService.run() treats a NULL watermark on a RUNNING/FAILED
    replay that already has persisted domain state as untrustworthy and refuses to
    resume it (UntrustworthyResumeWatermarkError) rather than guessing.

    Runs inside its own explicit transaction, exactly like _migrate_v1_to_v2: any
    failure rolls back completely and the database stays labelled v2, matching its
    true physical state.
    """

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")

        conn.execute("ALTER TABLE replay_metadata ADD COLUMN last_completed_date TEXT")

        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError(f"v2->v3 migration would violate foreign key integrity: {fk_errors}")

        # Never label this database v3 without verifying BOTH v3 invariants -- the
        # watermark column just added, AND signal_close's nullability (this function
        # assumes its caller already ensured that, but a database's ranked_candidates
        # could in principle be in an unexpected state; fail closed rather than trust
        # the caller silently).
        _verify_v3_invariants(conn)

        _set_schema_version(conn, 3)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_db(conn: sqlite3.Connection) -> None:
    """Create all sandbox tables if they do not already exist, and migrate an
    existing older database up to SCHEMA_VERSION, one version at a time. Idempotent
    -- safe to call on every CLI invocation.

    CREATE TABLE IF NOT EXISTS never alters an existing table's physical schema, so a
    pre-existing database is migrated explicitly (see _migrate_v1_to_v2,
    _migrate_v2_to_v3) based on its ACTUAL recorded schema_version -- never by
    blindly overwriting schema_meta to SCHEMA_VERSION, which would just relabel an
    unmigrated database without changing its physical schema. A database recorded as
    NEWER than SCHEMA_VERSION is refused outright rather than silently operated on.

    A recorded schema_version of 2 is NOT trusted at face value: a published bug (see
    this module's docstring) could relabel a genuinely physical v1 database as '2'
    without ever migrating ranked_candidates.signal_close to nullable. So for
    current_version == 2, the ACTUAL physical shape of ranked_candidates is inspected
    before choosing a migration path -- correct physical v2 (nullable already) only
    needs _migrate_v2_to_v3; mislabeled physical v1 (still NOT NULL) needs the
    v1->v2 physical repair first. A database whose physical shape matches neither
    known state at that recorded version fails closed with SchemaIntegrityError."""

    conn.executescript(_DDL)

    current_version = _get_schema_version(conn)
    if current_version is None:
        # Brand-new database: the DDL above already created every table at the
        # current (v3) schema, so there is nothing to migrate -- just record it.
        _verify_v3_invariants(conn)
        _set_schema_version(conn, SCHEMA_VERSION)
        conn.commit()
        return

    if current_version > SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"Database schema_version={current_version} is newer than this code "
            f"understands (SCHEMA_VERSION={SCHEMA_VERSION}). Refusing to open it -- "
            "upgrade the code before opening this database."
        )

    if current_version < 2:
        _migrate_v1_to_v2(conn)
        current_version = 2

    if current_version == 2:
        if _replay_metadata_has_watermark_column(conn):
            # Should never happen at a genuine v1 or v2 database -- the watermark
            # column was only ever introduced by v2->v3. Refuse to guess.
            raise SchemaIntegrityError(
                "Database recorded as schema_version=2 already has "
                "replay_metadata.last_completed_date, which is inconsistent with any "
                "known v1 or v2 physical state. Refusing to guess a migration path."
            )
        if not _ranked_candidates_signal_close_is_nullable(conn):
            # Mislabeled: a known historical bug in an earlier init_db() could write
            # schema_version=2 onto a database whose ranked_candidates was never
            # actually migrated. Repair by running the exact same v1->v2 physical
            # migration (safe: it operates purely on physical structure and does not
            # read or trust the currently-stored version), which brings schema_meta
            # back in sync with physical reality before proceeding to v3.
            _migrate_v1_to_v2(conn)
        _migrate_v2_to_v3(conn)
        current_version = 3

    conn.commit()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn
