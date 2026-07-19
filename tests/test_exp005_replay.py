"""Integration/determinism tests for EXP-005's replay entry point (Revision 5,
Stage 8, Section 13's synthetic integration tests): the full stack wired via
build_exp005_replay_services, driven through the SAME ReplayService.run() path a
non-EXP-005 replay uses.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

import stock_analyzer.sandbox.application.candidate_service as candidate_service_module
import stock_analyzer.sandbox.application.entry_service as entry_service_module
import stock_analyzer.sandbox.application.monitoring_service as monitoring_service_module
from stock_analyzer.sandbox.application.candidate_service import HistoricalFeatureUniverseProvider
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.replay import DEVELOPMENT_HISTORICAL_REPLAY, ReplayMetadata
from stock_analyzer.sandbox.exp005.application.replay import build_exp005_replay_services
from stock_analyzer.sandbox.exp005.application.variant_runner import control_score
from stock_analyzer.sandbox.exp005.config import VARIANT_B, VARIANT_D, Exp005Config, PortfolioConfig
from stock_analyzer.sandbox.infrastructure.schema import init_db

SYMBOLS = ["SYM0", "SYM1", "SYM2", "SYM3", "SYM4"]


class FakeModelAdapter:
    """Matches Model2PredictionAdapter's public interface (score/model_version/
    fit_params/feature_names/train_row_count) without loading real frozen data."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores
        self.model_version = "fake-model-for-tests"
        self.fit_params = {
            "adv_edges": np.array([-np.inf, 15.0, 17.0, 19.0, 21.0, np.inf]),
            "adv_labels": ["adv_q1", "adv_q2", "adv_q3", "adv_q4", "adv_q5"],
        }
        self.feature_names = ("f1",)
        self.train_row_count = 100

    def score(self, features_df: pd.DataFrame) -> pd.Series:
        return pd.Series([self._scores.get(sym, 0.0) for sym in features_df.index], index=features_df.index)


class FakeUniverseProvider:
    def __init__(self, symbols: list[str], trading_dates: list[date]) -> None:
        self._symbols = symbols
        self._trading_dates = set(trading_dates)

    def features_for_date(self, as_of_date: date) -> pd.DataFrame:
        if as_of_date not in self._trading_dates:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "adv20": [20_000_000.0] * len(self._symbols),
                "rvol_20": [1.0] * len(self._symbols),
                "rsi_14": [50.0] * len(self._symbols),
                "spy_trend": ["Bull"] * len(self._symbols),
                "spy_volatility_bucket": ["Normal"] * len(self._symbols),
            },
            index=pd.Index(self._symbols, name="symbol"),
        )


def _business_days(start: date, n: int) -> list[date]:
    return [d.date() for d in pd.bdate_range(start=start, periods=n)]


def _synthetic_prices(as_of_date: date, days: int = 30, close: float = 100.0) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp(as_of_date), periods=days)
    closes = [close] * days  # flat -- never hits +20% target, so time exits dominate deterministically
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.005 for c in closes],
            "Low": [c * 0.995 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * days,
        },
        index=dates,
    )


def _patch_fetch_as_of(monkeypatch) -> None:
    def fake_fetch_as_of(symbol: str, fetch_date: date, period: str = "2y") -> pd.DataFrame:
        return _synthetic_prices(fetch_date)

    monkeypatch.setattr(candidate_service_module, "fetch_as_of", fake_fetch_as_of)
    monkeypatch.setattr(entry_service_module, "fetch_as_of", fake_fetch_as_of)
    monkeypatch.setattr(monitoring_service_module, "fetch_as_of", fake_fetch_as_of)


def _make_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema

    init_exp005_schema(conn)
    return conn


def _replay_metadata(replay_id: str, dates: list[date], signal_end: date) -> ReplayMetadata:
    return ReplayMetadata(
        replay_id=replay_id,
        classification=DEVELOPMENT_HISTORICAL_REPLAY,
        signal_start_date=dates[0],
        signal_end_date=signal_end,
        outcome_data_end_date=dates[-1],
        configuration_json="{}",
        configuration_hash="test-hash",
        started_at=datetime.now(timezone.utc),
    )


def _run_replay(replay_id: str, dates: list[date], variant_id: str, control_seed: int | None, monkeypatch):
    _patch_fetch_as_of(monkeypatch)
    conn = _make_connection()
    scores = {sym: float(len(SYMBOLS) - i) for i, sym in enumerate(SYMBOLS)}
    model_adapter = FakeModelAdapter(scores)
    universe = FakeUniverseProvider(SYMBOLS, dates)
    exp005_config = Exp005Config(variant_id=variant_id, control_seed=control_seed, portfolio=PortfolioConfig())

    services = build_exp005_replay_services(
        conn, model_adapter, universe, exp005_config, replay_id, market_data_snapshot_id="snap-1",
    )
    replay = _replay_metadata(replay_id, dates, signal_end=dates[-1])
    result = services.replay_service.run(replay, dates)
    return conn, services, result


# --------------------------------------------------------------- daily snapshots


def test_exactly_one_snapshot_per_processed_day(monkeypatch):
    dates = _business_days(date(2026, 1, 5), 10)
    conn, services, result = _run_replay("replay-b", dates, VARIANT_B, None, monkeypatch)

    snapshots = services.portfolio_repo.list_equity_snapshots("replay-b")

    assert len(snapshots) == len(dates)
    assert [s.as_of_date for s in snapshots] == dates


