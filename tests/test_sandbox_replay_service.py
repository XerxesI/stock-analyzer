from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

import stock_analyzer.sandbox.application.candidate_service as candidate_service_module
from stock_analyzer.sandbox.application.candidate_service import CandidateService
from stock_analyzer.sandbox.application.entry_service import EntryService
from stock_analyzer.sandbox.application.monitoring_service import MonitoringService
from stock_analyzer.sandbox.application.replay_service import ReplayAlreadyCompletedError, ReplayService
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.replay import COMPLETED, DEVELOPMENT_HISTORICAL_REPLAY, ReplayMetadata
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository
from stock_analyzer.sandbox.reporting.replay_metrics import build_replay_metrics

CONFIG = SandboxConfig()


class FakePredictionAdapter:
    def __init__(self, scores_by_date: dict[date, dict[str, float]]) -> None:
        self._scores_by_date = scores_by_date
        self.model_version = "fake-model-for-tests"
        self.fit_params = {
            "adv_edges": np.array([-np.inf, 15.0, 17.0, 19.0, 21.0, np.inf]),
            "adv_labels": ["adv_q1", "adv_q2", "adv_q3", "adv_q4", "adv_q5"],
        }
        self._current_date: date | None = None

    def for_date(self, as_of_date: date) -> "FakePredictionAdapter":
        self._current_date = as_of_date
        return self

    def score(self, features_df: pd.DataFrame) -> pd.Series:
        scores = self._scores_by_date.get(self._current_date, {})
        return pd.Series([scores.get(sym, 0.0) for sym in features_df.index], index=features_df.index)


class DateAwareFakeAdapter:
    """Wraps FakePredictionAdapter so CandidateService's calls to .score() see the
    right date without changing CandidateService's own interface."""

    def __init__(self, inner: FakePredictionAdapter) -> None:
        self._inner = inner
        self.model_version = inner.model_version
        self.fit_params = inner.fit_params

    def score(self, features_df: pd.DataFrame) -> pd.Series:
        return self._inner.score(features_df)


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


@pytest.fixture
def repo() -> SandboxRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return SandboxRepository(conn)


def _make_replay_service(repo: SandboxRepository, symbols: list[str], trading_dates: list[date], monkeypatch) -> ReplayService:
    def fake_fetch_as_of(symbol: str, fetch_date: date, period: str = "2y") -> pd.DataFrame:
        return _synthetic_prices(fetch_date)

    monkeypatch.setattr(candidate_service_module, "fetch_as_of", fake_fetch_as_of)
    import stock_analyzer.sandbox.application.entry_service as entry_service_module
    import stock_analyzer.sandbox.application.monitoring_service as monitoring_service_module

    monkeypatch.setattr(entry_service_module, "fetch_as_of", fake_fetch_as_of)
    monkeypatch.setattr(monitoring_service_module, "fetch_as_of", fake_fetch_as_of)

    scores_by_date = {d: {sym: float(len(symbols) - i) for i, sym in enumerate(symbols)} for d in trading_dates}
    adapter = DateAwareFakeAdapter(FakePredictionAdapter(scores_by_date))

    # CandidateService doesn't know about "current date" for the adapter -- patch the
    # adapter's score() to look at module-level "current date" state via a closure
    # keyed off generate_candidates' own as_of_date by wrapping CandidateService.
    universe = FakeUniverseProvider(symbols, trading_dates)
    candidate_service = _DateTrackingCandidateService(repo, adapter, universe, CONFIG)
    entry_service = EntryService(repo, CONFIG)
    monitoring_service = MonitoringService(repo, CONFIG)
    return ReplayService(repo, candidate_service, entry_service, monitoring_service, CONFIG)


class _DateTrackingCandidateService(CandidateService):
    def generate_candidates(self, as_of_date: date):
        self._adapter._inner.for_date(as_of_date)
        return super().generate_candidates(as_of_date)


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


def test_long_sequential_replay_across_30_plus_sessions(repo: SandboxRepository, monkeypatch):
    symbols = [f"SYM{i}" for i in range(5)]
    dates = _business_days(date(2026, 1, 5), 35)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-30d", dates, signal_end=dates[-1])

    result = service.run(replay, dates)

    assert len(result.dates_processed) == 35
    assert len(result.day_results) == 35
    assert all(r.is_signal_day for r in result.day_results)
    metadata = repo.get_replay_metadata("replay-30d")
    assert metadata.status == COMPLETED


