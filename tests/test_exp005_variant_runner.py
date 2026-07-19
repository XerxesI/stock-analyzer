"""Tests for EXP-005's Variant B/D orchestration (Revision 5, Section 11.2/11.4,
Stage 7): RankingControlAdapter's deterministic control scoring and
CapacityAdmissionOrchestrator's admission seam.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.exp005.application.admission_orchestrator import AdmissionTransactionService
from stock_analyzer.sandbox.exp005.application.variant_runner import (
    CapacityAdmissionOrchestrator,
    ControlScoreNotConfiguredError,
    RankingControlAdapter,
    control_score,
)
from stock_analyzer.sandbox.exp005.domain.admission import NO_CAPACITY
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

SIGNAL_DATE = date(2026, 6, 15)
REPLAY_ID = "replay-1"


class _FakeModelAdapter:
    model_version = "test-model-v1"
    fit_params = {"adv_edges": [0, 1, 2], "adv_labels": ["adv_q1", "adv_q2"]}
    feature_names = ("f1", "f2")
    train_row_count = 1234


# --------------------------------------------------------- control_score formula


def test_control_score_matches_frozen_formula():
    seed, as_of, symbol = 7, date(2026, 6, 15), "AAA"
    expected = int(hashlib.sha256(f"{seed}:{as_of.isoformat()}:{symbol}".encode("utf-8")).hexdigest(), 16) / 2**256
    assert control_score(seed, as_of, symbol) == expected


def test_control_score_deterministic_across_repeated_calls():
    assert control_score(1, SIGNAL_DATE, "AAA") == control_score(1, SIGNAL_DATE, "AAA")


def test_control_score_varies_with_seed_date_and_symbol():
    base = control_score(1, SIGNAL_DATE, "AAA")
    assert control_score(2, SIGNAL_DATE, "AAA") != base
    assert control_score(1, date(2026, 6, 16), "AAA") != base
    assert control_score(1, SIGNAL_DATE, "BBB") != base


# ------------------------------------------------------- RankingControlAdapter


def test_ranking_control_adapter_exposes_model_provenance_unchanged():
    adapter = RankingControlAdapter(_FakeModelAdapter(), control_seed=1)
    assert adapter.model_version == "test-model-v1"
    assert adapter.fit_params == {"adv_edges": [0, 1, 2], "adv_labels": ["adv_q1", "adv_q2"]}
    assert adapter.feature_names == ("f1", "f2")
    assert adapter.train_row_count == 1234


def test_score_raises_if_current_date_not_set():
    adapter = RankingControlAdapter(_FakeModelAdapter(), control_seed=1)
    features_df = pd.DataFrame({"x": [1.0]}, index=["AAA"])
    with pytest.raises(ControlScoreNotConfiguredError):
        adapter.score(features_df)


def test_score_matches_control_score_per_symbol():
    adapter = RankingControlAdapter(_FakeModelAdapter(), control_seed=5)
    adapter.set_current_date(SIGNAL_DATE)
    features_df = pd.DataFrame({"x": [1.0, 2.0]}, index=["AAA", "BBB"])

    scores = adapter.score(features_df)

    assert scores["AAA"] == control_score(5, SIGNAL_DATE, "AAA")
    assert scores["BBB"] == control_score(5, SIGNAL_DATE, "BBB")


def test_score_invariant_to_input_row_order():
    adapter = RankingControlAdapter(_FakeModelAdapter(), control_seed=5)
    adapter.set_current_date(SIGNAL_DATE)
    forward = pd.DataFrame({"x": [1.0, 2.0, 3.0]}, index=["AAA", "BBB", "CCC"])
    reversed_ = pd.DataFrame({"x": [3.0, 2.0, 1.0]}, index=["CCC", "BBB", "AAA"])

    scores_forward = adapter.score(forward).sort_index()
    scores_reversed = adapter.score(reversed_).sort_index()

    pd.testing.assert_series_equal(scores_forward, scores_reversed)


def test_score_empty_features_df_returns_empty_series():
    adapter = RankingControlAdapter(_FakeModelAdapter(), control_seed=1)
    adapter.set_current_date(SIGNAL_DATE)
    result = adapter.score(pd.DataFrame(columns=["x"]))
    assert result.empty


def test_score_breaks_ties_by_symbol_ascending(monkeypatch):
    """Forces a collision (astronomically unlikely with the real hash) to verify
    the documented tie-break rule: symbol ascending, deterministic regardless of
    input row order."""

    import stock_analyzer.sandbox.exp005.application.variant_runner as variant_runner_module

    monkeypatch.setattr(variant_runner_module, "control_score", lambda seed, as_of, symbol: 0.5)
    adapter = RankingControlAdapter(_FakeModelAdapter(), control_seed=1)
    adapter.set_current_date(SIGNAL_DATE)
    reversed_ = pd.DataFrame({"x": [1.0, 2.0, 3.0]}, index=["CCC", "BBB", "AAA"])

    scores = adapter.score(reversed_)
    ranked = scores.sort_values(ascending=False)

    assert list(ranked.index) == ["AAA", "BBB", "CCC"]


# --------------------------------------------------- CapacityAdmissionOrchestrator


def _make_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    return conn


def _seed_candidate(conn: sqlite3.Connection, symbol: str, rank: int) -> RankedCandidate:
    run_id = f"run-{symbol}"
    conn.execute(
        "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, status, "
        " model_version, data_snapshot_id, code_commit_sha, configuration_hash, error_message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, SIGNAL_DATE.isoformat(), "generate-candidates", datetime.now(timezone.utc).isoformat(), None, "COMPLETED", "v1", None, None, "hash", None),
    )
    conn.commit()
    candidate = RankedCandidate(
        candidate_id=RankedCandidate.make_id(SIGNAL_DATE, symbol), run_id=run_id, as_of_date=SIGNAL_DATE, symbol=symbol,
        daily_rank=rank, model_score=0.5, signal_close=100.0, atr14=2.0, max_entry_price=101.0, shadow_top10=True,
        actionable=True, exclusion_reason=None, adv_quintile="adv_q1", market_regime="Bull_Normal",
    )
    SandboxRepository(conn).insert_ranked_candidate(candidate)
    return candidate


class _FixedCash:
    def __init__(self, units: int) -> None:
        self._units = units

    def available_unreserved_cash_units(self) -> int:
        return self._units


def test_capacity_orchestrator_admits_within_capacity():
    conn = _make_connection()
    portfolio_repo = PortfolioRepository(conn)
    sandbox_repo = SandboxRepository(conn)
    candidate = _seed_candidate(conn, "AAA", 1)
    admission_service = AdmissionTransactionService(
        conn, portfolio_repo, sandbox_repo, REPLAY_ID, max_slots=10, slot_budget_units=1_000_000,
        cash_provider=_FixedCash(10**12),
    )
    orchestrator = CapacityAdmissionOrchestrator(admission_service, entry_validity_sessions=2)

    orders = orchestrator.admit_and_create_orders([candidate], SIGNAL_DATE)

    assert len(orders) == 1
    assert orders[0].candidate_id == candidate.candidate_id
    admission = portfolio_repo.get_admission(candidate.candidate_id)
    assert admission.decision == "ACCEPTED"
    reservation = portfolio_repo.get_reservation_for_admission(candidate.candidate_id)
    assert reservation.status == "RESERVED"


def test_capacity_orchestrator_excludes_no_capacity_candidates_from_returned_orders():
    conn = _make_connection()
    portfolio_repo = PortfolioRepository(conn)
    sandbox_repo = SandboxRepository(conn)
    candidates = [_seed_candidate(conn, f"SYM{i}", i + 1) for i in range(3)]
    admission_service = AdmissionTransactionService(
        conn, portfolio_repo, sandbox_repo, REPLAY_ID, max_slots=1, slot_budget_units=1_000_000,
        cash_provider=_FixedCash(10**12),
    )
    orchestrator = CapacityAdmissionOrchestrator(admission_service, entry_validity_sessions=2)

    orders = orchestrator.admit_and_create_orders(candidates, SIGNAL_DATE)

    assert len(orders) == 1  # only the first (max_slots=1)
    assert orders[0].candidate_id == candidates[0].candidate_id
    second_admission = portfolio_repo.get_admission(candidates[1].candidate_id)
    assert second_admission.decision == NO_CAPACITY
    assert sandbox_repo.get_entry_order(f"{candidates[1].candidate_id}:order") is None


def test_capacity_orchestrator_processes_in_given_rank_order():
    """Section 8.4: capacity is consumed strictly in the order candidates are
    given -- the orchestrator itself does not re-sort."""

    conn = _make_connection()
    portfolio_repo = PortfolioRepository(conn)
    sandbox_repo = SandboxRepository(conn)
    candidates = [_seed_candidate(conn, sym, rank) for rank, sym in enumerate(["ZZZ", "AAA"], start=1)]
    admission_service = AdmissionTransactionService(
        conn, portfolio_repo, sandbox_repo, REPLAY_ID, max_slots=1, slot_budget_units=1_000_000,
        cash_provider=_FixedCash(10**12),
    )
    orchestrator = CapacityAdmissionOrchestrator(admission_service, entry_validity_sessions=2)

    orders = orchestrator.admit_and_create_orders(candidates, SIGNAL_DATE)

    assert orders[0].symbol == "ZZZ"  # first in the given list, not alphabetical
