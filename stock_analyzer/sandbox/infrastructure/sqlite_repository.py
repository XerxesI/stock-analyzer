"""SQLite-backed repository for the Recommendation Sandbox.

The application layer depends on this class's method signatures only, not on SQLite
specifics, so the storage engine could later be swapped (ADR-006). Most "insert"
methods for append-only tables use INSERT OR IGNORE and report whether a row was
newly created, which is how the application layer implements idempotent daily runs
(a duplicate key there always means "this exact event was already recorded" --
attempts/snapshots/recommendations/transactions/data-quality-events are each keyed by
an entity id plus a date, and re-deriving the same entity+date deterministically
produces the same content, so no separate conflict check is needed for them).

insert_ranked_candidate is the one exception: it explicitly checks the existing row's
content before writing, distinguishing an identical pre-existing row (safe resume,
returns False) from a genuine conflict (raises RankedCandidateConflictError). See its
docstring -- this was added after a resume defect where a blanket "any rowcount==0
means corruption" assumption in the application layer broke legitimate resumes.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.data_quality import DataQualityEvent
from stock_analyzer.sandbox.domain.entry_order import EntryOrder, EntryOrderAttempt
from stock_analyzer.sandbox.domain.position import PositionSnapshot, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import Recommendation
from stock_analyzer.sandbox.domain.replay import ReplayMetadata
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.domain.transaction import VirtualTransaction


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _b(value: int | None) -> bool | None:
    return None if value is None else bool(value)


_FLOAT_TOLERANCE = 1e-6


def _floats_close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= _FLOAT_TOLERANCE * max(1.0, abs(a), abs(b))


def _ranked_candidate_content_matches(existing: RankedCandidate, new: RankedCandidate) -> bool:
    """True if `existing` and `new` describe the same logical event (same symbol/date
    producing the same ranking/data-quality outcome) -- the case a replay resume must
    treat as an idempotent no-op. False means a genuine conflict: the same
    candidate_id (same as_of_date + symbol) produced DIFFERENT content across two
    runs, which is not safe to silently accept."""

    return (
        existing.symbol == new.symbol
        and existing.as_of_date == new.as_of_date
        and existing.daily_rank == new.daily_rank
        and _floats_close(existing.model_score, new.model_score)
        and _floats_close(existing.signal_close, new.signal_close)
        and _floats_close(existing.atr14, new.atr14)
        and _floats_close(existing.max_entry_price, new.max_entry_price)
        and existing.shadow_top10 == new.shadow_top10
        and existing.actionable == new.actionable
        and existing.exclusion_reason == new.exclusion_reason
        and existing.adv_quintile == new.adv_quintile
        and existing.market_regime == new.market_regime
    )


class RankedCandidateConflictError(RuntimeError):
    """Raised by insert_ranked_candidate when a row for this candidate_id already
    exists with DIFFERENT content than what is being inserted now. A plain repeat of
    IDENTICAL content is the expected, safe outcome of resuming a replay that already
    persisted this signal date -- see insert_ranked_candidate and
    application/candidate_service.py."""


class SandboxRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def connection(self) -> sqlite3.Connection:
        """Read-only escape hatch for ad hoc reporting queries (e.g. replay_metrics.py)
        that would be premature to promote into named repository methods. Application
        services must not use this for writes -- all writes go through the named
        methods below so idempotency/append-only guarantees stay centralized."""

        return self._conn

    # ---------------------------------------------------------------- runs
    def create_run(self, run: SandboxRun) -> tuple[SandboxRun, bool]:
        """Idempotent: if a run with this run_id already exists, returns it unchanged
        (created=False) rather than inserting a second one."""

        existing = self.get_run(run.run_id)
        if existing is not None:
            return existing, False
        self._conn.execute(
            "INSERT INTO sandbox_runs "
            "(run_id, as_of_date, command, started_at, completed_at, status, "
            " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                run.run_id,
                run.as_of_date.isoformat(),
                run.command,
                run.started_at.isoformat(),
                run.completed_at.isoformat() if run.completed_at else None,
                run.status,
                run.model_version,
                run.data_snapshot_id,
                run.code_commit_sha,
                run.configuration_hash,
                run.error_message,
            ),
        )
        self._conn.commit()
        return run, True

    def get_run(self, run_id: str) -> SandboxRun | None:
        row = self._conn.execute("SELECT * FROM sandbox_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return SandboxRun(
            run_id=row["run_id"],
            as_of_date=_d(row["as_of_date"]),
            command=row["command"],
            started_at=_dt(row["started_at"]),
            completed_at=_dt(row["completed_at"]),
            status=row["status"],
            model_version=row["model_version"],
            data_snapshot_id=row["data_snapshot_id"],
            code_commit_sha=row["code_commit_sha"],
            configuration_hash=row["configuration_hash"],
            error_message=row["error_message"],
        )

    def complete_run(self, run_id: str, completed_at: datetime) -> None:
        self._conn.execute(
            "UPDATE sandbox_runs SET status='COMPLETED', completed_at=? WHERE run_id=?",
            (completed_at.isoformat(), run_id),
        )
        self._conn.commit()

    def fail_run(self, run_id: str, completed_at: datetime, error_message: str) -> None:
        self._conn.execute(
            "UPDATE sandbox_runs SET status='FAILED', completed_at=?, error_message=? WHERE run_id=?",
            (completed_at.isoformat(), error_message, run_id),
        )
        self._conn.commit()

    # --------------------------------------------------------- candidates
    def insert_ranked_candidate(self, candidate: RankedCandidate) -> bool:
        """Returns True if this row was newly inserted, False if a row with this
        candidate_id already existed and has IDENTICAL content (the expected outcome
        when a replay resume reprocesses an already-persisted signal date -- see
        application/candidate_service.py). Raises RankedCandidateConflictError if a
        row with this candidate_id already exists with DIFFERENT content, since that
        is not a safe resume: the same (as_of_date, symbol) produced two different
        results across runs and silently keeping the first would disagree with the
        in-memory result the caller is about to use.

        Checks existence explicitly (rather than relying on INSERT OR IGNORE's
        rowcount, which cannot distinguish an identical pre-existing row from a
        conflicting one) and uses a plain INSERT for the actual write, so any other,
        genuinely unexpected constraint violation surfaces as sqlite3.IntegrityError
        instead of being silently swallowed."""

        existing = self.get_candidate(candidate.candidate_id)
        if existing is not None:
            if _ranked_candidate_content_matches(existing, candidate):
                return False
            raise RankedCandidateConflictError(
                f"ranked_candidates row for {candidate.candidate_id} already exists with "
                f"different content than the candidate being inserted now -- "
                f"existing={existing!r}, new={candidate!r}."
            )
        self._conn.execute(
            "INSERT INTO ranked_candidates "
            "(candidate_id, run_id, as_of_date, symbol, daily_rank, model_score, signal_close, "
            " atr14, max_entry_price, shadow_top10, actionable, exclusion_reason, adv_quintile, "
            " market_regime, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate.candidate_id,
                candidate.run_id,
                candidate.as_of_date.isoformat(),
                candidate.symbol,
                candidate.daily_rank,
                candidate.model_score,
                candidate.signal_close,
                candidate.atr14,
                candidate.max_entry_price,
                int(candidate.shadow_top10),
                int(candidate.actionable),
                candidate.exclusion_reason,
                candidate.adv_quintile,
                candidate.market_regime,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return True

    def get_candidates_for_date(self, as_of_date: date) -> list[RankedCandidate]:
        rows = self._conn.execute(
            "SELECT * FROM ranked_candidates WHERE as_of_date = ? ORDER BY daily_rank ASC",
            (as_of_date.isoformat(),),
        ).fetchall()
        return [self._row_to_candidate(r) for r in rows]

    def get_candidate(self, candidate_id: str) -> RankedCandidate | None:
        row = self._conn.execute(
            "SELECT * FROM ranked_candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        return self._row_to_candidate(row) if row else None

    @staticmethod
    def _row_to_candidate(row: sqlite3.Row) -> RankedCandidate:
        return RankedCandidate(
            candidate_id=row["candidate_id"],
            run_id=row["run_id"],
            as_of_date=_d(row["as_of_date"]),
            symbol=row["symbol"],
            daily_rank=row["daily_rank"],
            model_score=row["model_score"],
            signal_close=row["signal_close"],
            atr14=row["atr14"],
            max_entry_price=row["max_entry_price"],
            shadow_top10=_b(row["shadow_top10"]),
            actionable=_b(row["actionable"]),
            exclusion_reason=row["exclusion_reason"],
            adv_quintile=row["adv_quintile"],
            market_regime=row["market_regime"],
        )

    # -------------------------------------------------------- entry orders
    def create_entry_order(self, order: EntryOrder) -> tuple[EntryOrder, bool]:
        existing = self.get_entry_order(order.order_id)
        if existing is not None:
            return existing, False
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO entry_orders "
            "(order_id, candidate_id, symbol, signal_date, created_date, valid_until, "
            " max_entry_price, status, fill_date, fill_price, fill_reason, no_fill_reason, "
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                order.order_id,
                order.candidate_id,
                order.symbol,
                order.signal_date.isoformat(),
                order.created_date.isoformat(),
                order.valid_until.isoformat(),
                order.max_entry_price,
                order.status,
                order.fill_date.isoformat() if order.fill_date else None,
                order.fill_price,
                order.fill_reason,
                order.no_fill_reason,
                now,
                now,
            ),
        )
        self._conn.commit()
        return order, True

    def get_entry_order(self, order_id: str) -> EntryOrder | None:
        row = self._conn.execute("SELECT * FROM entry_orders WHERE order_id = ?", (order_id,)).fetchone()
        return self._row_to_order(row) if row else None

    def get_entry_order_by_candidate(self, candidate_id: str) -> EntryOrder | None:
        row = self._conn.execute(
            "SELECT * FROM entry_orders WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        return self._row_to_order(row) if row else None

    def get_pending_orders(self) -> list[EntryOrder]:
        rows = self._conn.execute("SELECT * FROM entry_orders WHERE status = 'PENDING'").fetchall()
        return [self._row_to_order(r) for r in rows]

    def has_pending_order_for_symbol(self, symbol: str, before_date: date) -> bool:
        """Only counts a PENDING order whose signal_date is strictly BEFORE
        before_date. Candidate selection calls this with the signal day currently
        being decided -- an order with signal_date == that same day can only be this
        same candidate-generation call's own, already-created order from an earlier,
        interrupted attempt at this date (replay resume reprocesses the full date
        list, including a date whose Phase 4 order creation partially completed
        before crashing). Counting it would wrongly exclude a symbol as
        "already pending" because of its own not-yet-finished selection, diverging
        from what an uninterrupted run would have decided."""

        row = self._conn.execute(
            "SELECT 1 FROM entry_orders WHERE symbol = ? AND status = 'PENDING' AND signal_date < ? LIMIT 1",
            (symbol, before_date.isoformat()),
        ).fetchone()
        return row is not None

    def update_order_status(
        self,
        order_id: str,
        status: str,
        fill_date: date | None = None,
        fill_price: float | None = None,
        fill_reason: str | None = None,
        no_fill_reason: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE entry_orders SET status=?, fill_date=?, fill_price=?, fill_reason=?, "
            "no_fill_reason=?, updated_at=? WHERE order_id=?",
            (
                status,
                fill_date.isoformat() if fill_date else None,
                fill_price,
                fill_reason,
                no_fill_reason,
                datetime.now(timezone.utc).isoformat(),
                order_id,
            ),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> EntryOrder:
        return EntryOrder(
            order_id=row["order_id"],
            candidate_id=row["candidate_id"],
            symbol=row["symbol"],
            signal_date=_d(row["signal_date"]),
            created_date=_d(row["created_date"]),
            valid_until=_d(row["valid_until"]),
            max_entry_price=row["max_entry_price"],
            status=row["status"],
            fill_date=_d(row["fill_date"]),
            fill_price=row["fill_price"],
            fill_reason=row["fill_reason"],
            no_fill_reason=row["no_fill_reason"],
        )

    # ------------------------------------------------- entry order attempts
    def insert_entry_order_attempt(self, attempt: EntryOrderAttempt) -> bool:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO entry_order_attempts "
            "(attempt_id, order_id, symbol, attempt_date, session_open, session_high, "
            " session_low, session_close, max_entry_price, outcome, fill_price, reason, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                attempt.attempt_id,
                attempt.order_id,
                attempt.symbol,
                attempt.attempt_date.isoformat(),
                attempt.session_open,
                attempt.session_high,
                attempt.session_low,
                attempt.session_close,
                attempt.max_entry_price,
                attempt.outcome,
                attempt.fill_price,
                attempt.reason,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_attempts_for_order(self, order_id: str) -> list[EntryOrderAttempt]:
        rows = self._conn.execute(
            "SELECT * FROM entry_order_attempts WHERE order_id = ? ORDER BY attempt_date ASC", (order_id,)
        ).fetchall()
        return [
            EntryOrderAttempt(
                attempt_id=r["attempt_id"],
                order_id=r["order_id"],
                symbol=r["symbol"],
                attempt_date=_d(r["attempt_date"]),
                session_open=r["session_open"],
                session_high=r["session_high"],
                session_low=r["session_low"],
                session_close=r["session_close"],
                max_entry_price=r["max_entry_price"],
                outcome=r["outcome"],
                fill_price=r["fill_price"],
                reason=r["reason"],
            )
            for r in rows
        ]

    # ------------------------------------------------------- positions
    def create_position(self, position: VirtualPosition) -> tuple[VirtualPosition, bool]:
        existing = self.get_position(position.position_id)
        if existing is not None:
            return existing, False
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO virtual_positions "
            "(position_id, symbol, candidate_id, order_id, signal_date, entry_date, entry_price, "
            " quantity, initial_rank, initial_model_score, signal_close, max_entry_price, "
            " initial_adv_quintile, initial_market_regime, status, current_holding_day_count, "
            " current_close, unrealized_return, mfe, mae, target_price, planned_time_exit_date, "
            " exit_date, exit_price, exit_reason, realized_return, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                position.position_id,
                position.symbol,
                position.candidate_id,
                position.order_id,
                position.signal_date.isoformat(),
                position.entry_date.isoformat(),
                position.entry_price,
                position.quantity,
                position.initial_rank,
                position.initial_model_score,
                position.signal_close,
                position.max_entry_price,
                position.initial_adv_quintile,
                position.initial_market_regime,
                position.status,
                position.current_holding_day_count,
                position.current_close,
                position.unrealized_return,
                position.mfe,
                position.mae,
                position.target_price,
                position.planned_time_exit_date.isoformat(),
                position.exit_date.isoformat() if position.exit_date else None,
                position.exit_price,
                position.exit_reason,
                position.realized_return,
                now,
                now,
            ),
        )
        self._conn.commit()
        return position, True

    def get_position(self, position_id: str) -> VirtualPosition | None:
        row = self._conn.execute(
            "SELECT * FROM virtual_positions WHERE position_id = ?", (position_id,)
        ).fetchone()
        return self._row_to_position(row) if row else None

    def get_open_positions(self) -> list[VirtualPosition]:
        rows = self._conn.execute("SELECT * FROM virtual_positions WHERE status = 'OPEN'").fetchall()
        return [self._row_to_position(r) for r in rows]

    def has_open_position_for_symbol(self, symbol: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM virtual_positions WHERE symbol = ? AND status = 'OPEN' LIMIT 1", (symbol,)
        ).fetchone()
        return row is not None

    def update_position_state(
        self,
        position_id: str,
        current_holding_day_count: int,
        current_close: float | None,
        unrealized_return: float | None,
        mfe: float,
        mae: float,
    ) -> None:
        self._conn.execute(
            "UPDATE virtual_positions SET current_holding_day_count=?, current_close=?, "
            "unrealized_return=?, mfe=?, mae=?, updated_at=? WHERE position_id=?",
            (
                current_holding_day_count,
                current_close,
                unrealized_return,
                mfe,
                mae,
                datetime.now(timezone.utc).isoformat(),
                position_id,
            ),
        )
        self._conn.commit()

    def close_position(
        self,
        position_id: str,
        exit_date: date,
        exit_price: float,
        exit_reason: str,
        realized_return: float,
        final_holding_day_count: int,
        final_mfe: float,
        final_mae: float,
    ) -> None:
        """`final_holding_day_count`/`final_mfe`/`final_mae` are required -- the
        closing session's own values, matching what was just written to that
        position's final position_snapshots row. Without this, current_holding_day_
        count/mfe/mae would freeze at the PRIOR day's HOLD update and read one
        session stale forever after close (see EXP-004 review: reported mean
        holding days/MFE/MAE were wrong because a consumer read this table's
        current-state columns instead of the final snapshot)."""

        self._conn.execute(
            "UPDATE virtual_positions SET status='CLOSED', exit_date=?, exit_price=?, "
            "exit_reason=?, realized_return=?, current_holding_day_count=?, mfe=?, mae=?, "
            "updated_at=? WHERE position_id=?",
            (
                exit_date.isoformat(),
                exit_price,
                exit_reason,
                realized_return,
                final_holding_day_count,
                final_mfe,
                final_mae,
                datetime.now(timezone.utc).isoformat(),
                position_id,
            ),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> VirtualPosition:
        return VirtualPosition(
            position_id=row["position_id"],
            symbol=row["symbol"],
            candidate_id=row["candidate_id"],
            order_id=row["order_id"],
            signal_date=_d(row["signal_date"]),
            entry_date=_d(row["entry_date"]),
            entry_price=row["entry_price"],
            quantity=row["quantity"],
            initial_rank=row["initial_rank"],
            initial_model_score=row["initial_model_score"],
            signal_close=row["signal_close"],
            max_entry_price=row["max_entry_price"],
            initial_adv_quintile=row["initial_adv_quintile"],
            initial_market_regime=row["initial_market_regime"],
            status=row["status"],
            current_holding_day_count=row["current_holding_day_count"],
            current_close=row["current_close"],
            unrealized_return=row["unrealized_return"],
            mfe=row["mfe"],
            mae=row["mae"],
            target_price=row["target_price"],
            planned_time_exit_date=_d(row["planned_time_exit_date"]),
            exit_date=_d(row["exit_date"]),
            exit_price=row["exit_price"],
            exit_reason=row["exit_reason"],
            realized_return=row["realized_return"],
        )

    # --------------------------------------------------- position snapshots
    def insert_position_snapshot(self, snapshot: PositionSnapshot) -> bool:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO position_snapshots "
            "(snapshot_id, position_id, symbol, as_of_date, close_price, daily_return, "
            " cumulative_unrealized_return, holding_day_count, mfe, mae, distance_to_target, "
            " current_rank, current_model_score, rank_change_from_entry, current_adv_quintile, "
            " current_market_regime, data_quality_status, recommendation, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot.snapshot_id,
                snapshot.position_id,
                snapshot.symbol,
                snapshot.as_of_date.isoformat(),
                snapshot.close_price,
                snapshot.daily_return,
                snapshot.cumulative_unrealized_return,
                snapshot.holding_day_count,
                snapshot.mfe,
                snapshot.mae,
                snapshot.distance_to_target,
                snapshot.current_rank,
                snapshot.current_model_score,
                snapshot.rank_change_from_entry,
                snapshot.current_adv_quintile,
                snapshot.current_market_regime,
                snapshot.data_quality_status,
                snapshot.recommendation,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_snapshots_for_position(self, position_id: str) -> list[PositionSnapshot]:
        rows = self._conn.execute(
            "SELECT * FROM position_snapshots WHERE position_id = ? ORDER BY as_of_date ASC",
            (position_id,),
        ).fetchall()
        return [
            PositionSnapshot(
                snapshot_id=r["snapshot_id"],
                position_id=r["position_id"],
                symbol=r["symbol"],
                as_of_date=_d(r["as_of_date"]),
                close_price=r["close_price"],
                daily_return=r["daily_return"],
                cumulative_unrealized_return=r["cumulative_unrealized_return"],
                holding_day_count=r["holding_day_count"],
                mfe=r["mfe"],
                mae=r["mae"],
                distance_to_target=r["distance_to_target"],
                current_rank=r["current_rank"],
                current_model_score=r["current_model_score"],
                rank_change_from_entry=r["rank_change_from_entry"],
                current_adv_quintile=r["current_adv_quintile"],
                current_market_regime=r["current_market_regime"],
                data_quality_status=r["data_quality_status"],
                recommendation=r["recommendation"],
            )
            for r in rows
        ]

    # ------------------------------------------------------ recommendations
    def insert_recommendation(self, rec: Recommendation) -> bool:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO recommendations "
            "(recommendation_id, entity_type, entity_id, symbol, as_of_date, recommendation, "
            " reason, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                rec.recommendation_id,
                rec.entity_type,
                rec.entity_id,
                rec.symbol,
                rec.as_of_date.isoformat(),
                rec.recommendation,
                rec.reason,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_recommendations_for_entity(self, entity_type: str, entity_id: str) -> list[Recommendation]:
        rows = self._conn.execute(
            "SELECT * FROM recommendations WHERE entity_type = ? AND entity_id = ? ORDER BY as_of_date ASC",
            (entity_type, entity_id),
        ).fetchall()
        return [
            Recommendation(
                recommendation_id=r["recommendation_id"],
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                symbol=r["symbol"],
                as_of_date=_d(r["as_of_date"]),
                recommendation=r["recommendation"],
                reason=r["reason"],
            )
            for r in rows
        ]

    # ------------------------------------------------------- transactions
    def insert_transaction(self, txn: VirtualTransaction) -> bool:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO virtual_transactions "
            "(transaction_id, position_id, symbol, transaction_type, transaction_date, price, "
            " quantity, notional, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                txn.transaction_id,
                txn.position_id,
                txn.symbol,
                txn.transaction_type,
                txn.transaction_date.isoformat(),
                txn.price,
                txn.quantity,
                txn.notional,
                txn.reason,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_transactions_for_position(self, position_id: str) -> list[VirtualTransaction]:
        rows = self._conn.execute(
            "SELECT * FROM virtual_transactions WHERE position_id = ? ORDER BY transaction_date ASC",
            (position_id,),
        ).fetchall()
        return [
            VirtualTransaction(
                transaction_id=r["transaction_id"],
                position_id=r["position_id"],
                symbol=r["symbol"],
                transaction_type=r["transaction_type"],
                transaction_date=_d(r["transaction_date"]),
                price=r["price"],
                quantity=r["quantity"],
                notional=r["notional"],
                reason=r["reason"],
            )
            for r in rows
        ]

    # --------------------------------------------------- data quality events
    def insert_data_quality_event(self, event: DataQualityEvent) -> bool:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO data_quality_events "
            "(event_id, symbol, as_of_date, event_type, details, created_at) VALUES (?,?,?,?,?,?)",
            (
                event.event_id,
                event.symbol,
                event.as_of_date.isoformat(),
                event.event_type,
                event.details,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_data_quality_events_for_date(self, as_of_date: date) -> list[DataQualityEvent]:
        rows = self._conn.execute(
            "SELECT * FROM data_quality_events WHERE as_of_date = ?", (as_of_date.isoformat(),)
        ).fetchall()
        return [
            DataQualityEvent(
                event_id=r["event_id"],
                symbol=r["symbol"],
                as_of_date=_d(r["as_of_date"]),
                event_type=r["event_type"],
                details=r["details"],
            )
            for r in rows
        ]

    # ------------------------------------------------------- reporting queries
    def get_orders_created_on(self, as_of_date: date) -> list[EntryOrder]:
        rows = self._conn.execute(
            "SELECT * FROM entry_orders WHERE created_date = ?", (as_of_date.isoformat(),)
        ).fetchall()
        return [self._row_to_order(r) for r in rows]

    def get_orders_filled_on(self, as_of_date: date) -> list[EntryOrder]:
        rows = self._conn.execute(
            "SELECT * FROM entry_orders WHERE fill_date = ? AND status = 'FILLED'", (as_of_date.isoformat(),)
        ).fetchall()
        return [self._row_to_order(r) for r in rows]

    def get_orders_expired_or_skipped_on(self, as_of_date: date) -> list[EntryOrder]:
        rows = self._conn.execute(
            "SELECT eo.* FROM entry_orders eo "
            "JOIN entry_order_attempts a ON a.order_id = eo.order_id "
            "WHERE a.attempt_date = ? AND eo.status IN ('EXPIRED','SKIPPED')",
            (as_of_date.isoformat(),),
        ).fetchall()
        return [self._row_to_order(r) for r in rows]

    def get_positions_closed_on(self, as_of_date: date) -> list[VirtualPosition]:
        rows = self._conn.execute(
            "SELECT * FROM virtual_positions WHERE exit_date = ? AND status = 'CLOSED'", (as_of_date.isoformat(),)
        ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_snapshots_for_date(self, as_of_date: date) -> list[PositionSnapshot]:
        rows = self._conn.execute(
            "SELECT * FROM position_snapshots WHERE as_of_date = ?", (as_of_date.isoformat(),)
        ).fetchall()
        return [
            PositionSnapshot(
                snapshot_id=r["snapshot_id"],
                position_id=r["position_id"],
                symbol=r["symbol"],
                as_of_date=_d(r["as_of_date"]),
                close_price=r["close_price"],
                daily_return=r["daily_return"],
                cumulative_unrealized_return=r["cumulative_unrealized_return"],
                holding_day_count=r["holding_day_count"],
                mfe=r["mfe"],
                mae=r["mae"],
                distance_to_target=r["distance_to_target"],
                current_rank=r["current_rank"],
                current_model_score=r["current_model_score"],
                rank_change_from_entry=r["rank_change_from_entry"],
                current_adv_quintile=r["current_adv_quintile"],
                current_market_regime=r["current_market_regime"],
                data_quality_status=r["data_quality_status"],
                recommendation=r["recommendation"],
            )
            for r in rows
        ]

    # ------------------------------------------------------- replay metadata
    def create_replay_metadata(self, replay: ReplayMetadata) -> tuple[ReplayMetadata, bool]:
        """Idempotent by replay_id: a rerun with the same replay_id returns the
        existing row (created=False) rather than a second one -- callers must decide
        whether that means "verify identical" or "refuse to proceed" (EXP-004
        section 5)."""

        existing = self.get_replay_metadata(replay.replay_id)
        if existing is not None:
            return existing, False
        self._conn.execute(
            "INSERT INTO replay_metadata "
            "(replay_id, classification, code_commit_sha, model_version, feature_snapshot_id, "
            " market_data_snapshot_id, signal_start_date, signal_end_date, outcome_data_end_date, "
            " configuration_json, configuration_hash, status, started_at, completed_at, "
            " last_completed_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                replay.replay_id,
                replay.classification,
                replay.code_commit_sha,
                replay.model_version,
                replay.feature_snapshot_id,
                replay.market_data_snapshot_id,
                replay.signal_start_date.isoformat(),
                replay.signal_end_date.isoformat(),
                replay.outcome_data_end_date.isoformat(),
                replay.configuration_json,
                replay.configuration_hash,
                replay.status,
                replay.started_at.isoformat(),
                replay.completed_at.isoformat() if replay.completed_at else None,
                replay.last_completed_date.isoformat() if replay.last_completed_date else None,
            ),
        )
        self._conn.commit()
        return replay, True

    def get_replay_metadata(self, replay_id: str) -> ReplayMetadata | None:
        row = self._conn.execute(
            "SELECT * FROM replay_metadata WHERE replay_id = ?", (replay_id,)
        ).fetchone()
        if row is None:
            return None
        return ReplayMetadata(
            replay_id=row["replay_id"],
            classification=row["classification"],
            code_commit_sha=row["code_commit_sha"],
            model_version=row["model_version"],
            feature_snapshot_id=row["feature_snapshot_id"],
            market_data_snapshot_id=row["market_data_snapshot_id"],
            signal_start_date=_d(row["signal_start_date"]),
            signal_end_date=_d(row["signal_end_date"]),
            outcome_data_end_date=_d(row["outcome_data_end_date"]),
            configuration_json=row["configuration_json"],
            configuration_hash=row["configuration_hash"],
            status=row["status"],
            started_at=_dt(row["started_at"]),
            completed_at=_dt(row["completed_at"]),
            last_completed_date=_d(row["last_completed_date"]),
        )

    def complete_replay(self, replay_id: str, completed_at: datetime) -> None:
        self._conn.execute(
            "UPDATE replay_metadata SET status='COMPLETED', completed_at=? WHERE replay_id=?",
            (completed_at.isoformat(), replay_id),
        )
        self._conn.commit()

    def fail_replay(self, replay_id: str, completed_at: datetime) -> None:
        self._conn.execute(
            "UPDATE replay_metadata SET status='FAILED', completed_at=? WHERE replay_id=?",
            (completed_at.isoformat(), replay_id),
        )
        self._conn.commit()

    _DOMAIN_TABLES = (
        "ranked_candidates",
        "entry_orders",
        "entry_order_attempts",
        "virtual_positions",
        "position_snapshots",
        "recommendations",
        "virtual_transactions",
        "data_quality_events",
    )

    def has_any_domain_state(self) -> bool:
        """True if ANY row exists in ANY domain table. Used to decide whether a
        RUNNING/FAILED replay with an unknown (NULL) resume watermark -- e.g. a
        database migrated from a schema version that predates last_completed_date --
        can be safely assumed to have done no work yet (empty domain tables, safe to
        resume from the beginning) or must reject resume outright (see
        application/replay_service.py's UntrustworthyResumeWatermarkError)."""

        for table in self._DOMAIN_TABLES:
            row = self._conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            if row is not None:
                return True
        return False

    def mark_date_completed(self, replay_id: str, completed_date: date) -> None:
        """Advances the resume watermark after a date's FULL processing (entries +
        monitoring + candidate generation, if a signal day) has committed
        successfully. ReplayService uses this to skip already-completed dates on
        resume -- see application/replay_service.py."""

        self._conn.execute(
            "UPDATE replay_metadata SET last_completed_date=? WHERE replay_id=?",
            (completed_date.isoformat(), replay_id),
        )
        self._conn.commit()
