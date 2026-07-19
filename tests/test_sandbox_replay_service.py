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
    UntrustworthyResumeWatermarkError,
)
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.replay import (
    COMPLETED,
    DEVELOPMENT_HISTORICAL_REPLAY,
    FAILED,
    RUNNING,
    ReplayMetadata,
)
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.infrastructure.schema import connect, init_db
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
    """Reproduces the defect: a replay whose FIRST signal date genuinely completed
    (processed through ReplayService's own _process_dates, which advances the resume
    watermark on success -- exactly what a real crash-and-restart leaves behind) must
    resume successfully when called again with the original, full trading_dates list.
    Before the resume-conflict fix, CandidateService Phase 3 raised RuntimeError on
    ANY reprocessing of an already-persisted candidate; the watermark now avoids
    reprocessing dates[0] at all on resume (the stronger fix -- see
    test_interrupted_and_resumed_replay_matches_uninterrupted_replay for the case
    where a date genuinely IS reprocessed), verified here by confirming dates[0]'s
    candidates are untouched (same count, not duplicated or altered) after resume.

    A replay whose watermark is NULL but which already has persisted domain state
    (e.g. built by calling the sub-services directly, bypassing _process_dates
    entirely) is a DIFFERENT, deliberately unsafe scenario -- see
    test_resume_is_rejected_when_watermark_is_null_but_domain_state_exists."""

    symbols = [f"SYM{i}" for i in range(5)]
    dates = _business_days(date(2026, 1, 5), 10)
    service, candidates, entries, monitoring = _make_replay_components(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-partial-resume", dates, signal_end=dates[-1])

    repo.create_replay_metadata(replay)
    # Simulate a crash right after dates[0] finished: process it through the real
    # _process_dates (which advances last_completed_date on success, exactly like a
    # real run), then stop -- never call run() to completion.
    service._process_dates(replay, [dates[0]], progress_every=None)

    persisted_before_resume = len(repo.get_candidates_for_date(dates[0]))
    assert persisted_before_resume == len(symbols)
    assert repo.get_replay_metadata("replay-partial-resume").last_completed_date == dates[0]

    result = service.run(replay, dates)  # resume: the original, complete date list

    assert result.replay_id == "replay-partial-resume"
    stored = repo.get_replay_metadata("replay-partial-resume")
    assert stored.status == COMPLETED
    # dates[0] was skipped entirely on resume (already past the watermark), not
    # reprocessed -- so its candidates are exactly what the first pass produced.
    assert len(repo.get_candidates_for_date(dates[0])) == persisted_before_resume
    # Every date, including the already-processed one, ended up with candidates.
    for d in dates:
        assert len(repo.get_candidates_for_date(d)) == len(symbols)


def test_resume_is_rejected_when_watermark_is_null_but_domain_state_exists(repo: SandboxRepository, monkeypatch):
    """A RUNNING/FAILED replay with persisted domain state but NO resume watermark
    (last_completed_date is NULL) cannot be trusted to resume: there is no way to
    tell whether "NULL" means "genuinely nothing done yet" or "migrated from a
    schema version older than v3, which never had a watermark, while real work was
    in progress" (see infrastructure/schema.py's v1/v2/v3 history). This must fail
    closed with a specific, understandable exception rather than guessing -- and the
    rejection itself must not mutate anything (no fail_replay() call, no domain
    writes)."""

    symbols = [f"SYM{i}" for i in range(5)]
    dates = _business_days(date(2026, 1, 5), 10)
    service, candidates, entries, monitoring = _make_replay_components(repo, symbols, dates, monkeypatch)
    replay = _replay_metadata("replay-untrustworthy-watermark", dates, signal_end=dates[-1])

    repo.create_replay_metadata(replay)
    # Domain state exists (dates[0] fully processed), but NOT through _process_dates
    # -- so last_completed_date is never advanced. This is deliberately how a
    # database migrated from a pre-watermark schema (v1/v2) looks: real work
    # persisted, watermark column present but NULL.
    entries.process_entries(dates[0])
    monitoring.monitor(dates[0])
    candidates.generate_candidates(dates[0])

    candidates_before = repo.get_candidates_for_date(dates[0])
    status_before = repo.get_replay_metadata("replay-untrustworthy-watermark").status
    assert repo.get_replay_metadata("replay-untrustworthy-watermark").last_completed_date is None

    with pytest.raises(UntrustworthyResumeWatermarkError, match="watermark"):
        service.run(replay, dates)

    # The rejection did not mutate anything: no reprocessing, no fail_replay() call.
    stored_after = repo.get_replay_metadata("replay-untrustworthy-watermark")
    assert stored_after.status == status_before  # not flipped to FAILED by the rejection itself
    assert repo.get_candidates_for_date(dates[0]) == candidates_before


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


# ---------------------------------------------------------------------------------
# Legacy (migrated) database resume-safety: a v1 database (no last_completed_date at
# all) migrated up to v3 gets that column added as NULL for any pre-existing
# replay_metadata row. These tests build a REAL migrated database file (not just a
# fresh v3 database with the watermark manually nulled) to prove ReplayService
# handles each of the three legacy states correctly -- see
# UntrustworthyResumeWatermarkError in application/replay_service.py.
# ---------------------------------------------------------------------------------

_LEGACY_V1_DDL = """
CREATE TABLE IF NOT EXISTS sandbox_runs (
    run_id TEXT PRIMARY KEY, as_of_date TEXT NOT NULL, command TEXT NOT NULL,
    started_at TEXT NOT NULL, completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
    model_version TEXT, data_snapshot_id TEXT, code_commit_sha TEXT,
    configuration_hash TEXT NOT NULL, error_message TEXT
);
CREATE TABLE IF NOT EXISTS ranked_candidates (
    candidate_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES sandbox_runs(run_id),
    as_of_date TEXT NOT NULL, symbol TEXT NOT NULL, daily_rank INTEGER NOT NULL,
    model_score REAL NOT NULL, signal_close REAL NOT NULL, atr14 REAL, max_entry_price REAL,
    shadow_top10 INTEGER NOT NULL CHECK (shadow_top10 IN (0,1)),
    actionable INTEGER NOT NULL CHECK (actionable IN (0,1)),
    exclusion_reason TEXT, adv_quintile TEXT, market_regime TEXT, created_at TEXT NOT NULL,
    UNIQUE(symbol, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_ranked_candidates_as_of_date ON ranked_candidates(as_of_date);
CREATE TABLE IF NOT EXISTS entry_orders (
    order_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL UNIQUE REFERENCES ranked_candidates(candidate_id),
    symbol TEXT NOT NULL, signal_date TEXT NOT NULL, created_date TEXT NOT NULL,
    valid_until TEXT NOT NULL, max_entry_price REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','FILLED','EXPIRED','SKIPPED')),
    fill_date TEXT, fill_price REAL, fill_reason TEXT, no_fill_reason TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entry_orders_status ON entry_orders(status);
CREATE TABLE IF NOT EXISTS virtual_positions (
    position_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
    candidate_id TEXT NOT NULL REFERENCES ranked_candidates(candidate_id),
    order_id TEXT NOT NULL REFERENCES entry_orders(order_id), signal_date TEXT NOT NULL,
    entry_date TEXT NOT NULL, entry_price REAL NOT NULL, quantity REAL NOT NULL,
    initial_rank INTEGER NOT NULL, initial_model_score REAL NOT NULL, signal_close REAL NOT NULL,
    max_entry_price REAL NOT NULL, initial_adv_quintile TEXT, initial_market_regime TEXT,
    status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED')),
    current_holding_day_count INTEGER NOT NULL DEFAULT 0, current_close REAL,
    unrealized_return REAL, mfe REAL NOT NULL DEFAULT 0, mae REAL NOT NULL DEFAULT 0,
    target_price REAL NOT NULL, planned_time_exit_date TEXT NOT NULL, exit_date TEXT,
    exit_price REAL, exit_reason TEXT, realized_return REAL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, UNIQUE(symbol, entry_date)
);
CREATE INDEX IF NOT EXISTS idx_virtual_positions_status ON virtual_positions(status);
CREATE TABLE IF NOT EXISTS entry_order_attempts (
    attempt_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES entry_orders(order_id),
    symbol TEXT NOT NULL, attempt_date TEXT NOT NULL, session_open REAL, session_high REAL,
    session_low REAL, session_close REAL, max_entry_price REAL NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('FILLED_AT_OPEN','FILLED_AT_CEILING','NO_FILL')),
    fill_price REAL, reason TEXT, created_at TEXT NOT NULL, UNIQUE(order_id, attempt_date)
);
CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_id TEXT PRIMARY KEY, position_id TEXT NOT NULL REFERENCES virtual_positions(position_id),
    symbol TEXT NOT NULL, as_of_date TEXT NOT NULL, close_price REAL, daily_return REAL,
    cumulative_unrealized_return REAL, holding_day_count INTEGER NOT NULL, mfe REAL NOT NULL,
    mae REAL NOT NULL, distance_to_target REAL, current_rank INTEGER, current_model_score REAL,
    rank_change_from_entry INTEGER, current_adv_quintile TEXT, current_market_regime TEXT,
    data_quality_status TEXT NOT NULL, recommendation TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(position_id, as_of_date)
);
CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('candidate','position')),
    entity_id TEXT NOT NULL, symbol TEXT NOT NULL, as_of_date TEXT NOT NULL,
    recommendation TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL,
    UNIQUE(entity_type, entity_id, as_of_date)
);
CREATE TABLE IF NOT EXISTS virtual_transactions (
    transaction_id TEXT PRIMARY KEY, position_id TEXT NOT NULL REFERENCES virtual_positions(position_id),
    symbol TEXT NOT NULL, transaction_type TEXT NOT NULL CHECK (transaction_type IN ('BUY','SELL')),
    transaction_date TEXT NOT NULL, price REAL NOT NULL, quantity REAL NOT NULL,
    notional REAL NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(position_id, transaction_type, transaction_date)
);
CREATE TABLE IF NOT EXISTS data_quality_events (
    event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, as_of_date TEXT NOT NULL,
    event_type TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL,
    UNIQUE(symbol, as_of_date, event_type)
);
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS replay_metadata (
    replay_id TEXT PRIMARY KEY, classification TEXT NOT NULL, code_commit_sha TEXT,
    model_version TEXT, feature_snapshot_id TEXT, market_data_snapshot_id TEXT,
    signal_start_date TEXT NOT NULL, signal_end_date TEXT NOT NULL,
    outcome_data_end_date TEXT NOT NULL, configuration_json TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
    started_at TEXT NOT NULL, completed_at TEXT
);
"""


def _build_legacy_v1_replay_fixture(
    db_path: str, replay: ReplayMetadata, *, status: str, with_domain_state: bool
) -> None:
    """A physical v1 database (no last_completed_date column at all -- predates the
    watermark entirely) containing one replay_metadata row matching `replay`'s
    identity fields exactly, and optionally one persisted candidate (simulating
    "this legacy replay already had work in progress when it was migrated")."""

    conn = sqlite3.connect(db_path)
    conn.executescript(_LEGACY_V1_DDL)
    conn.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')")
    conn.execute(
        "INSERT INTO replay_metadata (replay_id, classification, code_commit_sha, model_version, "
        " feature_snapshot_id, market_data_snapshot_id, signal_start_date, signal_end_date, "
        " outcome_data_end_date, configuration_json, configuration_hash, status, started_at, "
        " completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            replay.replay_id, replay.classification, replay.code_commit_sha, replay.model_version,
            replay.feature_snapshot_id, replay.market_data_snapshot_id,
            replay.signal_start_date.isoformat(), replay.signal_end_date.isoformat(),
            replay.outcome_data_end_date.isoformat(), replay.configuration_json,
            replay.configuration_hash, status, replay.started_at.isoformat(),
            replay.started_at.isoformat() if status == COMPLETED else None,
        ),
    )
    if with_domain_state:
        conn.execute(
            "INSERT INTO sandbox_runs (run_id, as_of_date, command, started_at, completed_at, "
            " status, model_version, data_snapshot_id, code_commit_sha, configuration_hash, "
            " error_message) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("run-legacy", replay.signal_start_date.isoformat(), "generate-candidates",
             replay.started_at.isoformat(), None, "COMPLETED", "v1", None, None, "hash", None),
        )
        conn.execute(
            "INSERT INTO ranked_candidates (candidate_id, run_id, as_of_date, symbol, daily_rank, "
            " model_score, signal_close, atr14, max_entry_price, shadow_top10, actionable, "
            " exclusion_reason, adv_quintile, market_regime, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                RankedCandidate.make_id(replay.signal_start_date, "AAA"), "run-legacy",
                replay.signal_start_date.isoformat(), "AAA", 1, 5.0, 100.0, 2.0, 101.0, 1, 1,
                None, "adv_q3", "Bull_Normal", replay.started_at.isoformat(),
            ),
        )
    conn.commit()
    conn.close()


