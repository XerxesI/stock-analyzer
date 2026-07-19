"""Daily position monitoring: target/time exits, HOLD, MFE/MAE tracking, per MVP 2
spec sections 11-12.

Position-level recommendation decisions (HOLD/SELL_TARGET/SELL_TIME/MONITORING_BLOCKED)
live here rather than in a separate module, because the decision is inseparable from
the monitoring computation that produces it (unlike candidate-level BUY decisions,
which are a distinct upstream ranking/selection step -- see candidate_service.py). A
thin RecommendationService (recommendation_service.py) provides read-only history
queries used by reporting, keeping the read side separate from this write side.

A missing price bar never mechanically sells a position -- see _handle_missing_data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from stock_analyzer.sandbox.config import SandboxConfig, round_money, round_price
from stock_analyzer.sandbox.domain.data_quality import DataQualityEvent
from stock_analyzer.sandbox.domain.data_quality import MISSING_MARKET_DATA as DQ_MISSING_MARKET_DATA
from stock_analyzer.sandbox.domain.position import PositionSnapshot, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import (
    ENTITY_POSITION,
    HOLD,
    MONITORING_BLOCKED,
    Recommendation,
    SELL_TARGET,
    SELL_TIME,
)
from stock_analyzer.sandbox.domain.transaction import SELL, VirtualTransaction
from stock_analyzer.sandbox.infrastructure.market_data_adapter import fetch_as_of, session_bar
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


@dataclass(frozen=True)
class MonitoringOutcome:
    position_id: str
    symbol: str
    recommendation: str
    holding_day_count: int | None = None


class MonitoringService:
    def __init__(self, repository: SandboxRepository, config: SandboxConfig | None = None) -> None:
        self._repo = repository
        self._config = config or SandboxConfig()

    def monitor(self, as_of_date: date) -> list[MonitoringOutcome]:
        outcomes: list[MonitoringOutcome] = []
        today_candidates = {c.symbol: c for c in self._repo.get_candidates_for_date(as_of_date)}

        for position in self._repo.get_open_positions():
            if as_of_date < position.entry_date:
                continue  # a position never has a monitoring day before its own entry

            prices = fetch_as_of(position.symbol, as_of_date)
            bar = session_bar(prices, as_of_date)

            if bar is None:
                outcomes.append(self._handle_missing_data(position, as_of_date))
                continue

            outcomes.append(self._handle_session(position, as_of_date, bar, today_candidates.get(position.symbol)))

        return outcomes

    # ------------------------------------------------------------- missing data
    def _handle_missing_data(self, position: VirtualPosition, as_of_date: date) -> MonitoringOutcome:
        """A missing price bar NEVER mechanically sells a position, no matter how many
        consecutive days it persists. Per review: a calendar-days-since-last-snapshot
        proxy (the prior MVP 2 behavior) is not a reliable enough signal of a genuine
        terminal event (delisting, cash merger, ...) to justify a virtual sale --
        MVP 2 has no such detector, so this only ever records the gap and blocks
        monitoring for the day. SELL_DATA_FAILURE remains defined (recommendation.py)
        for a future, formally confirmed terminal-event detector; it is not emitted
        here."""

        self._repo.insert_data_quality_event(
            DataQualityEvent(
                event_id=DataQualityEvent.make_id(position.symbol, as_of_date, DQ_MISSING_MARKET_DATA),
                symbol=position.symbol,
                as_of_date=as_of_date,
                event_type=DQ_MISSING_MARKET_DATA,
                details=f"No price bar for open position {position.position_id} on {as_of_date.isoformat()}.",
            )
        )
        self._repo.insert_recommendation(
            Recommendation(
                recommendation_id=Recommendation.make_id(ENTITY_POSITION, position.position_id, as_of_date),
                entity_type=ENTITY_POSITION,
                entity_id=position.position_id,
                symbol=position.symbol,
                as_of_date=as_of_date,
                recommendation=MONITORING_BLOCKED,
                reason=DQ_MISSING_MARKET_DATA,
            )
        )

        # No snapshot row for a day with no data (never fabricate a price) -- last
        # known state carries forward in reports via the most recent snapshot, not by
        # inventing a new one. The position stays OPEN and unresolved; it is reported
        # explicitly (via data_quality_events + this MONITORING_BLOCKED recommendation)
        # rather than silently or mechanically closed.
        return MonitoringOutcome(position.position_id, position.symbol, MONITORING_BLOCKED)

    # ------------------------------------------------------------- normal session
    def _handle_session(
        self, position: VirtualPosition, as_of_date: date, bar: pd.Series, current_candidate
    ) -> MonitoringOutcome:
        open_price = float(bar["Open"])
        high_price = float(bar["High"])
        low_price = float(bar["Low"])
        close_price = float(bar["Close"])

        holding_day_count = self._next_holding_day_count(position, as_of_date)

        target_hit, exit_price = self._check_target(position, open_price, high_price)
        time_exit = holding_day_count >= self._config.holding_horizon_days

        mfe = max(position.mfe, (high_price - position.entry_price) / position.entry_price)
        mae = min(position.mae, (low_price - position.entry_price) / position.entry_price)
        unrealized_return = (close_price - position.entry_price) / position.entry_price
        distance_to_target = (position.target_price / close_price) - 1.0 if close_price > 0 else None

        if target_hit:
            recommendation = SELL_TARGET
        elif time_exit:
            recommendation = SELL_TIME
            exit_price = close_price
        else:
            recommendation = HOLD

        self._repo.insert_position_snapshot(
            PositionSnapshot(
                snapshot_id=PositionSnapshot.make_id(position.position_id, as_of_date),
                position_id=position.position_id,
                symbol=position.symbol,
                as_of_date=as_of_date,
                close_price=round_price(close_price),
                daily_return=None,  # day-over-day return needs the prior snapshot; left for reporting to derive
                cumulative_unrealized_return=unrealized_return,
                holding_day_count=holding_day_count,
                mfe=mfe,
                mae=mae,
                distance_to_target=distance_to_target,
                current_rank=current_candidate.daily_rank if current_candidate else None,
                current_model_score=current_candidate.model_score if current_candidate else None,
                rank_change_from_entry=(position.initial_rank - current_candidate.daily_rank) if current_candidate else None,
                current_adv_quintile=current_candidate.adv_quintile if current_candidate else None,
                current_market_regime=current_candidate.market_regime if current_candidate else None,
                data_quality_status="OK",
                recommendation=recommendation,
            )
        )
        self._repo.insert_recommendation(
            Recommendation(
                recommendation_id=Recommendation.make_id(ENTITY_POSITION, position.position_id, as_of_date),
                entity_type=ENTITY_POSITION,
                entity_id=position.position_id,
                symbol=position.symbol,
                as_of_date=as_of_date,
                recommendation=recommendation,
                reason=f"holding_day={holding_day_count}",
            )
        )

        if recommendation in (SELL_TARGET, SELL_TIME):
            self._close_position(
                position,
                exit_date=as_of_date,
                exit_price=exit_price,
                exit_reason=recommendation,
                final_holding_day_count=holding_day_count,
                final_mfe=mfe,
                final_mae=mae,
            )
        else:
            self._repo.update_position_state(
                position.position_id,
                current_holding_day_count=holding_day_count,
                current_close=round_price(close_price),
                unrealized_return=unrealized_return,
                mfe=mfe,
                mae=mae,
            )

        return MonitoringOutcome(position.position_id, position.symbol, recommendation, holding_day_count)

    @staticmethod
    def _check_target(position: VirtualPosition, open_price: float, high_price: float) -> tuple[bool, float | None]:
        if open_price >= position.target_price:
            return True, round_price(open_price)
        if high_price >= position.target_price:
            return True, round_price(position.target_price)
        return False, None

    def _next_holding_day_count(self, position: VirtualPosition, as_of_date: date) -> int:
        """Counts only snapshots strictly BEFORE as_of_date. A plain len(existing)+1
        would double-count today's own snapshot if it already exists from a prior,
        interrupted attempt at processing this same date (a replay resume reprocesses
        the full trading_dates list, including a date whose snapshot already
        persisted before the crash) -- silently inflating the holding day by one and
        risking a premature SELL_TIME decision that would not have happened in an
        uninterrupted run."""

        existing = self._repo.get_snapshots_for_position(position.position_id)
        prior = [s for s in existing if s.as_of_date < as_of_date]
        return len(prior) + 1

    def _close_position(
        self,
        position: VirtualPosition,
        exit_date: date,
        exit_price: float,
        exit_reason: str,
        final_holding_day_count: int,
        final_mfe: float,
        final_mae: float,
    ) -> None:
        realized_return = (exit_price - position.entry_price) / position.entry_price
        self._repo.close_position(
            position.position_id,
            exit_date=exit_date,
            exit_price=round_price(exit_price),
            exit_reason=exit_reason,
            realized_return=realized_return,
            final_holding_day_count=final_holding_day_count,
            final_mfe=final_mfe,
            final_mae=final_mae,
        )
        self._repo.insert_transaction(
            VirtualTransaction(
                transaction_id=VirtualTransaction.make_id(position.position_id, SELL, exit_date),
                position_id=position.position_id,
                symbol=position.symbol,
                transaction_type=SELL,
                transaction_date=exit_date,
                price=round_price(exit_price),
                quantity=position.quantity,
                notional=round_money(position.quantity * exit_price),
                reason=exit_reason,
            )
        )
