from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

import stock_analyzer.sandbox.application.candidate_service as candidate_service_module
from stock_analyzer.sandbox.application.candidate_service import CandidateService
from stock_analyzer.sandbox.application.entry_service import EntryService
from stock_analyzer.sandbox.application.monitoring_service import MonitoringService
from stock_analyzer.sandbox.application.replay_service import (
    ReplayAlreadyCompletedError,
    ReplayConfigurationMismatchError,
    ReplayInputError,
    ReplayService,
)
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.replay import COMPLETED, DEVELOPMENT_HISTORICAL_REPLAY, ReplayMetadata
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import RankedCandidateConflictError, SandboxRepository
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


def _make_replay_components(
    repo: SandboxRepository, symbols: list[str], trading_dates: list[date], monkeypatch
) -> tuple[ReplayService, CandidateService, EntryService, MonitoringService]:
    """Like _make_replay_service, but also returns the underlying sub-services so a
    test can call them directly to simulate a partially-processed signal date (i.e. a
    crash) before resuming through the public ReplayService.run() path."""

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
    replay_service = ReplayService(repo, candidate_service, entry_service, monitoring_service, CONFIG)
    return replay_service, candidate_service, entry_service, monitoring_service


def _make_replay_service(repo: SandboxRepository, symbols: list[str], trading_dates: list[date], monkeypatch) -> ReplayService:
    service, _candidates, _entries, _monitoring = _make_replay_components(repo, symbols, trading_dates, monkeypatch)
    return service


_DOMAIN_TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    # table -> (primary key column, non-deterministic columns to exclude)
    "sandbox_runs": ("run_id", ("started_at", "completed_at")),
    "ranked_candidates": ("candidate_id", ("created_at",)),
    "entry_orders": ("order_id", ("created_at", "updated_at")),
    "entry_order_attempts": ("attempt_id", ("created_at",)),
    "virtual_positions": ("position_id", ("created_at", "updated_at")),
    "position_snapshots": ("snapshot_id", ("created_at",)),
    "recommendations": ("recommendation_id", ("created_at",)),
    "virtual_transactions": ("transaction_id", ("created_at",)),
    "data_quality_events": ("event_id", ("created_at",)),
}


def _dump_domain_state(repo: SandboxRepository) -> dict[str, list[dict]]:
    """Dumps every persisted domain row (excluding non-deterministic timestamp
    columns), keyed by table -> rows sorted by primary key. Used to assert that an
    interrupted-then-resumed replay persists byte-for-byte the same domain content as
    an uninterrupted run of the identical configuration."""

    conn = repo.connection
    dump: dict[str, list[dict]] = {}
    for table, (pk, exclude_cols) in _DOMAIN_TABLES.items():
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {pk}").fetchall()
        dump[table] = [{k: v for k, v in dict(r).items() if k not in exclude_cols} for r in rows]
    return dump


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


