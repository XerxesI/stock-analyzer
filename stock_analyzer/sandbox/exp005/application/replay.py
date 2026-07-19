"""EXP-005's frozen-artifact replay entry point -- Revision 5, Stage 8.

Wires ONE variant/seed run's full stack, reusing core `ReplayService`/
`CandidateService`/`EntryService`/`MonitoringService` completely unmodified in their
own decision logic -- only the seams already frozen in Sections 11.2/11.3 (the
admission orchestrator, the accounting seam, and `ReplayService`'s day-start/day-
complete hooks) are used to attach EXP-005's own persistence. No vectorized
shortcut, no bypass of the day-by-day sequence: `ReplayService._process_dates`'s
entries -> monitoring -> candidates order (Section 8.5) drives everything.

`day_started_hook` tells a Variant D `RankingControlAdapter` which date to score
against (Section 11.4 -- `CandidateService.generate_candidates` never passes
`as_of_date` to the adapter directly). `day_completed_hook` computes and persists
exactly one `portfolio_equity_snapshots` row per processed day, AFTER that day's
full sequence and BEFORE the resume watermark advances (Section 8.5).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

from stock_analyzer.sandbox.application.candidate_service import CandidateService, HistoricalFeatureUniverseProvider
from stock_analyzer.sandbox.application.entry_service import EntryService
from stock_analyzer.sandbox.application.monitoring_service import MonitoringService
from stock_analyzer.sandbox.application.replay_service import ReplayService
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.exp005.application.admission_orchestrator import AdmissionTransactionService
from stock_analyzer.sandbox.exp005.application.portfolio_accounting_seam import Exp005AccountingSeam
from stock_analyzer.sandbox.exp005.application.portfolio_ledger import PortfolioLedger
from stock_analyzer.sandbox.exp005.application.variant_runner import CapacityAdmissionOrchestrator, RankingControlAdapter
from stock_analyzer.sandbox.exp005.config import VARIANT_D, Exp005Config
from stock_analyzer.sandbox.exp005.domain.units import to_money_units
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


@dataclass(frozen=True)
class Exp005ReplayServices:
    """Everything wired for one EXP-005 replay run (one variant, one seed).
    `replay_service.run(replay_metadata, trading_dates, ...)` drives it, exactly
    like a non-EXP-005 replay -- see stock_analyzer.sandbox.application.
    replay_service.ReplayService.run for the resume/determinism contract, unchanged
    here. The individual sub-services are also exposed (mirroring the core
    ReplayService test fixtures' own convention) so a test can call them directly
    to simulate a partially-processed date before resuming through the public
    replay_service.run() path."""

    sandbox_repo: SandboxRepository
    portfolio_repo: PortfolioRepository
    ledger: PortfolioLedger
    candidate_service: CandidateService
    entry_service: EntryService
    monitoring_service: MonitoringService
    replay_service: ReplayService


def build_exp005_replay_services(
    conn: sqlite3.Connection,
    model_adapter,
    universe_provider: HistoricalFeatureUniverseProvider,
    exp005_config: Exp005Config,
    replay_id: str,
    market_data_snapshot_id: str,
    sandbox_config: SandboxConfig | None = None,
) -> Exp005ReplayServices:
    """`conn` must already have both `stock_analyzer.sandbox.infrastructure.
    schema.init_db` and `stock_analyzer.sandbox.exp005.infrastructure.schema.
    init_exp005_schema` applied. `model_adapter` is always a real (or fake, for
    tests), already-fitted `Model2PredictionAdapter`-shaped object -- for Variant D
    it is wrapped in a `RankingControlAdapter` (Section 11.4) that reuses its
    fit_params/model_version/feature_names but replaces the ranking score;
    Variant B uses it directly, unmodified (Section 12 item 4)."""

    sandbox_config = sandbox_config or SandboxConfig()
    sandbox_repo = SandboxRepository(conn)
    portfolio_repo = PortfolioRepository(conn)

    portfolio_config = exp005_config.portfolio
    starting_capital_units = to_money_units(portfolio_config.starting_capital)
    slot_budget_units = to_money_units(portfolio_config.slot_budget)

    ledger = PortfolioLedger(portfolio_repo, sandbox_repo, replay_id, starting_capital_units)

    admission_service = AdmissionTransactionService(
        conn, portfolio_repo, sandbox_repo, replay_id, portfolio_config.max_slots, slot_budget_units, ledger,
    )
    capacity_orchestrator = CapacityAdmissionOrchestrator(admission_service, sandbox_config.entry_validity_sessions)

    adapter = (
        RankingControlAdapter(model_adapter, exp005_config.control_seed)
        if exp005_config.variant_id == VARIANT_D
        else model_adapter
    )

    accounting_seam = Exp005AccountingSeam(
        portfolio_repo, replay_id, exp005_config.variant_id, exp005_config.control_seed,
        portfolio_config, market_data_snapshot_id,
    )

    candidate_service = CandidateService(
        sandbox_repo, adapter, universe_provider, sandbox_config, admission_orchestrator=capacity_orchestrator,
    )
    entry_service = EntryService(sandbox_repo, sandbox_config, accounting_seam=accounting_seam)
    monitoring_service = MonitoringService(sandbox_repo, sandbox_config, accounting_seam=accounting_seam)

    def day_started_hook(as_of_date: date) -> None:
        if isinstance(adapter, RankingControlAdapter):
            adapter.set_current_date(as_of_date)

    def day_completed_hook(as_of_date: date) -> None:
        snapshot = ledger.compute_snapshot(as_of_date, datetime.now(timezone.utc))
        portfolio_repo.append_equity_snapshot(snapshot)

    replay_service = ReplayService(
        sandbox_repo, candidate_service, entry_service, monitoring_service, sandbox_config,
        day_started_hook=day_started_hook, day_completed_hook=day_completed_hook,
    )

    return Exp005ReplayServices(
        sandbox_repo, portfolio_repo, ledger, candidate_service, entry_service, monitoring_service, replay_service,
    )
