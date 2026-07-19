"""Historical Sandbox Replay: sequential, day-by-day replay of the full sandbox
lifecycle using the SAME application services and orchestration path as a normal
daily sandbox run -- no vectorized shortcut that bypasses candidate persistence,
pending entry orders, entry processing, recommendation events, position snapshots,
transaction events, or idempotency rules.

Per date T, in order:
    1. process pending orders using date T's own OHLC;
    2. monitor positions that were already open before date T;
    3. (exits are executed as part of monitoring itself, same as daily_run_service);
    4. if T <= signal_end_date: run frozen Model 2 inference, persist the date T
       shadow top-10, select at most 3 actionable candidates, create entry orders
       that cannot execute before the next observed trading session.
    5. if T > signal_end_date: no new candidates are generated -- T is an
       "outcome-only" day, existing only to let already-open positions and pending
       orders finish their lifecycle.

No future date may influence a decision made on date T: each day's processing reads
only repository state already committed from prior days, and price data is always
truncated to <= T by market_data_adapter.fetch_as_of.

Resume semantics
-----------------
Calling run() again on a replay_id that is RUNNING or FAILED (rather than COMPLETED)
is a resume, not a rerun. The contract:
  - The caller always supplies the ORIGINAL, complete trading_dates list -- the same
    one used (or intended) for the first attempt. ReplayService decides internally
    what to skip.
  - Every date up to and including replay_metadata.last_completed_date already
    committed its FULL processing (entries + monitoring + candidate generation, if a
    signal day) in a prior attempt and is skipped entirely -- it is never
    reprocessed, so it can never "see" state from a date that, from its own point in
    time, had not happened yet (see _migrate_v1_to_v2 / mark_date_completed).
  - The one date strictly after last_completed_date (if any work happened on it
    before the prior attempt stopped) is reprocessed from scratch. Every write in
    that reprocessing must be idempotent: an identical pre-existing row (same
    candidate_id/order_id/position_id/etc, same content) is a safe no-op; a
    conflicting pre-existing row (same id, different content) must raise loudly
    rather than silently diverge from what is already persisted -- see
    SandboxRepository.insert_ranked_candidate and RankedCandidateConflictError.
  - A configuration mismatch (different code/model/data/date-boundaries) is rejected
    outright by _require_matching_configuration -- resume never continues a different
    configuration into a partially-populated database.
  - A NULL last_completed_date (no date has completed a full processing cycle since
    this replay_metadata row was created) is normally the ordinary state of a fresh
    RUNNING replay that died before finishing its first date -- safe to reprocess the
    full trading_dates list from the start. But a database migrated up from a schema
    version older than v3 (see infrastructure/schema.py) also has a NULL
    last_completed_date on its (possibly not actually empty) replay_metadata row,
    since the watermark simply did not exist before v3. There is no way to
    distinguish "genuinely nothing done yet" from "migrated from a pre-watermark
    schema with real, partially-done work" by the watermark alone, so resume checks
    whether ANY domain table already has rows (has_any_domain_state()): if so, resume
    is refused outright (UntrustworthyResumeWatermarkError) rather than guessed at
    (e.g. via MAX(as_of_date), which is unsafe because the maximum persisted date
    could itself be an incompletely-processed boundary day). The only correct
    recovery is a new replay_id with a fresh isolated database.
  - The end result of an interrupted-then-resumed replay must be identical (modulo
    non-deterministic timestamp columns) to an uninterrupted run of the same
    configuration -- see tests/test_sandbox_replay_service.py's comparison test.

See docs/09_experiments/EXP-004_Sandbox_Historical_Replay.md for the pre-registration
and classification (DEVELOPMENT_HISTORICAL_REPLAY -- NOT INDEPENDENT MODEL VALIDATION
-- NOT FOR POLICY OPTIMIZATION).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from stock_analyzer.sandbox.application.candidate_service import CandidateService
from stock_analyzer.sandbox.application.entry_service import EntryService
from stock_analyzer.sandbox.application.monitoring_service import MonitoringService
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.replay import COMPLETED, ReplayMetadata
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

# Fields that identify "the same replay configuration" -- compared on resume so a
# RUNNING/FAILED replay cannot silently continue under changed code/config/data.
# Excludes started_at/completed_at/status (expected to differ) and replay_id (the key
# itself).
_IDENTITY_FIELDS = (
    "classification",
    "code_commit_sha",
    "model_version",
    "feature_snapshot_id",
    "market_data_snapshot_id",
    "signal_start_date",
    "signal_end_date",
    "outcome_data_end_date",
    "configuration_hash",
)


class ReplayAlreadyCompletedError(RuntimeError):
    """Raised when a replay_id that already completed is run again. Per spec: a
    repeated replay configuration must either verify identical results or fail
    clearly -- this implementation fails clearly rather than silently re-running or
    silently skipping."""


class ReplayConfigurationMismatchError(RuntimeError):
    """Raised when resuming a RUNNING/FAILED replay_id with a configuration that
    differs from what was originally registered -- e.g. a retry after changing code,
    the model, a feature/data snapshot, or the date boundaries. Resuming into a
    partially-populated database under a different configuration would silently mix
    two configurations' data under one replay_id."""


