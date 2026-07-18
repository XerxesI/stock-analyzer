"""Read-only recommendation-history queries, used by reporting.

Recommendation *decisions* are made where the state that determines them is computed
(candidate_service.py for BUY-side, monitoring_service.py for HOLD/SELL-side). This
module only reads back what was already persisted -- it never writes.
"""

from __future__ import annotations

from datetime import date

from stock_analyzer.sandbox.domain.recommendation import ENTITY_CANDIDATE, ENTITY_POSITION, Recommendation
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


class RecommendationService:
    def __init__(self, repository: SandboxRepository) -> None:
        self._repo = repository

    def candidate_history(self, candidate_id: str) -> list[Recommendation]:
        return self._repo.get_recommendations_for_entity(ENTITY_CANDIDATE, candidate_id)

    def position_history(self, position_id: str) -> list[Recommendation]:
        return self._repo.get_recommendations_for_entity(ENTITY_POSITION, position_id)

    def reconstruct_position_timeline(self, position_id: str) -> list[tuple[date, str]]:
        """`[(date, recommendation), ...]` in chronological order -- e.g.
        `[(day1, "BUY_FILLED"), (day2, "HOLD"), (day3, "HOLD"), (day4, "SELL_TARGET")]`,
        reconstructed purely from append-only persisted rows (MVP 2 spec section 12)."""

        return [(rec.as_of_date, rec.recommendation) for rec in self.position_history(position_id)]
