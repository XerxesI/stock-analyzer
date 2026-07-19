"""Tests for EXP-005's four new tables (Revision 5, Stage 2, corrected in the
Stage 2-5 review cycle -- Sections 8.1, 8.5, 18;
docs/09_experiments/EXP-005_Implementation_Checklist.md).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from stock_analyzer.sandbox.exp005.infrastructure.schema import (
    DECISION_AUDIT_DDL,
    DECISION_AUDIT_SCHEMA_VERSION,
    DecisionAuditSchemaIntegrityError,
    UnsupportedDecisionAuditSchemaVersionError,
    _get_decision_audit_schema_version,
    _verify_v1_physical_invariants,
    init_exp005_schema,
)
from stock_analyzer.sandbox.infrastructure.schema import init_db

NOW = datetime.now(timezone.utc).isoformat()


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)  # core sandbox tables (ranked_candidates, entry_orders, ...)
    init_exp005_schema(c)  # the four new EXP-005 tables
    return c


def _insert_sandbox_run(conn: sqlite3.Connection, run_id: str = "run-1") -> None:
    conn.execute(
        "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, status, "
        " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, "2026-01-05", "generate-candidates", NOW, None, "COMPLETED", "v1", None, None, "hash", None),
    )


def _insert_candidate(conn: sqlite3.Connection, candidate_id: str, run_id: str = "run-1", symbol: str = "AAA") -> None:
    conn.execute(
        "INSERT INTO ranked_candidates (candidate_id, run_id, as_of_date, symbol, daily_rank, "
        " model_score, signal_close, atr14, max_entry_price, shadow_top10, actionable, "
        " exclusion_reason, adv_quintile, market_regime, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (candidate_id, run_id, "2026-01-05", symbol, 1, 5.0, 100.0, 2.0, 101.0, 1, 1, None, "adv_q3", "Bull_Normal", NOW),
    )


def _insert_admission(
    conn: sqlite3.Connection, admission_id: str, candidate_id: str, *, decision: str = "ACCEPTED", slot_budget_units=1_000_000
) -> None:
    conn.execute(
        "INSERT INTO portfolio_admissions (admission_id, replay_id, candidate_id, symbol, as_of_date, "
        " decision, rank_at_admission, slot_budget_units, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (admission_id, "replay-1", candidate_id, "AAA", "2026-01-05", decision, 1, slot_budget_units, None, NOW),
    )


# --------------------------------------------------------------------- FK enforcement


def test_foreign_keys_are_actually_enabled(conn: sqlite3.Connection):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_admission_rejects_nonexistent_candidate(conn: sqlite3.Connection):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_admission(conn, "does-not-exist", "does-not-exist")


def test_reservation_rejects_nonexistent_admission(conn: sqlite3.Connection):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO slot_reservations (reservation_id, replay_id, admission_id, candidate_id, symbol, "
            " reserved_amount_units, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("r1", "replay-1", "no-such-admission", "AAA", "AAA", 1_000_000, "RESERVED", NOW, None),
        )


def test_execution_rejects_nonexistent_candidate(conn: sqlite3.Connection):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO executions (execution_id, replay_id, variant_id, control_seed, order_id, "
            " candidate_id, position_id, symbol, side, decision_date, execution_date, "
            " raw_market_fill_price_units, effective_fill_price_units, quantity_units, gross_notional_units, "
            " commission_units, slippage_rate_units, slippage_cost_units, net_cash_flow_units, fill_reason, "
            " market_data_snapshot_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "e1", "replay-1", "B", None, None, "no-such-candidate", None, "AAA", "SELL",
                "2026-01-05", "2026-01-06", 1_000_000, 1_000_500, 100_000, 100_050_000, 100, 5, 50_000, 99_949_900,
                "SELL_TARGET", "snap-1", NOW,
            ),
        )


# ------------------------------------------------------------------- uniqueness/orphans


def test_duplicate_reservation_for_one_admission_is_rejected(conn: sqlite3.Connection):
    _insert_sandbox_run(conn)
    _insert_candidate(conn, "c1")
    _insert_admission(conn, "c1", "c1")
    conn.execute(
        "INSERT INTO slot_reservations (reservation_id, replay_id, admission_id, candidate_id, symbol, "
        " reserved_amount_units, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("r1", "replay-1", "c1", "c1", "AAA", 1_000_000, "RESERVED", NOW, None),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO slot_reservations (reservation_id, replay_id, admission_id, candidate_id, symbol, "
            " reserved_amount_units, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("r2-different-id", "replay-1", "c1", "c1", "AAA", 1_000_000, "RESERVED", NOW, None),
        )


def test_duplicate_execution_identity_is_rejected(conn: sqlite3.Connection):
    _insert_sandbox_run(conn)
    _insert_candidate(conn, "c1")
    params = (
        "e1", "replay-1", "B", None, None, "c1", None, "AAA", "SELL",
        "2026-01-05", "2026-01-06", 1_000_000, 1_000_500, 100_000, 100_050_000, 100, 5, 50_000, 99_949_900,
        "SELL_TARGET", "snap-1", NOW,
    )
    sql = (
        "INSERT INTO executions (execution_id, replay_id, variant_id, control_seed, order_id, "
        " candidate_id, position_id, symbol, side, decision_date, execution_date, "
        " raw_market_fill_price_units, effective_fill_price_units, quantity_units, gross_notional_units, "
        " commission_units, slippage_rate_units, slippage_cost_units, net_cash_flow_units, fill_reason, "
        " market_data_snapshot_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    conn.execute(sql, params)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, params)


def test_two_admissions_cannot_share_a_candidate_id(conn: sqlite3.Connection):
    # admission_id IS candidate_id (Section 8.2) -- the primary key itself enforces
    # "one candidate, at most one admission decision, ever."
    _insert_sandbox_run(conn)
    _insert_candidate(conn, "c1")
    _insert_admission(conn, "c1", "c1")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_admission(conn, "c1", "c1")


# ------------------------------------------------------------------------- not-null


def test_admission_decision_cannot_be_null(conn: sqlite3.Connection):
    _insert_sandbox_run(conn)
    _insert_candidate(conn, "c1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO portfolio_admissions (admission_id, replay_id, candidate_id, symbol, as_of_date, "
            " decision, rank_at_admission, slot_budget_units, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("c1", "replay-1", "c1", "AAA", "2026-01-05", None, 1, 1_000_000, None, NOW),
        )


# --------------------------------------------------------------------------- CHECKs


def test_admission_decision_slot_budget_consistency_check(conn: sqlite3.Connection):
    _insert_sandbox_run(conn)
    _insert_candidate(conn, "c1")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_admission(conn, "c1", "c1", decision="ACCEPTED", slot_budget_units=None)
    _insert_candidate(conn, "c2", symbol="BBB")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_admission(conn, "c2", "c2", decision="NO_CAPACITY", slot_budget_units=1_000_000)


def test_reservation_status_resolved_at_consistency_check(conn: sqlite3.Connection):
    _insert_sandbox_run(conn)
    _insert_candidate(conn, "c1")
    _insert_admission(conn, "c1", "c1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO slot_reservations (reservation_id, replay_id, admission_id, candidate_id, symbol, "
            " reserved_amount_units, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("r1", "replay-1", "c1", "c1", "AAA", 1_000_000, "RESERVED", NOW, NOW),  # RESERVED + resolved_at set
        )


def test_execution_quantity_must_be_positive(conn: sqlite3.Connection):
    _insert_sandbox_run(conn)
    _insert_candidate(conn, "c1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO executions (execution_id, replay_id, variant_id, control_seed, order_id, "
            " candidate_id, position_id, symbol, side, decision_date, execution_date, "
            " raw_market_fill_price_units, effective_fill_price_units, quantity_units, gross_notional_units, "
            " commission_units, slippage_rate_units, slippage_cost_units, net_cash_flow_units, fill_reason, "
            " market_data_snapshot_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "e1", "replay-1", "B", None, None, "c1", None, "AAA", "SELL",
                "2026-01-05", "2026-01-06", 1_000_000, 1_000_500, 0, 100_050_000, 100, 5, 50_000, 99_949_900,
                "SELL_TARGET", "snap-1", NOW,
            ),
        )


def test_execution_net_cash_flow_sign_must_match_side(conn: sqlite3.Connection):
    _insert_sandbox_run(conn)
    _insert_candidate(conn, "c1")
    # SELL with a negative net_cash_flow violates the frozen sign convention.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO executions (execution_id, replay_id, variant_id, control_seed, order_id, "
            " candidate_id, position_id, symbol, side, decision_date, execution_date, "
            " raw_market_fill_price_units, effective_fill_price_units, quantity_units, gross_notional_units, "
            " commission_units, slippage_rate_units, slippage_cost_units, net_cash_flow_units, fill_reason, "
            " market_data_snapshot_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "e1", "replay-1", "B", None, None, "c1", None, "AAA", "SELL",
                "2026-01-05", "2026-01-06", 1_000_000, 1_000_500, 100_000, 100_050_000, 100, 5, 50_000, -99_949_900,
                "SELL_TARGET", "snap-1", NOW,
            ),
        )


def test_buy_execution_requires_order_id(conn: sqlite3.Connection):
    _insert_sandbox_run(conn)
    _insert_candidate(conn, "c1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO executions (execution_id, replay_id, variant_id, control_seed, order_id, "
            " candidate_id, position_id, symbol, side, decision_date, execution_date, "
            " raw_market_fill_price_units, effective_fill_price_units, quantity_units, gross_notional_units, "
            " commission_units, slippage_rate_units, slippage_cost_units, net_cash_flow_units, fill_reason, "
            " market_data_snapshot_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "e1", "replay-1", "B", None, None, "c1", None, "AAA", "BUY",
                "2026-01-05", "2026-01-06", 1_000_000, 1_000_500, 100_000, 100_050_000, 100, 5, 50_000, -100_050_100,
                "FILLED_AT_OPEN", "snap-1", NOW,
            ),
        )


def test_equity_snapshot_rejects_second_row_for_same_day(conn: sqlite3.Connection):
    conn.execute(
        "INSERT INTO portfolio_equity_snapshots (snapshot_id, replay_id, as_of_date, cash_units, "
        " reserved_capital_units, open_position_market_value_units, total_equity_units, "
        " open_position_count, reserved_order_count, cumulative_commissions_units, "
        " cumulative_slippage_cost_units, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("s1", "replay-1", "2026-01-05", 10_000_000, 0, 0, 10_000_000, 0, 0, 0, 0, NOW),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO portfolio_equity_snapshots (snapshot_id, replay_id, as_of_date, cash_units, "
            " reserved_capital_units, open_position_market_value_units, total_equity_units, "
            " open_position_count, reserved_order_count, cumulative_commissions_units, "
            " cumulative_slippage_cost_units, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("s2-different-id", "replay-1", "2026-01-05", 9_900_000, 0, 0, 9_900_000, 0, 0, 0, 0, NOW),
        )


# ------------------------------------------------------------------ FK-cycle regression


def test_portfolio_admissions_has_no_reverse_reference_columns(conn: sqlite3.Connection):
    """Direct regression test for Section 8.1's foreign-key-cycle fix: the table
    must not have a reservation_id or order_id column pointing forward at its own
    children -- that is exactly the two-way cycle that could not be inserted in any
    order."""

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(portfolio_admissions)").fetchall()}
    assert "reservation_id" not in columns
    assert "order_id" not in columns


def test_admission_candidate_id_and_entry_orders_candidate_id_are_type_compatible(conn: sqlite3.Connection):
    """Section 8.1: the related order is resolved through
    entry_orders.candidate_id == portfolio_admissions.admission_id -- proves the
    join actually works end to end, not just that the types match on paper."""

    _insert_sandbox_run(conn)
    _insert_candidate(conn, "c1")
    _insert_admission(conn, "c1", "c1")
    conn.execute(
        "INSERT INTO entry_orders (order_id, candidate_id, symbol, signal_date, created_date, valid_until, "
        " max_entry_price, status, fill_date, fill_price, fill_reason, no_fill_reason, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("o1", "c1", "AAA", "2026-01-05", "2026-01-05", "2026-01-07", 101.0, "PENDING", None, None, None, None, NOW, NOW),
    )
    row = conn.execute(
        "SELECT eo.order_id FROM entry_orders eo "
        "JOIN portfolio_admissions pa ON eo.candidate_id = pa.admission_id "
        "WHERE pa.admission_id = 'c1'"
    ).fetchone()
    assert row["order_id"] == "o1"


# ------------------------------------------------------------------------- indexes


@pytest.mark.parametrize(
    "table,expected_index",
    [
        ("portfolio_admissions", "idx_portfolio_admissions_as_of_date"),
        ("portfolio_admissions", "idx_portfolio_admissions_replay_id"),
        ("slot_reservations", "idx_slot_reservations_status"),
        ("slot_reservations", "idx_slot_reservations_replay_id"),
        ("portfolio_equity_snapshots", "idx_portfolio_equity_snapshots_as_of_date"),
        ("executions", "idx_executions_order_id"),
        ("executions", "idx_executions_position_id"),
        ("executions", "idx_executions_candidate_id"),
        ("executions", "idx_executions_replay_id"),
    ],
)
def test_expected_index_exists(conn: sqlite3.Connection, table: str, expected_index: str):
    names = {row["name"] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}
    assert expected_index in names


def test_deterministic_ordering_keys_exist_on_every_new_table(conn: sqlite3.Connection):
    for table, column in (
        ("portfolio_admissions", "as_of_date"),
        ("slot_reservations", "created_at"),
        ("portfolio_equity_snapshots", "as_of_date"),
        ("executions", "execution_date"),
    ):
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert column in columns


def test_all_money_price_quantity_rate_columns_are_integer_typed(conn: sqlite3.Connection):
    """Direct regression test for the Stage 2-5 corrective cycle's core fix: no
    financial column may be REAL (float) -- every one must be INTEGER, exact
    fixed-point units."""

    expectations = {
        "portfolio_admissions": ["slot_budget_units"],
        "slot_reservations": ["reserved_amount_units"],
        "portfolio_equity_snapshots": [
            "cash_units", "reserved_capital_units", "open_position_market_value_units", "total_equity_units",
            "cumulative_commissions_units", "cumulative_slippage_cost_units",
        ],
        "executions": [
            "raw_market_fill_price_units", "effective_fill_price_units", "quantity_units", "gross_notional_units",
            "commission_units", "slippage_rate_units", "slippage_cost_units", "net_cash_flow_units",
        ],
    }
    for table, columns in expectations.items():
        info = {row["name"]: row["type"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column in columns:
            assert info[column].upper() == "INTEGER", f"{table}.{column} is {info[column]}, expected INTEGER"


def test_init_exp005_schema_is_idempotent(conn: sqlite3.Connection):
    init_exp005_schema(conn)
    init_exp005_schema(conn)
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"portfolio_admissions", "slot_reservations", "portfolio_equity_snapshots", "executions"} <= tables


# ---------------------------------------------------- decision-audit schema identity


def test_fresh_database_records_decision_audit_schema_version(conn: sqlite3.Connection):
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'decision_audit_schema_version'").fetchone()
    assert row is not None
    assert int(row["value"]) == DECISION_AUDIT_SCHEMA_VERSION == 1


def test_unsupported_future_decision_audit_version_is_refused():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('decision_audit_schema_version', '99') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )
    conn.commit()
    with pytest.raises(UnsupportedDecisionAuditSchemaVersionError):
        init_exp005_schema(conn)


# --------------------------------------- DDL-before-version-check ordering defect
#
# init_exp005_schema used to run executescript(DECISION_AUDIT_DDL) BEFORE reading
# and validating decision_audit_schema_version. Because SQLite DDL statements
# auto-commit as they execute (executescript cannot be rolled back after the fact),
# a database recorded at an unsupported FUTURE version could be physically mutated
# -- e.g. an idempotent CREATE INDEX IF NOT EXISTS silently adding an index that
# database's actual (newer, unknown) shape didn't have -- before the version
# exception was ever raised. Fixed by reading/validating the version first.


def _snapshot(conn: sqlite3.Connection) -> tuple[frozenset, frozenset]:
    """(sqlite_master rows, schema_meta rows) -- enough to detect ANY DDL or data
    mutation, not just the specific missing-index case under test."""

    master = frozenset(
        (row["type"], row["name"], row["sql"]) for row in conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
    )
    meta = frozenset((row["key"], row["value"]) for row in conn.execute("SELECT key, value FROM schema_meta").fetchall())
    return master, meta


def test_unsupported_future_version_with_missing_ddl_object_causes_zero_mutation():
    """Direct regression test: a database labeled with an unsupported future
    version, whose actual physical shape happens to be missing something the
    current code's DDL would (idempotently) create, must be rejected with the
    connection left byte-for-byte/logically unchanged -- not merely "eventually
    raises after already mutating.\""""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    # A plausible "future version" physical shape: otherwise DDL-compatible, but
    # missing one index the CURRENT code's DDL would create via IF NOT EXISTS.
    mutated_ddl = DECISION_AUDIT_DDL.replace(
        "CREATE INDEX IF NOT EXISTS idx_executions_order_id ON executions(order_id);\n", "", 1
    )
    conn.executescript(mutated_ddl)
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('decision_audit_schema_version', '99')")
    conn.commit()

    before = _snapshot(conn)
    with pytest.raises(UnsupportedDecisionAuditSchemaVersionError):
        init_exp005_schema(conn)
    after = _snapshot(conn)

    assert before == after, "database was mutated before the unsupported-version rejection"
    # Specifically confirm the index that DDL would have (idempotently) added is
    # still absent -- the exact mutation this defect used to allow.
    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(executions)").fetchall()}
    assert "idx_executions_order_id" not in indexes


def test_unsupported_future_version_with_no_physical_drift_also_causes_zero_mutation():
    """Same guarantee even when the future-version database's physical shape is
    otherwise fully DDL-compatible: no CREATE TABLE/INDEX statement should ever run
    against a database whose version we have not yet confirmed we understand."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    conn.execute(
        "UPDATE schema_meta SET value = '99' WHERE key = 'decision_audit_schema_version'"
    )
    conn.commit()

    before = _snapshot(conn)
    with pytest.raises(UnsupportedDecisionAuditSchemaVersionError):
        init_exp005_schema(conn)
    after = _snapshot(conn)

    assert before == after