def test_unsorted_trading_dates_are_rejected(repo: SandboxRepository, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-unsorted", dates, dates[-1])

    unsorted_dates = [dates[1], dates[0], dates[2], dates[3], dates[4]]
    with pytest.raises(ReplayInputError, match="sorted"):
        service.run(replay, unsorted_dates)


def test_duplicate_trading_dates_are_rejected(repo: SandboxRepository, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-dupes", dates, dates[-1])

    dates_with_dupe = dates[:3] + [dates[2]] + dates[3:]
    with pytest.raises(ReplayInputError, match="duplicate"):
        service.run(replay, dates_with_dupe)


def test_trading_dates_before_signal_start_are_rejected(repo: SandboxRepository, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-early", dates, dates[-1])
    replay.signal_start_date = dates[1]  # first registered date is dates[1], not dates[0]

    with pytest.raises(ReplayInputError, match="signal_start_date"):
        service.run(replay, dates)  # dates[0] is before the registered signal_start_date


def test_trading_dates_after_outcome_end_are_rejected(repo: SandboxRepository, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-late", dates, dates[-1])
    replay.outcome_data_end_date = dates[-2]  # registered end is before the last supplied date

    with pytest.raises(ReplayInputError, match="outcome_data_end_date"):
        service.run(replay, dates)


def test_resume_with_mismatched_configuration_is_rejected(repo: SandboxRepository, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)

    # Register a RUNNING replay directly (simulating a crash before completion),
    # then attempt to resume with a different configuration_hash.
    original = _replay_metadata("replay-resume", dates, dates[-1])
    repo.create_replay_metadata(original)

    changed = _replay_metadata("replay-resume", dates, dates[-1])
    changed.configuration_hash = "a-different-hash"

    with pytest.raises(ReplayConfigurationMismatchError, match="configuration_hash"):
        service.run(changed, dates)


def test_resume_with_matching_configuration_proceeds(repo: SandboxRepository, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)

    original = _replay_metadata("replay-resume-ok", dates, dates[-1])
    repo.create_replay_metadata(original)

    same_config = _replay_metadata("replay-resume-ok", dates, dates[-1])
    result = service.run(same_config, dates)

    assert result.replay_id == "replay-resume-ok"
    metadata = repo.get_replay_metadata("replay-resume-ok")
    assert metadata.status == COMPLETED


def test_exception_during_replay_marks_metadata_failed(repo: SandboxRepository, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-crash", dates, dates[-1])

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash mid-replay")

    monkeypatch.setattr(service, "_process_dates", boom)

    with pytest.raises(RuntimeError, match="simulated crash"):
        service.run(replay, dates)

    metadata = repo.get_replay_metadata("replay-crash")
    assert metadata.status == "FAILED"


def test_resume_after_genuine_partial_signal_day_succeeds(repo: SandboxRepository, monkeypatch):
    """Reproduces the defect: a replay whose FIRST signal date was genuinely
    processed and persisted (not just a RUNNING metadata row) must resume
    successfully when called again with the original, full trading_dates list --
    exactly what a real crash-and-restart looks like. Before the fix, CandidateService
    Phase 3 raised RuntimeError on the resumed date's re-insertion of identical
    candidates, and that exception then marked the replay FAILED.

    The "crash" is simulated realistically: dates[0] is processed through
    ReplayService's own _process_dates (which sets the resume watermark on success,
    exactly as a real run would), then dates[0] is processed a SECOND time directly
    through the sub-services -- mimicking a process that died after redoing some of
    dates[0]'s work a second time before the watermark-setting run ever returned
    (e.g. retried locally, or the prior attempt's own watermark write never
    committed). This is the realistic worst case for the boundary date: its own
    persistence must be idempotent even under direct re-invocation, not just under
    ReplayService's watermark skip."""

    symbols = [f"SYM{i}" for i in range(5)]
    dates = _business_days(date(2026, 1, 5), 10)
    service, candidates, entries, monitoring = _make_replay_components(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-partial-resume", dates, signal_end=dates[-1])

    repo.create_replay_metadata(replay)
    entries.process_entries(dates[0])
    monitoring.monitor(dates[0])
    candidates.generate_candidates(dates[0])

    persisted_before_resume = len(repo.get_candidates_for_date(dates[0]))
    assert persisted_before_resume == len(symbols)

    result = service.run(replay, dates)  # resume: the original, complete date list

    assert result.replay_id == "replay-partial-resume"
    stored = repo.get_replay_metadata("replay-partial-resume")
    assert stored.status == COMPLETED
    # dates[0]'s candidates were reprocessed idempotently, not duplicated.
    assert len(repo.get_candidates_for_date(dates[0])) == persisted_before_resume
    # Every date, including the already-processed one, ended up with candidates.
    for d in dates:
        assert len(repo.get_candidates_for_date(d)) == len(symbols)


def test_interrupted_and_resumed_replay_matches_uninterrupted_replay(monkeypatch):
    """The strongest resume-safety guarantee: an interrupted-then-resumed replay must
    persist EXACTLY the same domain state (candidates, orders, attempts, positions,
    snapshots, recommendations, transactions -- excluding only non-deterministic
    timestamp columns) as an uninterrupted run of the identical configuration.

    The "crash" is simulated realistically, matching what a real process death looks
    like: dates before the boundary are processed through ReplayService's own
    _process_dates (which advances the resume watermark after each date's full
    processing succeeds, exactly like a real run), and the boundary date itself is
    then processed once more DIRECTLY through the sub-services -- simulating a crash
    that happens after the boundary date's own work (including candidate generation,
    whose Phase 4 creates entry orders) but before _process_dates gets to mark it
    complete. Resuming must then redo only that one boundary date and reach the exact
    same final state as an uninterrupted run."""

    symbols = [f"SYM{i}" for i in range(4)]
    dates = _business_days(date(2026, 1, 5), 12)
    interrupt_at = 5  # index into `dates` where the "crash" happens, mid-processing

    conn_a = sqlite3.connect(":memory:")
    conn_a.row_factory = sqlite3.Row
    conn_a.execute("PRAGMA foreign_keys = ON")
    init_db(conn_a)
    repo_a = SandboxRepository(conn_a)
    service_a = _make_replay_service(repo_a, symbols, dates, monkeypatch)
    replay_a = _replay_metadata("replay-compare", dates, dates[-1])
    service_a.run(replay_a, dates)

    conn_b = sqlite3.connect(":memory:")
    conn_b.row_factory = sqlite3.Row
    conn_b.execute("PRAGMA foreign_keys = ON")
    init_db(conn_b)
    repo_b = SandboxRepository(conn_b)
    service_b, candidates_b, entries_b, monitoring_b = _make_replay_components(repo_b, symbols, dates, monkeypatch)
    replay_b = _replay_metadata("replay-compare", dates, dates[-1])

    repo_b.create_replay_metadata(replay_b)
    # Realistically process the prefix through ReplayService itself, so the resume
    # watermark (last_completed_date) is set exactly as a real run would leave it.
    service_b._process_dates(replay_b, dates[:interrupt_at], progress_every=None)
    # Now simulate the crash: dates[interrupt_at] gets processed once, directly
    # through the sub-services (bypassing _process_dates, so last_completed_date is
    # NOT advanced past dates[interrupt_at - 1]) -- exactly the state left behind by
    # a process that died after finishing this date's substantive work but before
    # _process_dates recorded it as complete.
    entries_b.process_entries(dates[interrupt_at])
    monitoring_b.monitor(dates[interrupt_at])
    candidates_b.generate_candidates(dates[interrupt_at])

    assert repo_b.get_replay_metadata("replay-compare").last_completed_date == dates[interrupt_at - 1]

    service_b.run(replay_b, dates)  # resume: the original, complete date list

    assert repo_b.get_replay_metadata("replay-compare").status == COMPLETED
    assert _dump_domain_state(repo_a) == _dump_domain_state(repo_b)


def test_conflicting_ranked_candidate_content_is_rejected(repo: SandboxRepository):
    """A genuine conflict -- the same candidate_id (same as_of_date + symbol)
    produced DIFFERENT content across two inserts -- must fail loudly, not be
    silently accepted as if it were a safe resume."""

    as_of = date(2026, 1, 5)
    run = SandboxRun(
        run_id=SandboxRun.make_id(as_of, "generate-candidates"),
        as_of_date=as_of,
        command="generate-candidates",
        started_at=datetime.now(timezone.utc),
        configuration_hash="hash",
        model_version="v",
    )
    repo.create_run(run)

    original = RankedCandidate(
        candidate_id=RankedCandidate.make_id(as_of, "AAA"),
        run_id=run.run_id,
        as_of_date=as_of,
        symbol="AAA",
        daily_rank=1,
        model_score=1.23,
        signal_close=100.0,
        atr14=2.0,
        max_entry_price=101.0,
        shadow_top10=True,
        actionable=True,
        exclusion_reason=None,
        adv_quintile="adv_q3",
        market_regime="Bull_Normal",
    )
    assert repo.insert_ranked_candidate(original) is True

    conflicting = replace(original, model_score=9.99)
    with pytest.raises(RankedCandidateConflictError):
        repo.insert_ranked_candidate(conflicting)

    # An IDENTICAL repeat -- the actual resume case -- is a safe no-op, not an error.
    assert repo.insert_ranked_candidate(replace(original)) is False