class ReplayInputError(ValueError):
    """Raised when the caller-supplied trading_dates do not satisfy ReplayService.run's
    documented contract (sorted, unique, within the registered period)."""


class UntrustworthyResumeWatermarkError(RuntimeError):
    """Raised when resuming a RUNNING/FAILED replay that already has persisted
    domain state but no trustworthy resume watermark (last_completed_date is NULL).
    This happens when a replay's database was migrated from a schema version that
    predates the watermark (v1 or v2 -- see infrastructure/schema.py) while it still
    had unfinished work in progress. Guessing a watermark (e.g. from
    MAX(as_of_date) across the domain tables) cannot be proven safe: that date might
    itself be a partially-processed boundary day, not a fully committed one -- the
    exact point-in-time contamination the watermark exists to prevent (see
    test_interrupted_and_resumed_replay_matches_uninterrupted_replay for what goes
    wrong without it). The correct recovery is to start a new replay under a new
    replay_id, in practice with a fresh isolated database file -- not to resume this
    one. Deliberately raised OUTSIDE the try/except around _process_dates, so
    rejecting a resume this way does not call fail_replay() and mark an otherwise
    untouched replay's status as freshly failed by this attempt."""


@dataclass
class ReplayDayResult:
    as_of_date: date
    is_signal_day: bool
    n_entries_processed: int
    n_filled: int
    n_monitored: int
    n_shadow_candidates: int


@dataclass
class ReplayRunResult:
    replay_id: str
    dates_processed: list[date]
    day_results: list[ReplayDayResult]
    unresolved_position_ids: list[str]