# --------------------------------------------------- strict column-set equality


@pytest.mark.parametrize(
    "table,extra_column_ddl",
    [
        ("portfolio_admissions", "unexpected_extra_column TEXT"),
        ("slot_reservations", "unexpected_extra_column TEXT"),
        ("portfolio_equity_snapshots", "unexpected_extra_column TEXT"),
        ("executions", "unexpected_extra_column TEXT"),
    ],
)
def test_unexpected_extra_column_on_any_table_is_rejected(table: str, extra_column_ddl: str):
    """The frozen pilot's physical schema is matched EXACTLY, not as a compatible
    superset: an unrecognized extra column on any of the four tables (not just the
    two named forbidden columns on portfolio_admissions) must fail closed."""

    mutated_ddl = DECISION_AUDIT_DDL.replace(f"CREATE TABLE IF NOT EXISTS {table} (", f"CREATE TABLE IF NOT EXISTS {table} ({extra_column_ddl}, ", 1)
    assert mutated_ddl != DECISION_AUDIT_DDL
    conn = _init_with_mutated_ddl(mutated_ddl)
    with pytest.raises(DecisionAuditSchemaIntegrityError, match="unexpected_extra_column"):
        init_exp005_schema(conn)


def test_physical_verification_catches_a_falsely_labeled_database():
    """Never rely only on the version label: a database claiming
    decision_audit_schema_version=1 but missing the expected integer-unit columns
    (e.g. a pre-corrective-cycle float-based physical shape) must be rejected."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE portfolio_admissions (admission_id TEXT PRIMARY KEY, replay_id TEXT, as_of_date TEXT);
        CREATE TABLE slot_reservations (reservation_id TEXT PRIMARY KEY, replay_id TEXT, status TEXT, created_at TEXT);
        CREATE TABLE portfolio_equity_snapshots (snapshot_id TEXT PRIMARY KEY, as_of_date TEXT);
        -- The pre-corrective-cycle float-based shape (this is the case under test):
        -- present, but WITHOUT the *_units integer columns init_exp005_schema expects.
        CREATE TABLE executions (
            execution_id TEXT PRIMARY KEY, order_id TEXT, position_id TEXT, candidate_id TEXT,
            replay_id TEXT, execution_date TEXT, raw_market_fill_price REAL
        );
        """
    )
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('decision_audit_schema_version', '1')")
    conn.commit()
    with pytest.raises(DecisionAuditSchemaIntegrityError):
        init_exp005_schema(conn)