def test_candidate_generation_stops_at_signal_end_date(repo: SandboxRepository, monkeypatch):
    symbols = [f"SYM{i}" for i in range(5)]
    dates = _business_days(date(2026, 1, 5), 20)
    signal_end = dates[9]  # first 10 dates are signal days, rest are outcome-only
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-cutoff", dates, signal_end=signal_end)

    result = service.run(replay, dates)

    signal_days = [r for r in result.day_results if r.is_signal_day]
    outcome_only_days = [r for r in result.day_results if not r.is_signal_day]
    assert len(signal_days) == 10
    assert len(outcome_only_days) == 10
    assert all(r.n_shadow_candidates == 0 for r in outcome_only_days)
    assert all(r.n_shadow_candidates == 5 for r in signal_days)
    # No candidate rows exist for any date after signal_end.
    for d in dates[10:]:
        assert repo.get_candidates_for_date(d) == []


def test_outcome_processing_continues_after_signal_end_date(repo: SandboxRepository, monkeypatch):
    symbols = [f"SYM{i}" for i in range(3)]
    dates = _business_days(date(2026, 1, 5), 25)
    signal_end = dates[4]
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-outcome-continues", dates, signal_end=signal_end)

    result = service.run(replay, dates)

    # Positions opened during the signal window are still being monitored on
    # outcome-only days (entries/monitoring keep running past signal_end_date).
    later_days = [r for r in result.day_results if r.as_of_date > signal_end]
    assert any(r.n_monitored > 0 for r in later_days)


def test_isolated_replay_databases_do_not_share_state(monkeypatch):
    conn_a = sqlite3.connect(":memory:")
    conn_a.row_factory = sqlite3.Row
    conn_a.execute("PRAGMA foreign_keys = ON")
    init_db(conn_a)
    repo_a = SandboxRepository(conn_a)

    conn_b = sqlite3.connect(":memory:")
    conn_b.row_factory = sqlite3.Row
    conn_b.execute("PRAGMA foreign_keys = ON")
    init_db(conn_b)
    repo_b = SandboxRepository(conn_b)

    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    service_a = _make_replay_service(repo_a, symbols, dates, monkeypatch)
    service_a.run(_replay_metadata("replay-a", dates, dates[-1]), dates)

    assert repo_a.get_replay_metadata("replay-a") is not None
    assert repo_b.get_replay_metadata("replay-a") is None  # isolated -- not visible in the other DB
    assert len(repo_a.get_candidates_for_date(dates[0])) == 1
    assert len(repo_b.get_candidates_for_date(dates[0])) == 0


def test_rerun_of_completed_replay_id_fails_clearly(repo: SandboxRepository, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-rerun", dates, dates[-1])

    service.run(replay, dates)

    with pytest.raises(ReplayAlreadyCompletedError):
        service.run(replay, dates)


def test_unresolved_positions_reported_at_outcome_end(repo: SandboxRepository, monkeypatch):
    symbols = [f"SYM{i}" for i in range(3)]
    # Only 10 trading days total -- not enough for a position opened near the end to
    # reach its 20-holding-day time exit before outcome_data_end_date.
    dates = _business_days(date(2026, 1, 5), 10)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-unresolved", dates, signal_end=dates[-1])

    result = service.run(replay, dates)

    assert len(result.unresolved_position_ids) > 0
    for position_id in result.unresolved_position_ids:
        position = repo.get_position(position_id)
        assert position.status == "OPEN"


def test_replay_metrics_funnel_and_counterfactual_counts(repo: SandboxRepository, monkeypatch):
    symbols = [f"SYM{i}" for i in range(5)]
    dates = _business_days(date(2026, 1, 5), 15)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-metrics", dates, signal_end=dates[-1])
    service.run(replay, dates)

    metrics = build_replay_metrics(repo, dates[0], dates[-1], dates[-1])

    assert metrics["funnel"]["shadow_candidates_total"] > 0
    assert metrics["funnel"]["actionable_candidates_total"] > 0
    assert metrics["funnel"]["entry_orders_created"] > 0
    assert metrics["funnel"]["positions_opened"] > 0
    assert metrics["candidate_selection"]["actionable_candidates_created"] == metrics["funnel"]["actionable_candidates_total"]
    assert isinstance(metrics["operational"]["max_simultaneous_open_positions"], int)
    assert metrics["operational"]["max_simultaneous_open_positions"] >= 1
