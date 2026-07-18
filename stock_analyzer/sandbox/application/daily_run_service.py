"""Fixed daily orchestration order (MVP 2 spec section 15):

    1. process pending entries (using as_of_date's own OHLC for orders created earlier)
    2. monitor currently open positions (including one just filled in step 1 -- the
       entry day is holding day 1, so same-day monitoring is intended, not a bug)
    3. (exits are executed as part of step 2 -- MonitoringService closes positions
       directly, since the exit decision and its execution are the same computation)
    4. generate new candidates from as_of_date's completed close
    5. (new pending entry orders are created as part of step 4 -- CandidateService
       creates an EntryOrder for every actionable candidate directly)
    6. generate reports

A candidate generated in step 4 is never processed as an entry on as_of_date itself --
its earliest possible fill is as_of_date's next trading session, handled by step 1 of
a *later* daily-run invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from stock_analyzer.sandbox.application.candidate_service import CandidateGenerationResult, CandidateService
from stock_analyzer.sandbox.application.entry_service import EntryProcessingOutcome, EntryService
from stock_analyzer.sandbox.application.monitoring_service import MonitoringOutcome, MonitoringService
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.run import COMPLETED, SandboxRun
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


@dataclass
class DailyRunResult:
    run_id: str
    as_of_date: date
    already_completed: bool
    entry_outcomes: list[EntryProcessingOutcome]
    monitoring_outcomes: list[MonitoringOutcome]
    candidate_result: CandidateGenerationResult | None


class DailyRunService:
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

    def run(self, as_of_date: date) -> DailyRunResult:
        run_id = SandboxRun.make_id(as_of_date, "daily-run")
        existing = self._repo.get_run(run_id)
        if existing is not None and existing.status == COMPLETED:
            # Idempotent: a completed daily-run for this date is not re-executed --
            # every sub-step would be a safe no-op anyway (each has its own
            # idempotency guard), but re-running would still mean redundant network
            # fetches for no behavioral difference. Return early instead.
            return DailyRunResult(run_id, as_of_date, True, [], [], None)

        run = SandboxRun(
            run_id=run_id,
            as_of_date=as_of_date,
            command="daily-run",
            started_at=datetime.now(timezone.utc),
            configuration_hash=self._config.config_hash(),
        )
        self._repo.create_run(run)

        entry_outcomes = self._entries.process_entries(as_of_date)
        monitoring_outcomes = self._monitoring.monitor(as_of_date)
        candidate_result = self._candidates.generate_candidates(as_of_date)

        self._repo.complete_run(run_id, datetime.now(timezone.utc))
        return DailyRunResult(run_id, as_of_date, False, entry_outcomes, monitoring_outcomes, candidate_result)