def test_malformed_fresh_database_is_rejected_without_ever_being_relabeled_v1():
    """Distinct from the test above (which pre-labels the malformed database as
    v1): here NO version is recorded yet -- init_exp005_schema must still reject
    the malformed physical shape, and critically must NOT fall back to writing
    decision_audit_schema_version=1 just because verification failed on a
    fresh-looking (unlabeled) database. A failed verification must never result in
    a false v1 label being recorded."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    conn.executescript(
        """
        CREATE TABLE portfolio_admissions (admission_id TEXT PRIMARY KEY, replay_id TEXT, as_of_date TEXT);
        CREATE TABLE slot_reservations (reservation_id TEXT PRIMARY KEY, replay_id TEXT, status TEXT, created_at TEXT);
        CREATE TABLE portfolio_equity_snapshots (snapshot_id TEXT PRIMARY KEY, as_of_date TEXT);
        CREATE TABLE executions (
            execution_id TEXT PRIMARY KEY, order_id TEXT, position_id TEXT, candidate_id TEXT,
            replay_id TEXT, execution_date TEXT, raw_market_fill_price REAL
        );
        """
    )
    conn.commit()

    assert _get_decision_audit_schema_version(conn) is None
    with pytest.raises(DecisionAuditSchemaIntegrityError):
        init_exp005_schema(conn)
    assert _get_decision_audit_schema_version(conn) is None, "a failed verification must never record a v1 label"


# --------------------------------------------- malformed-fixture, fail-closed suite
#
# Each test below builds an otherwise-correct v1 physical shape (derived from the
# real DECISION_AUDIT_DDL via a single targeted mutation, so only the ONE invariant
# under test is actually violated) and confirms init_exp005_schema fails closed --
# raising DecisionAuditSchemaIntegrityError -- rather than silently accepting the
# recorded version=1 label. None of these tests exercise any repair path: this
# module has none, by design (Revision 5's "no silent repair of corrupted data").


def _init_with_mutated_ddl(mutated_ddl: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    conn.executescript(mutated_ddl)
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('decision_audit_schema_version', '1')")
    conn.commit()
    return conn


def test_malformed_portfolio_admissions_wrong_column_type_fails_closed():
    mutated = DECISION_AUDIT_DDL.replace("slot_budget_units INTEGER,", "slot_budget_units TEXT,", 1)
    assert mutated != DECISION_AUDIT_DDL
    conn = _init_with_mutated_ddl(mutated)
    with pytest.raises(DecisionAuditSchemaIntegrityError, match="slot_budget_units"):
        init_exp005_schema(conn)


def test_malformed_portfolio_admissions_missing_not_null_fails_closed():
    mutated = DECISION_AUDIT_DDL.replace(
        "    replay_id TEXT NOT NULL,\n    candidate_id TEXT NOT NULL REFERENCES ranked_candidates(candidate_id),\n"
        "    symbol TEXT NOT NULL,\n    as_of_date TEXT NOT NULL,\n    decision",
        "    replay_id TEXT,\n    candidate_id TEXT NOT NULL REFERENCES ranked_candidates(candidate_id),\n"
        "    symbol TEXT NOT NULL,\n    as_of_date TEXT NOT NULL,\n    decision",
        1,
    )
    assert mutated != DECISION_AUDIT_DDL
    conn = _init_with_mutated_ddl(mutated)
    with pytest.raises(DecisionAuditSchemaIntegrityError, match="replay_id"):
        init_exp005_schema(conn)


def test_malformed_portfolio_admissions_forbidden_reverse_column_fails_closed():
    mutated = DECISION_AUDIT_DDL.replace(
        "    admission_id TEXT NOT NULL PRIMARY KEY,\n    replay_id TEXT NOT NULL,",
        "    admission_id TEXT NOT NULL PRIMARY KEY,\n    reservation_id TEXT,\n    replay_id TEXT NOT NULL,",
        1,
    )
    assert mutated != DECISION_AUDIT_DDL
    conn = _init_with_mutated_ddl(mutated)
    with pytest.raises(DecisionAuditSchemaIntegrityError, match="reservation_id"):
        init_exp005_schema(conn)


def test_malformed_slot_reservations_missing_admission_id_uniqueness_fails_closed():
    mutated = DECISION_AUDIT_DDL.replace(
        "admission_id TEXT NOT NULL UNIQUE REFERENCES portfolio_admissions(admission_id),",
        "admission_id TEXT NOT NULL REFERENCES portfolio_admissions(admission_id),",
        1,
    )
    assert mutated != DECISION_AUDIT_DDL
    conn = _init_with_mutated_ddl(mutated)
    with pytest.raises(DecisionAuditSchemaIntegrityError, match="slot_reservations"):
        init_exp005_schema(conn)


def test_malformed_portfolio_equity_snapshots_missing_uniqueness_fails_closed():
    mutated = DECISION_AUDIT_DDL.replace(
        "    created_at TEXT NOT NULL,\n    UNIQUE (replay_id, as_of_date)\n);",
        "    created_at TEXT NOT NULL\n);",
        1,
    )
    assert mutated != DECISION_AUDIT_DDL
    conn = _init_with_mutated_ddl(mutated)
    with pytest.raises(DecisionAuditSchemaIntegrityError, match="portfolio_equity_snapshots"):
        init_exp005_schema(conn)


def test_malformed_executions_missing_index_fails_closed():
    """Unlike the CREATE TABLE statements (guarded by IF NOT EXISTS, so an already-
    existing malformed table is left untouched), CREATE INDEX IF NOT EXISTS is
    per-object and would silently ADD a dropped index back onto an existing table
    the next time init_exp005_schema runs its own DDL -- so a missing-index defect
    can only be observed by calling the verification step directly, before that
    self-healing re-execution has a chance to run. This still proves the check
    itself fails closed, and documents the self-healing behavior explicitly rather
    than leaving it as a silent surprise."""

    mutated = DECISION_AUDIT_DDL.replace(
        "CREATE INDEX IF NOT EXISTS idx_executions_order_id ON executions(order_id);\n", "", 1
    )
    assert mutated != DECISION_AUDIT_DDL
    conn = _init_with_mutated_ddl(mutated)
    with pytest.raises(DecisionAuditSchemaIntegrityError, match="idx_executions_order_id"):
        _verify_v1_physical_invariants(conn)


def test_malformed_table_missing_any_check_constraint_fails_closed():
    """A table that otherwise matches every column/type/PK/index/uniqueness
    expectation, but was created with none of its CHECK constraints, must still be
    rejected -- CHECK-bearing table definitions are required "where practical" even
    though SQLite exposes no PRAGMA to verify them structurally."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    conn.executescript(
        """
        CREATE TABLE portfolio_admissions (
            admission_id TEXT NOT NULL PRIMARY KEY,
            replay_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL REFERENCES ranked_candidates(candidate_id),
            symbol TEXT NOT NULL,
            as_of_date TEXT NOT NULL,
            decision TEXT NOT NULL,
            rank_at_admission INTEGER NOT NULL,
            slot_budget_units INTEGER,
            reason TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_portfolio_admissions_as_of_date ON portfolio_admissions(as_of_date);
        CREATE INDEX idx_portfolio_admissions_replay_id ON portfolio_admissions(replay_id);
        CREATE TABLE slot_reservations (
            reservation_id TEXT NOT NULL PRIMARY KEY,
            replay_id TEXT NOT NULL,
            admission_id TEXT NOT NULL UNIQUE REFERENCES portfolio_admissions(admission_id),
            candidate_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            reserved_amount_units INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX idx_slot_reservations_status ON slot_reservations(status);
        CREATE INDEX idx_slot_reservations_replay_id ON slot_reservations(replay_id);
        CREATE TABLE portfolio_equity_snapshots (
            snapshot_id TEXT NOT NULL PRIMARY KEY,
            replay_id TEXT NOT NULL,
            as_of_date TEXT NOT NULL,
            cash_units INTEGER NOT NULL,
            reserved_capital_units INTEGER NOT NULL,
            open_position_market_value_units INTEGER NOT NULL,
            total_equity_units INTEGER NOT NULL,
            open_position_count INTEGER NOT NULL,
            reserved_order_count INTEGER NOT NULL,
            cumulative_commissions_units INTEGER NOT NULL,
            cumulative_slippage_cost_units INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (replay_id, as_of_date)
        );
        CREATE INDEX idx_portfolio_equity_snapshots_as_of_date ON portfolio_equity_snapshots(as_of_date);
        CREATE TABLE executions (
            execution_id TEXT NOT NULL PRIMARY KEY,
            replay_id TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            control_seed INTEGER,
            order_id TEXT REFERENCES entry_orders(order_id),
            candidate_id TEXT NOT NULL REFERENCES ranked_candidates(candidate_id),
            position_id TEXT REFERENCES virtual_positions(position_id),
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            decision_date TEXT NOT NULL,
            execution_date TEXT NOT NULL,
            raw_market_fill_price_units INTEGER NOT NULL,
            effective_fill_price_units INTEGER NOT NULL,
            quantity_units INTEGER NOT NULL,
            gross_notional_units INTEGER NOT NULL,
            commission_units INTEGER NOT NULL,
            slippage_rate_units INTEGER NOT NULL,
            slippage_cost_units INTEGER NOT NULL,
            net_cash_flow_units INTEGER NOT NULL,
            fill_reason TEXT NOT NULL,
            market_data_snapshot_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_executions_order_id ON executions(order_id);
        CREATE INDEX idx_executions_position_id ON executions(position_id);
        CREATE INDEX idx_executions_candidate_id ON executions(candidate_id);
        CREATE INDEX idx_executions_replay_id ON executions(replay_id);
        """
    )
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('decision_audit_schema_version', '1')")
    conn.commit()
    with pytest.raises(DecisionAuditSchemaIntegrityError, match="CHECK"):
        init_exp005_schema(conn)


def test_malformed_dangling_foreign_key_is_caught_by_integrity_check():
    """A row inserted while foreign_keys enforcement was OFF (e.g. restored from a
    dump) can leave a dangling reference that INSERT-time CHECKs never saw --
    PRAGMA foreign_key_check scans existing data directly, independent of whether
    enforcement was on at insert time, and must catch this."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO slot_reservations (reservation_id, replay_id, admission_id, candidate_id, symbol, "
        " reserved_amount_units, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("dangling-r1", "replay-1", "no-such-admission", "AAA", "AAA", 1_000_000, "RESERVED", NOW, None),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    with pytest.raises(DecisionAuditSchemaIntegrityError, match="foreign key"):
        _verify_v1_physical_invariants(conn)
