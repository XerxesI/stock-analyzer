"""Pending entry order processing: deterministic next-session-OHLC execution, per
MVP 2 spec section 9 and ADR-007.

Order expiry is driven by counting real attempted sessions (entry_order_attempts rows
with an actual price bar), not by comparing today's date against the order's
`valid_until` display estimate -- that estimate uses a weekday-only approximation
(stock_analyzer.sandbox.infrastructure.trading_days) that can be off by a day or two
around holidays, and must never gate a financial decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from stock_analyzer.sandbox.application.accounting_seam import DefaultAccountingSeam, PortfolioAccountingSeam
from stock_analyzer.sandbox.config import SandboxConfig, round_money, round_price
from stock_analyzer.sandbox.domain.entry_order import (
    EXPIRED,
    FILLED,
    FILLED_AT_CEILING,
    FILLED_AT_OPEN,
    NO_FILL,
    EntryOrder,
    EntryOrderAttempt,
)
from stock_analyzer.sandbox.domain.position import VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import (
    BUY_FILLED,
    ENTITY_CANDIDATE,
    EXPIRED_ENTRY,
    Recommendation,
    SKIP_PRICE_TOO_HIGH,
)
from stock_analyzer.sandbox.domain.transaction import BUY, VirtualTransaction
from stock_analyzer.sandbox.infrastructure.market_data_adapter import fetch_as_of, session_bar
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository
from stock_analyzer.sandbox.infrastructure.trading_days import add_trading_sessions


@dataclass(frozen=True)
class EntryProcessingOutcome:
    order_id: str
    symbol: str
    outcome: str  # "FILLED", "SKIPPED_TODAY", "EXPIRED", "NO_SESSION_DATA"
    fill_price: float | None = None
    position_id: str | None = None


class EntryService:
    def __init__(
        self,
        repository: SandboxRepository,
        config: SandboxConfig | None = None,
        accounting_seam: PortfolioAccountingSeam | None = None,
    ) -> None:
        self._repo = repository
        self._config = config or SandboxConfig()
        self._accounting_seam = accounting_seam or DefaultAccountingSeam(self._config)

    def process_entries(self, as_of_date: date) -> list[EntryProcessingOutcome]:
        outcomes: list[EntryProcessingOutcome] = []
        for order in self._repo.get_pending_orders():
            if as_of_date <= order.signal_date:
                # Never fill on or before the signal date -- the earliest possible
                # execution session is the signal date's next trading session.
                continue

            # Only attempts from EARLIER sessions count toward "prior" -- if today's
            # own attempt already exists (a replay resume reprocessing this same date
            # after an interruption between insert_entry_order_attempt and the status
            # update that follows it), it must not inflate attempt_number below and
            # cause a premature EXPIRED decision that would not have happened in an
            # uninterrupted run.
            prior_attempts = [a for a in self._repo.get_attempts_for_order(order.order_id) if a.attempt_date < as_of_date]
            if len(prior_attempts) >= self._config.entry_validity_sessions:
                continue  # already resolved in a prior run; defensive, should not happen

            prices = fetch_as_of(order.symbol, as_of_date)
            bar = session_bar(prices, as_of_date)
            if bar is None:
                outcomes.append(EntryProcessingOutcome(order.order_id, order.symbol, "NO_SESSION_DATA"))
                continue

            attempt_number = len(prior_attempts) + 1
            outcome, fill_price, reason = self._evaluate_execution(bar, order.max_entry_price)

            attempt = EntryOrderAttempt(
                attempt_id=EntryOrderAttempt.make_id(order.order_id, as_of_date),
                order_id=order.order_id,
                symbol=order.symbol,
                attempt_date=as_of_date,
                session_open=float(bar["Open"]) if pd.notna(bar["Open"]) else None,
                session_high=float(bar["High"]) if pd.notna(bar["High"]) else None,
                session_low=float(bar["Low"]) if pd.notna(bar["Low"]) else None,
                session_close=float(bar["Close"]) if pd.notna(bar["Close"]) else None,
                max_entry_price=order.max_entry_price,
                outcome=outcome,
                fill_price=fill_price,
                reason=reason,
            )
            self._repo.insert_entry_order_attempt(attempt)

            if outcome != NO_FILL:
                position = self._fill_order(order, as_of_date, fill_price, outcome)
                outcomes.append(EntryProcessingOutcome(order.order_id, order.symbol, "FILLED", fill_price, position.position_id))
            elif attempt_number >= self._config.entry_validity_sessions:
                self._expire_order(order, as_of_date, reason)
                self._repo.insert_recommendation(
                    Recommendation(
                        recommendation_id=Recommendation.make_id(ENTITY_CANDIDATE, order.candidate_id, as_of_date),
                        entity_type=ENTITY_CANDIDATE,
                        entity_id=order.candidate_id,
                        symbol=order.symbol,
                        as_of_date=as_of_date,
                        recommendation=EXPIRED_ENTRY,
                        reason=reason,
                    )
                )
                outcomes.append(EntryProcessingOutcome(order.order_id, order.symbol, "EXPIRED"))
            else:
                self._repo.insert_recommendation(
                    Recommendation(
                        recommendation_id=Recommendation.make_id(ENTITY_CANDIDATE, order.candidate_id, as_of_date),
                        entity_type=ENTITY_CANDIDATE,
                        entity_id=order.candidate_id,
                        symbol=order.symbol,
                        as_of_date=as_of_date,
                        recommendation=SKIP_PRICE_TOO_HIGH,
                        reason=reason,
                    )
                )
                outcomes.append(EntryProcessingOutcome(order.order_id, order.symbol, "SKIPPED_TODAY"))
        return outcomes

    @staticmethod
    def _evaluate_execution(bar: pd.Series, max_entry_price: float) -> tuple[str, float | None, str]:
        """ADR-007's three-way execution rule, using only this session's own OHLC."""

        open_price = float(bar["Open"])
        low_price = float(bar["Low"])

        if open_price <= max_entry_price:
            return FILLED_AT_OPEN, round_price(open_price), "next_day_open<=max_entry_price"
        if low_price <= max_entry_price < open_price:
            return FILLED_AT_CEILING, round_price(max_entry_price), "gap_above_ceiling_but_session_low_touched_ceiling"
        return NO_FILL, None, "entire_session_above_ceiling"

    def _fill_order(self, order: EntryOrder, fill_date: date, fill_price: float, fill_reason: str) -> VirtualPosition:
        # Sizing is the one seam-controlled step (accounting_seam.py) -- fillability
        # and fill_price above are already decided and unaffected by which seam is
        # injected. Called BEFORE the transaction opens: an EXP-005 seam's
        # implementation performs its own (read-only at this point) accounting
        # computation here and caches it, keyed by order.order_id, for on_filled to
        # reuse without recomputation.
        quantity = self._accounting_seam.size_buy(order, fill_price, fill_date)

        candidate = self._repo.get_candidate(order.candidate_id)
        target_price = round_price(fill_price * (1.0 + self._config.target_return))
        # Entry day = holding day 1 (see MVP 2 spec section 11); the 20th holding day
        # is 19 further trading sessions after the entry session.
        planned_time_exit_date = add_trading_sessions(fill_date, self._config.holding_horizon_days - 1)

        position = VirtualPosition(
            position_id=VirtualPosition.make_id(order.symbol, fill_date),
            symbol=order.symbol,
            candidate_id=order.candidate_id,
            order_id=order.order_id,
            signal_date=order.signal_date,
            entry_date=fill_date,
            entry_price=fill_price,
            quantity=quantity,
            initial_rank=candidate.daily_rank if candidate else -1,
            initial_model_score=candidate.model_score if candidate else float("nan"),
            signal_close=candidate.signal_close if candidate else fill_price,
            max_entry_price=order.max_entry_price,
            initial_adv_quintile=candidate.adv_quintile if candidate else None,
            initial_market_regime=candidate.market_regime if candidate else None,
            target_price=target_price,
            planned_time_exit_date=planned_time_exit_date,
        )
        transaction = VirtualTransaction(
            transaction_id=VirtualTransaction.make_id(position.position_id, BUY, fill_date),
            position_id=position.position_id,
            symbol=order.symbol,
            transaction_type=BUY,
            transaction_date=fill_date,
            price=fill_price,
            quantity=quantity,
            notional=round_money(quantity * fill_price),
            reason=fill_reason,
        )

        # Atomic: order status, position, BUY transaction, and (if an EXP-005 seam
        # is injected) its own executions/reservation writes commit or roll back as
        # one unit -- a crash partway through can never leave one side of the fill
        # event persisted without the other.
        self._repo._conn.execute("BEGIN IMMEDIATE")
        try:
            self._repo._update_order_status_row(
                order.order_id, FILLED, fill_date=fill_date, fill_price=fill_price, fill_reason=fill_reason
            )
            existing = self._repo.get_position(position.position_id)
            if existing is not None:
                position = existing
            else:
                self._repo._insert_position_row(position)
            self._repo._insert_transaction_row(transaction)
            self._accounting_seam.on_filled(order, position, transaction, fill_price)
            self._repo._conn.commit()
        except Exception:
            self._repo._conn.rollback()
            raise

        self._repo.insert_recommendation(
            Recommendation(
                recommendation_id=Recommendation.make_id(ENTITY_CANDIDATE, order.candidate_id, fill_date),
                entity_type=ENTITY_CANDIDATE,
                entity_id=order.candidate_id,
                symbol=order.symbol,
                as_of_date=fill_date,
                recommendation=BUY_FILLED,
                reason=fill_reason,
            )
        )
        return position

    def _expire_order(self, order: EntryOrder, as_of_date: date, reason: str) -> None:
        """Atomic: order status and (if an EXP-005 seam is injected) its own
        reservation-release write commit or roll back together."""

        self._repo._conn.execute("BEGIN IMMEDIATE")
        try:
            self._repo._update_order_status_row(order.order_id, EXPIRED, no_fill_reason=reason)
            self._accounting_seam.on_expired(order, as_of_date, reason)
            self._repo._conn.commit()
        except Exception:
            self._repo._conn.rollback()
            raise