def test_migrated_completed_legacy_replay_rerun_still_rejected(tmp_path, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    replay = _replay_metadata("replay-legacy-completed", dates, dates[-1])

    db_path = str(tmp_path / "legacy_completed.db")
    _build_legacy_v1_replay_fixture(db_path, replay, status=COMPLETED, with_domain_state=True)

    conn = connect(db_path)  # migrates v1 -> v3
    repo = SandboxRepository(conn)
    service = _make_replay_service(repo, symbols, dates, monkeypatch)

    # A COMPLETED replay is rejected regardless of its (NULL) watermark -- it can
    # never be resumed at all, migrated or not.
    with pytest.raises(ReplayAlreadyCompletedError):
        service.run(replay, dates)

    conn.close()


def test_migrated_running_legacy_replay_with_no_domain_state_resumes_from_start(tmp_path, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    replay = _replay_metadata("replay-legacy-empty", dates, dates[-1])

    db_path = str(tmp_path / "legacy_empty.db")
    _build_legacy_v1_replay_fixture(db_path, replay, status=RUNNING, with_domain_state=False)

    conn = connect(db_path)  # migrates v1 -> v3; last_completed_date is NULL
    repo = SandboxRepository(conn)
    assert repo.get_replay_metadata("replay-legacy-empty").last_completed_date is None
    assert repo.has_any_domain_state() is False
    service = _make_replay_service(repo, symbols, dates, monkeypatch)

    # NULL watermark + genuinely no domain state -- this is the ordinary "died before
    # finishing its first date" case, safe to reprocess the full list from scratch.
    result = service.run(replay, dates)

    assert repo.get_replay_metadata("replay-legacy-empty").status == COMPLETED
    assert len(result.dates_processed) == len(dates)

    conn.close()


def test_migrated_failed_legacy_replay_with_domain_state_rejects_resume(tmp_path, monkeypatch):
    symbols = ["AAA"]
    dates = _business_days(date(2026, 1, 5), 5)
    replay = _replay_metadata("replay-legacy-partial", dates, dates[-1])

    db_path = str(tmp_path / "legacy_partial.db")
    _build_legacy_v1_replay_fixture(db_path, replay, status=FAILED, with_domain_state=True)

    conn = connect(db_path)  # migrates v1 -> v3; last_completed_date is NULL
    repo = SandboxRepository(conn)
    assert repo.get_replay_metadata("replay-legacy-partial").last_completed_date is None
    assert repo.has_any_domain_state() is True
    candidates_before = repo.get_candidates_for_date(dates[0])
    service = _make_replay_service(repo, symbols, dates, monkeypatch)

    with pytest.raises(UntrustworthyResumeWatermarkError, match="watermark"):
        service.run(replay, dates)

    # Rejection did not mutate anything.
    stored_after = repo.get_replay_metadata("replay-legacy-partial")
    assert stored_after.status == FAILED  # not further altered by the rejection
    assert repo.get_candidates_for_date(dates[0]) == candidates_before

    conn.close()