class ReplayService:
    def __init__(
        self,
        repository: SandboxRepository,
        candidate_service: CandidateService,
        entry_service: EntryService,
        monitoring_service: MonitoringService,
        config: SandboxConfig | None = None,
    ) -> None:
        self._repo = repository
        self._candidates = candidate_service
        self._entries = entry_service
        self._monitoring = monitoring_service
        self._config = config or SandboxConfig()

    def run(self, replay: ReplayMetadata, trading_dates: list[date], progress_every: int | None = None) -> ReplayRunResult:
        """`trading_dates` must be sorted ascending, unique, and fall within
        [replay.signal_start_date, replay.outcome_data_end_date]. Candidate generation
        only happens for dates <= replay.signal_end_date."""

        self._validate_trading_dates(replay, trading_dates)

        stored, created = self._repo.create_replay_metadata(replay)
        resume_from: date | None = None
        if not created:
            if stored.status == COMPLETED:
                raise ReplayAlreadyCompletedError(
                    f"Replay '{replay.replay_id}' already completed at {stored.completed_at}. "
                    "Use a new replay_id for a different configuration, or delete this replay's "
                    "isolated database to genuinely rerun it."
                )
            # RUNNING or FAILED: only resume if the supplied configuration matches
            # what was originally registered -- otherwise a retry after changing
            # code/model/data could silently continue into a database populated
            # under a different configuration.
            self._require_matching_configuration(replay, stored)
            resume_from = stored.last_completed_date
            if resume_from is None and self._repo.has_any_domain_state():
                # A NULL watermark normally means "nothing has completed yet" (a
                # fresh RUNNING replay whose first date is still in progress) -- safe
                # to reprocess the full list from the start. But a database migrated
                # from a pre-watermark schema (v1/v2) also has a NULL
                # last_completed_date even if it already has real, partially-done
                # work persisted. There is no way to distinguish the two cases from
                # the watermark alone, and guessing (e.g. MAX(as_of_date)) is exactly
                # the point-in-time risk the watermark exists to prevent -- so refuse
                # outright rather than reprocess history that might contaminate an
                # earlier date's view with later-dated state.
                raise UntrustworthyResumeWatermarkError(
                    f"Replay '{replay.replay_id}' (status={stored.status}) has no resume "
                    "watermark (last_completed_date is NULL) but already has persisted domain "
                    "state -- likely a database migrated from a schema version that predates "
                    "the watermark while work was still in progress. Resuming it would require "
                    "guessing which dates already completed, which cannot be proven safe. "
                    "Start a new replay under a new replay_id (with a fresh isolated database) "
                    "instead of resuming this one."
                )
            # Resume watermark: every date up to and including last_completed_date
            # committed its FULL processing before the prior attempt stopped (crashed
            # or failed) -- skip those entirely and only reprocess the date after it
            # onward. This is what makes resume safe: the one date that may have been
            # left partially processed is redone from scratch (each service's own
            # idempotency handles that), but no already-completed date is ever
            # touched again, so it can never "see" state from dates that, from its
            # own point in time, had not happened yet.

        try:
            day_results = self._process_dates(replay, trading_dates, progress_every, resume_from)
        except Exception:
            self._repo.fail_replay(replay.replay_id, datetime.now(timezone.utc))
            raise

        unresolved = [p.position_id for p in self._repo.get_open_positions()]
        self._repo.complete_replay(replay.replay_id, datetime.now(timezone.utc))

        return ReplayRunResult(
            replay_id=replay.replay_id,
            dates_processed=[r.as_of_date for r in day_results],
            day_results=day_results,
            unresolved_position_ids=unresolved,
        )

    def _process_dates(
        self,
        replay: ReplayMetadata,
        trading_dates: list[date],
        progress_every: int | None,
        resume_from: date | None = None,
    ) -> list[ReplayDayResult]:
        dates_to_process = [d for d in trading_dates if resume_from is None or d > resume_from]

        day_results: list[ReplayDayResult] = []
        for position, as_of_date in enumerate(dates_to_process, start=1):
            entry_outcomes = self._entries.process_entries(as_of_date)
            monitoring_outcomes = self._monitoring.monitor(as_of_date)

            is_signal_day = as_of_date <= replay.signal_end_date
            n_shadow = 0
            if is_signal_day:
                candidate_result = self._candidates.generate_candidates(as_of_date)
                n_shadow = len(candidate_result.shadow_top10)

            day_results.append(
                ReplayDayResult(
                    as_of_date=as_of_date,
                    is_signal_day=is_signal_day,
                    n_entries_processed=len(entry_outcomes),
                    n_filled=len([o for o in entry_outcomes if o.outcome == "FILLED"]),
                    n_monitored=len(monitoring_outcomes),
                    n_shadow_candidates=n_shadow,
                )
            )
            self._repo.mark_date_completed(replay.replay_id, as_of_date)

            if progress_every and (position % progress_every == 0 or position == len(dates_to_process)):
                print(
                    f"[replay] {position}/{len(dates_to_process)} dates -- {as_of_date} "
                    f"(signal_day={is_signal_day})",
                    flush=True,
                )
        return day_results

    @staticmethod
    def _validate_trading_dates(replay: ReplayMetadata, trading_dates: list[date]) -> None:
        if not trading_dates:
            raise ReplayInputError("trading_dates must not be empty.")
        if len(trading_dates) != len(set(trading_dates)):
            raise ReplayInputError("trading_dates contains duplicate dates.")
        if trading_dates != sorted(trading_dates):
            raise ReplayInputError("trading_dates must be sorted ascending.")
        if trading_dates[0] < replay.signal_start_date:
            raise ReplayInputError(
                f"trading_dates[0]={trading_dates[0]} is before signal_start_date={replay.signal_start_date}."
            )
        if trading_dates[-1] > replay.outcome_data_end_date:
            raise ReplayInputError(
                f"trading_dates[-1]={trading_dates[-1]} is after outcome_data_end_date={replay.outcome_data_end_date}."
            )

    @staticmethod
    def _require_matching_configuration(replay: ReplayMetadata, stored: ReplayMetadata) -> None:
        mismatches = {
            field: (getattr(stored, field), getattr(replay, field))
            for field in _IDENTITY_FIELDS
            if getattr(stored, field) != getattr(replay, field)
        }
        if mismatches:
            raise ReplayConfigurationMismatchError(
                f"Resuming replay '{replay.replay_id}' (status={stored.status}) with a configuration "
                f"that differs from the originally registered one: {mismatches}. Use a new replay_id "
                "for a genuinely different configuration, or delete this replay's isolated database to "
                "start over with the new configuration under the same id."
            )
