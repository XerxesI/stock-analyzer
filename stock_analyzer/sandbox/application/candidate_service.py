"""Daily candidate generation: frozen Model 2 ranking -> shadow top-10 -> actionable
top-3, per MVP 2 spec sections 6-8.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_swing_20_context_target_mechanics import _apply_quantile_bucket  # noqa: E402
from stock_analyzer.core.indicators import calculate_indicators
from stock_analyzer.sandbox.config import SandboxConfig, round_price
from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.entry_order import PENDING as ORDER_PENDING
from stock_analyzer.sandbox.domain.recommendation import BUY_PENDING, ENTITY_CANDIDATE, Recommendation
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.infrastructure.market_data_adapter import fetch_as_of, latest_close
from stock_analyzer.sandbox.infrastructure.model2_prediction_adapter import Model2PredictionAdapter
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

ALREADY_OPEN_POSITION = "ALREADY_OPEN_POSITION"
ALREADY_PENDING_CANDIDATE = "ALREADY_PENDING_CANDIDATE"
MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
INVALID_PRICE = "INVALID_PRICE"
MISSING_ATR = "MISSING_ATR"


def compute_max_entry_price(signal_close: float | None, atr14: float | None, config: SandboxConfig) -> float | None:
    """The provisional, non-optimized entry ceiling (MVP 2 spec section 8, ADR-007):

        min(signal_close * (1 + max_close_extension_pct), signal_close + atr_extension_multiple * ATR14)

    Returns None (not 0, not a fabricated value) if either input is missing or the
    close is non-positive -- callers must treat that as MISSING_ATR / INVALID_PRICE,
    never silently substitute a default.
    """

    if signal_close is None or pd.isna(signal_close) or signal_close <= 0:
        return None
    if atr14 is None or pd.isna(atr14):
        return None
    close_cap = signal_close * (1.0 + config.max_close_extension_pct)
    atr_cap = signal_close + config.atr_extension_multiple * atr14
    return round_price(min(close_cap, atr_cap))


class HistoricalFeatureUniverseProvider:
    """Symbol universe + pre-computed Model 2 stock/context features for a given
    as-of date, read from an existing frozen feature dataset (e.g. the locked_test or
    train+validation features.parquet).

    For historical replay / integration smoke-testing only (MVP 2 spec section 17). A
    live forward run over new dates would need a fresh, live universe-discovery
    adapter -- explicitly out of scope for MVP 2 (spec section 19).
    """

    def __init__(self, features_path: str) -> None:
        self._df = pd.read_parquet(features_path)
        self._df["date"] = pd.to_datetime(self._df["date"])

    def features_for_date(self, as_of_date: date) -> pd.DataFrame:
        day = self._df[self._df["date"].dt.date == as_of_date]
        return day.set_index("symbol")


@dataclass
class CandidateGenerationResult:
    run_id: str
    as_of_date: date
    shadow_top10: list[RankedCandidate]
    actionable: list[RankedCandidate]
    entry_orders: list[EntryOrder]


class CandidateService:
    def __init__(
        self,
        repository: SandboxRepository,
        prediction_adapter: Model2PredictionAdapter,
        universe_provider: HistoricalFeatureUniverseProvider,
        config: SandboxConfig | None = None,
    ) -> None:
        self._repo = repository
        self._adapter = prediction_adapter
        self._universe = universe_provider
        self._config = config or SandboxConfig()

    def generate_candidates(self, as_of_date: date) -> CandidateGenerationResult:
        run_id = SandboxRun.make_id(as_of_date, "generate-candidates")
        run = SandboxRun(
            run_id=run_id,
            as_of_date=as_of_date,
            command="generate-candidates",
            started_at=datetime.now(timezone.utc),
            configuration_hash=self._config.config_hash(),
            model_version=self._adapter.model_version,
        )
        run, _created = self._repo.create_run(run)

        features_df = self._universe.features_for_date(as_of_date)
        if features_df.empty:
            self._repo.complete_run(run_id, datetime.now(timezone.utc))
            return CandidateGenerationResult(run_id, as_of_date, [], [], [])

        scores = self._adapter.score(features_df)
        ranked_symbols = scores.sort_values(ascending=False)
        shadow_symbols = list(ranked_symbols.index[: self._config.shadow_top_n])

        adv_edges = self._adapter.fit_params["adv_edges"]
        adv_labels = self._adapter.fit_params["adv_labels"]

        shadow_candidates: list[RankedCandidate] = []
        for rank, symbol in enumerate(shadow_symbols, start=1):
            feature_row = features_df.loc[symbol]
            log_adv20 = np.log(max(float(feature_row.get("adv20", 1.0)), 1.0))
            adv_quintile = _apply_quantile_bucket(pd.Series([log_adv20]), adv_edges, adv_labels).iloc[0]
            candidate = self._build_candidate(
                run_id, as_of_date, symbol, rank, float(ranked_symbols[symbol]), feature_row, adv_quintile
            )
            self._repo.insert_ranked_candidate(candidate)
            shadow_candidates.append(candidate)

        actionable, orders = self._select_actionable(run_id, as_of_date, shadow_candidates)

        self._repo.complete_run(run_id, datetime.now(timezone.utc))
        return CandidateGenerationResult(run_id, as_of_date, shadow_candidates, actionable, orders)

    def _build_candidate(
        self,
        run_id: str,
        as_of_date: date,
        symbol: str,
        rank: int,
        model_score: float,
        feature_row: pd.Series,
        adv_quintile: str,
    ) -> RankedCandidate:
        prices = fetch_as_of(symbol, as_of_date)
        signal_close = latest_close(prices)
        atr14 = None
        exclusion_reason = None

        if prices.empty or signal_close is None:
            exclusion_reason = MISSING_MARKET_DATA
        elif signal_close <= 0:
            exclusion_reason = INVALID_PRICE
        else:
            enriched = calculate_indicators(prices)
            atr_series = enriched.get("ATR14")
            if atr_series is not None and not atr_series.empty and pd.notna(atr_series.iloc[-1]):
                atr14 = float(atr_series.iloc[-1])
            else:
                exclusion_reason = MISSING_ATR

        max_entry_price = compute_max_entry_price(signal_close, atr14, self._config) if exclusion_reason is None else None
        if exclusion_reason is None and max_entry_price is None:
            exclusion_reason = MISSING_ATR

        return RankedCandidate(
            candidate_id=RankedCandidate.make_id(as_of_date, symbol),
            run_id=run_id,
            as_of_date=as_of_date,
            symbol=symbol,
            daily_rank=rank,
            model_score=model_score,
            signal_close=round_price(signal_close) if signal_close is not None else None,
            atr14=round_price(atr14) if atr14 is not None else None,
            max_entry_price=max_entry_price,
            shadow_top10=True,
            actionable=exclusion_reason is None,
            exclusion_reason=exclusion_reason,
            adv_quintile=str(adv_quintile) if pd.notna(adv_quintile) else None,
            market_regime=f"{feature_row.get('spy_trend')}_{feature_row.get('spy_volatility_bucket')}"
            if pd.notna(feature_row.get("spy_trend"))
            else None,
        )

    def _select_actionable(
        self, run_id: str, as_of_date: date, shadow_candidates: list[RankedCandidate]
    ) -> tuple[list[RankedCandidate], list[EntryOrder]]:
        actionable: list[RankedCandidate] = []
        orders: list[EntryOrder] = []

        for candidate in shadow_candidates:
            if len(actionable) >= self._config.max_actionable_candidates:
                break
            if candidate.exclusion_reason is not None:
                continue
            if self._repo.has_open_position_for_symbol(candidate.symbol):
                self._mark_excluded(candidate, ALREADY_OPEN_POSITION)
                continue
            if self._repo.has_pending_order_for_symbol(candidate.symbol):
                self._mark_excluded(candidate, ALREADY_PENDING_CANDIDATE)
                continue

            actionable.append(candidate)
            valid_until = _add_trading_sessions(as_of_date, self._config.entry_validity_sessions)
            order = EntryOrder(
                order_id=EntryOrder.make_id(candidate.candidate_id),
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                signal_date=as_of_date,
                created_date=as_of_date,
                valid_until=valid_until,
                max_entry_price=candidate.max_entry_price,
                status=ORDER_PENDING,
            )
            order, _created = self._repo.create_entry_order(order)
            orders.append(order)

            self._repo.insert_recommendation(
                Recommendation(
                    recommendation_id=Recommendation.make_id(ENTITY_CANDIDATE, candidate.candidate_id, as_of_date),
                    entity_type=ENTITY_CANDIDATE,
                    entity_id=candidate.candidate_id,
                    symbol=candidate.symbol,
                    as_of_date=as_of_date,
                    recommendation=BUY_PENDING,
                    reason=f"rank={candidate.daily_rank} model_score={candidate.model_score:.4f}",
                )
            )

        return actionable, orders

    def _mark_excluded(self, candidate: RankedCandidate, reason: str) -> None:
        # ranked_candidates rows are append-only and already persisted with
        # actionable=True/exclusion_reason=None at this point (they only fail the
        # ALREADY_OPEN/ALREADY_PENDING checks, which require repository state not
        # known until selection time) -- record the real outcome as a recommendation
        # instead of mutating the immutable candidate row.
        # The frozen recommendation vocabulary (spec section 11) has one bucket,
        # SKIP_ALREADY_OPEN, covering both "already has an OPEN position" and
        # "already has a pending candidate" -- both mean "already committed to this
        # symbol," distinct from a data-quality skip.
        recommendation = (
            "SKIP_ALREADY_OPEN"
            if reason in (ALREADY_OPEN_POSITION, ALREADY_PENDING_CANDIDATE)
            else "SKIP_DATA_QUALITY"
        )
        self._repo.insert_recommendation(
            Recommendation(
                recommendation_id=Recommendation.make_id(ENTITY_CANDIDATE, candidate.candidate_id, candidate.as_of_date),
                entity_type=ENTITY_CANDIDATE,
                entity_id=candidate.candidate_id,
                symbol=candidate.symbol,
                as_of_date=candidate.as_of_date,
                recommendation=recommendation,
                reason=reason,
            )
        )


def _add_trading_sessions(start: date, sessions: int) -> date:
    """Naive calendar-day trading-session approximation for order validity windows
    (weekends skipped; US market holidays not modeled in MVP 2). Sufficient for the
    entry-order validity window, which only needs to bound how long an order stays
    pending -- not to be confused with the point-in-time OHLC execution logic in
    entry_service.py, which uses only real fetched trading-day bars."""

    current = start
    remaining = sessions
    while remaining > 0:
        current = date.fromordinal(current.toordinal() + 1)
        if current.weekday() < 5:
            remaining -= 1
    return current
