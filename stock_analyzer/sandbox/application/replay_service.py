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

        try:
            day_results = self._process_dates(replay, trading_dates, progress_every)
        except Exception:
            self._repo.fail_replay(replay.replay_id, datetime.now(timezone.utc))
            raise

        unresolved = [p.position_id for p in self._repo.get_open_positions()]
        self._repo.complete_replay(replay.replay_id, datetime.now(timezone.utc))

        return ReplayRunResult(
            replay_id=replay.replay_id,
            dates_processed=trading_dates,
            day_results=day_results,
            unresolved_position_ids=unresolved,
        )

    def _process_dates(
        self, replay: ReplayMetadata, trading_dates: list[date], progress_every: int | None
    ) -> list[ReplayDayResult]:
        day_results: list[ReplayDayResult] = []
        for position, as_of_date in enumerate(trading_dates, start=1):
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

            if progress_every and (position % progress_every == 0 or position == len(trading_dates)):
                print(
                    f"[replay] {position}/{len(trading_dates)} dates -- {as_of_date} "
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