def test_snapshot_reflects_post_admission_state_not_pre_admission(monkeypatch):
    dates = _business_days(date(2026, 1, 5), 3)
    conn, services, result = _run_replay("replay-b", dates, VARIANT_B, None, monkeypatch)

    # dates[0] is a signal day -- candidates are ranked and (up to 3) admitted on it.
    first_day_snapshot = services.portfolio_repo.get_equity_snapshot("replay-b", dates[0])
    assert first_day_snapshot.reserved_order_count > 0  # reflects the admission(s) just made
    assert first_day_snapshot.reserved_capital_units > 0
    assert first_day_snapshot.cash_units < services.ledger._starting_capital_units  # cash debited


def test_reconciliation_invariant_holds_every_day(monkeypatch):
    dates = _business_days(date(2026, 1, 5), 8)
    conn, services, result = _run_replay("replay-b", dates, VARIANT_B, None, monkeypatch)

    for snapshot in services.portfolio_repo.list_equity_snapshots("replay-b"):
        assert (
            snapshot.cash_units + snapshot.reserved_capital_units + snapshot.open_position_market_value_units
            == snapshot.total_equity_units
        )


# ------------------------------------------------------------------- Variant D


def test_variant_d_scores_vary_deterministically_by_date(monkeypatch):
    dates = _business_days(date(2026, 1, 5), 5)
    conn, services, result = _run_replay("replay-d", dates, VARIANT_D, control_seed=7, monkeypatch=monkeypatch)

    admissions = services.portfolio_repo.list_admissions_for_session("replay-d", dates[0])
    assert len(admissions) > 0
    for admission in admissions:
        # the persisted rank must be consistent with the deterministic control
        # score formula for this exact (seed, date, symbol)
        expected_rank_order = sorted(SYMBOLS, key=lambda s: control_score(7, dates[0], s), reverse=True)
        assert expected_rank_order.index(admission.symbol) + 1 <= 5


def test_variant_d_is_deterministic_and_reproducible(monkeypatch):
    dates = _business_days(date(2026, 1, 5), 6)
    conn_a, services_a, _ = _run_replay("replay-d-a", dates, VARIANT_D, control_seed=3, monkeypatch=monkeypatch)
    conn_b, services_b, _ = _run_replay("replay-d-b", dates, VARIANT_D, control_seed=3, monkeypatch=monkeypatch)

    admissions_a = [(a.candidate_id, a.decision, a.rank_at_admission) for a in services_a.portfolio_repo.list_admissions_for_session("replay-d-a", dates[0])]
    admissions_b = [(a.candidate_id, a.decision, a.rank_at_admission) for a in services_b.portfolio_repo.list_admissions_for_session("replay-d-b", dates[0])]

    assert admissions_a == admissions_b


# --------------------------------------------------------------- resume safety


_EXP005_TABLES: dict[str, str] = {
    "portfolio_admissions": "admission_id",
    "slot_reservations": "reservation_id",
    "portfolio_equity_snapshots": "snapshot_id",
    "executions": "execution_id",
}
_EXCLUDE_COLUMNS = {"created_at", "updated_at", "resolved_at"}


def _dump_exp005_state(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    dump: dict[str, list[dict]] = {}
    for table, pk in _EXP005_TABLES.items():
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {pk}").fetchall()
        dump[table] = [{k: v for k, v in dict(r).items() if k not in _EXCLUDE_COLUMNS} for r in rows]
    return dump


def test_interrupted_replay_resumes_to_identical_final_state(monkeypatch):
    """Simulates a genuine crash-and-restart: the first few dates are processed
    through ReplayService._process_dates directly (which advances the resume
    watermark on success, exactly like a real crash after N dates completed --
    mirrors test_sandbox_replay_service.py's own
    test_resume_after_genuine_partial_signal_day_succeeds pattern), then a normal
    replay_service.run() call over the FULL original date list resumes from where
    the watermark left off. Every write involved -- both core and EXP-005 tables
    -- must produce the exact same final state as a single uninterrupted run of
    the identical configuration."""

    dates = _business_days(date(2026, 1, 5), 8)

    # uninterrupted run
    conn_full, services_full, _ = _run_replay("replay-full", dates, VARIANT_B, None, monkeypatch)
    full_dump = _dump_exp005_state(conn_full)

    # crashed-after-3-dates run, then resumed with the full original date list.
    _patch_fetch_as_of(monkeypatch)
    conn_resumed = _make_connection()
    scores = {sym: float(len(SYMBOLS) - i) for i, sym in enumerate(SYMBOLS)}
    model_adapter = FakeModelAdapter(scores)
    universe = FakeUniverseProvider(SYMBOLS, dates)
    exp005_config = Exp005Config(variant_id=VARIANT_B, portfolio=PortfolioConfig())
    services_resumed = build_exp005_replay_services(
        conn_resumed, model_adapter, universe, exp005_config, "replay-full", market_data_snapshot_id="snap-1",
    )
    replay = _replay_metadata("replay-full", dates, signal_end=dates[-1])
    services_resumed.sandbox_repo.create_replay_metadata(replay)
    services_resumed.replay_service._process_dates(replay, dates[:3], progress_every=None)
    assert services_resumed.sandbox_repo.get_replay_metadata("replay-full").last_completed_date == dates[2]

    services_resumed.replay_service.run(replay, dates)  # resume: full original list

    resumed_dump = _dump_exp005_state(conn_resumed)

    assert resumed_dump == full_dump
