from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

import stock_analyzer.sandbox.application.candidate_service as candidate_service_module
from stock_analyzer.sandbox.application.candidate_service import CandidateService
from stock_analyzer.sandbox.application.daily_run_service import DailyRunService
from stock_analyzer.sandbox.application.entry_service import EntryService
from stock_analyzer.sandbox.application.monitoring_service import MonitoringService
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

CONFIG = SandboxConfig()
AS_OF = date(2026, 6, 15)


class FakePredictionAdapter:
    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores
        self.model_version = "fake-model-for-tests"
        self.fit_params = {
            "adv_edges": np.array([-np.inf, 15.0, 17.0, 19.0, 21.0, np.inf]),
            "adv_labels": ["adv_q1", "adv_q2", "adv_q3", "adv_q4", "adv_q5"],
        }

    def score(self, features_df: pd.DataFrame) -> pd.Series:
        return pd.Series([self._scores.get(sym, 0.0) for sym in features_df.index], index=features_df.index)


class FakeUniverseProvider:
    def __init__(self, frames_by_date: dict[date, pd.DataFrame]) -> None:
        self._frames = frames_by_date

    def features_for_date(self, as_of_date: date) -> pd.DataFrame:
        return self._frames.get(as_of_date, pd.DataFrame()).copy()


def _universe_frame(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "adv20": [20_000_000.0] * len(symbols),
            "rvol_20": [1.0] * len(symbols),
            "rsi_14": [50.0] * len(symbols),
            "spy_trend": ["Bull"] * len(symbols),
            "spy_volatility_bucket": ["Normal"] * len(symbols),
        },
        index=pd.Index(symbols, name="symbol"),
    )


def _synthetic_prices(as_of_date: date, days: int = 30, close: float = 100.0) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp(as_of_date), periods=days)
    closes = [close + i * 0.1 for i in range(days)]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
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


def _make_daily_run_service(repo: SandboxRepository, symbols: list[str], scores: dict[str, float], monkeypatch) -> DailyRunService:
    def fake_fetch_as_of(symbol: str, fetch_date: date, period: str = "2y") -> pd.DataFrame:
        return _synthetic_prices(fetch_date)

    monkeypatch.setattr(candidate_service_module, "fetch_as_of", fake_fetch_as_of)

    universe = FakeUniverseProvider({AS_OF: _universe_frame(symbols)})
    adapter = FakePredictionAdapter(scores)
    candidate_service = CandidateService(repo, adapter, universe, CONFIG)
    entry_service = EntryService(repo, CONFIG)
    monitoring_service = MonitoringService(repo, CONFIG)
    return DailyRunService(repo, candidate_service, entry_service, monitoring_service, CONFIG)


def test_daily_run_generates_candidates_and_completes(repo: SandboxRepository, monkeypatch):
    symbols = [f"SYM{i}" for i in range(5)]
    scores = {sym: float(5 - i) for i, sym in enumerate(symbols)}
    service = _make_daily_run_service(repo, symbols, scores, monkeypatch)

    result = service.run(AS_OF)

    assert result.already_completed is False
    assert len(result.candidate_result.shadow_top10) == 5
    run = repo.get_run(result.run_id)
    assert run.status == "COMPLETED"


def test_daily_run_is_idempotent_for_same_date(repo: SandboxRepository, monkeypatch):
    symbols = [f"SYM{i}" for i in range(5)]
    scores = {sym: float(5 - i) for i, sym in enumerate(symbols)}
    service = _make_daily_run_service(repo, symbols, scores, monkeypatch)

    first = service.run(AS_OF)
    second = service.run(AS_OF)

    assert first.already_completed is False
    assert second.already_completed is True
    # No duplicate candidates from the second (skipped) run.
    assert len(repo.get_candidates_for_date(AS_OF)) == 5
