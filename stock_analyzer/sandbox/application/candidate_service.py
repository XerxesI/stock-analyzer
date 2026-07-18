"""Daily candidate generation: frozen Model 2 ranking -> shadow top-10 -> actionable
top-3, per MVP 2 spec sections 6-8.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
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
from stock_analyzer.sandbox.infrastructure.market_data_adapter import fetch_as_of, session_bar
from stock_analyzer.sandbox.infrastructure.model2_prediction_adapter import Model2PredictionAdapter
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository
from stock_analyzer.sandbox.infrastructure.trading_days import add_trading_sessions

ALREADY_OPEN_POSITION = "ALREADY_OPEN_POSITION"
ALREADY_PENDING_CANDIDATE = "ALREADY_PENDING_CANDIDATE"
MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
INVALID_PRICE = "INVALID_PRICE"
MISSING_ATR = "MISSING_ATR"
# Price history exists for the symbol, but not a bar dated exactly as_of_date (e.g. a
# data lag or a halted session) -- distinct from MISSING_MARKET_DATA (no history at
# all). Using an older bar's close/ATR as if it were the signal day's would silently
# distort the entry ceiling; see market_data_adapter.session_bar.
STALE_DATA = "STALE_DATA"
# Not a data-quality issue -- the candidate was individually clean but ranked below
# the max_actionable_candidates cutoff. Distinct from the other reasons so the
# attribution funnel (reporting/replay_metrics.py) can separate "ranking put it
# outside the top-3" from "we couldn't act on it even though it was top-3."
RANK_LIMIT_EXCEEDED = "RANK_LIMIT_EXCEEDED"


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
        """Build all 10 shadow candidates and decide the final selection in memory
        BEFORE persisting anything, so the `actionable` flag on every stored
        ranked_candidates row reflects the actual selection outcome (one of the <=3
        chosen symbols, not just per-symbol data quality) -- and entry orders (whose
        candidate_id is a real foreign key) are only created after their candidate
        row exists. ranked_candidates stays append-only (ADR-006): each row is
        inserted exactly once, already carrying its final value -- never inserted
        early and mutated later."""

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

        # Phase 1: build a data-quality-checked draft for every shadow symbol. Not
        # persisted yet -- `actionable`/`exclusion_reason` here reflect only whether
        # THIS symbol individually has usable price/ATR data, not the top-3 selection.
        drafts: list[RankedCandidate] = []
        for rank, symbol in enumerate(shadow_symbols, start=1):
            feature_row = features_df.loc[symbol]
            log_adv20 = np.log(max(float(feature_row.get("adv20", 1.0)), 1.0))
            adv_quintile = _apply_quantile_bucket(pd.Series([log_adv20]), adv_edges, adv_labels).iloc[0]
            drafts.append(
                self._build_candidate_draft(
                    run_id, as_of_date, symbol, rank, float(ranked_symbols[symbol]), feature_row, adv_quintile
                )
            )

        # Phase 2: decide the final selection (rank-limit cap, already-open/pending
        # exclusion), still in memory -- no orders yet, since entry_orders.candidate_id
        # has a foreign key into ranked_candidates and no candidate row exists yet.
        final_candidates, actionable = self._decide_selection(drafts)

        # Phase 3: persist every shadow row exactly once, with its final values.
        # Each candidate_id is freshly derived from (as_of_date, symbol) in this same
        # call, so a False return here can only mean the insert was silently rejected
        # (e.g. a constraint violation swallowed by INSERT OR IGNORE) -- never a
        # legitimate "already exists." Raise loudly rather than let the in-memory
        # result (used by callers and reports) silently disagree with what is
        # actually in the database.
        for candidate in final_candidates:
            inserted = self._repo.insert_ranked_candidate(candidate)
            if not inserted:
                raise RuntimeError(
                    f"ranked_candidates insert for {candidate.candidate_id} was silently rejected "
                    "(constraint violation swallowed by INSERT OR IGNORE) -- in-memory result would "
                    "no longer match persisted state."
                )

        # Phase 4: now that the candidate rows exist, create entry orders (and
        # BUY_PENDING recommendations) for the selected symbols only.
        orders = [self._create_entry_order(as_of_date, candidate) for candidate in actionable]

        self._repo.complete_run(run_id, datetime.now(timezone.utc))
        return CandidateGenerationResult(run_id, as_of_date, final_candidates, actionable, orders)

    def _build_candidate_draft(
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
        signal_close: float | None = None
        atr14 = None
        exclusion_reason = None

        if prices.empty:
            exclusion_reason = MISSING_MARKET_DATA
        else:
            # The last bar in `prices` must be dated EXACTLY as_of_date -- fetch_as_of
            # only guarantees no bar is dated after as_of_date, not that one exists ON
            # it. Using an older bar (e.g. after a data lag or a halted session) would
            # silently price the signal off a stale close/ATR.
            signal_bar = session_bar(prices, as_of_date)
            if signal_bar is None:
                exclusion_reason = STALE_DATA
            elif pd.isna(signal_bar["Close"]):
                exclusion_reason = MISSING_MARKET_DATA
            else:
                signal_close = float(signal_bar["Close"])
                if signal_close <= 0:
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

    def _decide_selection(self, drafts: list[RankedCandidate]) -> tuple[list[RankedCandidate], list[RankedCandidate]]:
        """Decides the final actionable/exclusion_reason for every draft, in rank
        order, entirely in memory -- no entry orders are created here (their
        candidate_id foreign key requires the candidate row to already be persisted,
        which only happens after this returns). Returns (all 10 finalized
        candidates, the selected <=3 actionable ones)."""

        final_candidates: list[RankedCandidate] = []
        actionable: list[RankedCandidate] = []

        for draft in drafts:
            if draft.exclusion_reason is not None:
                # Data-quality exclusion decided in phase 1 -- final as-is.
                final_candidates.append(draft)
                continue
            if len(actionable) >= self._config.max_actionable_candidates:
                final_candidates.append(replace(draft, actionable=False, exclusion_reason=RANK_LIMIT_EXCEEDED))
                continue
            if self._repo.has_open_position_for_symbol(draft.symbol):
                final_candidates.append(self._finalize_excluded(draft, ALREADY_OPEN_POSITION))
                continue
            if self._repo.has_pending_order_for_symbol(draft.symbol):
                final_candidates.append(self._finalize_excluded(draft, ALREADY_PENDING_CANDIDATE))
                continue

            final_candidates.append(draft)
            actionable.append(draft)

        return final_candidates, actionable

    def _finalize_excluded(self, draft: RankedCandidate, reason: str) -> RankedCandidate:
        # The frozen recommendation vocabulary (spec section 11) has one bucket,
        # SKIP_ALREADY_OPEN, covering both "already has an OPEN position" and
        # "already has a pending candidate" -- both mean "already committed to this
        # symbol," distinct from a data-quality skip. recommendations.entity_id has
        # no foreign key, so this insert is safe before the candidate row exists.
        self._repo.insert_recommendation(
            Recommendation(
                recommendation_id=Recommendation.make_id(ENTITY_CANDIDATE, draft.candidate_id, draft.as_of_date),
                entity_type=ENTITY_CANDIDATE,
                entity_id=draft.candidate_id,
                symbol=draft.symbol,
                as_of_date=draft.as_of_date,
                recommendation="SKIP_ALREADY_OPEN",
                reason=reason,
            )
        )
        return replace(draft, actionable=False, exclusion_reason=reason)

    def _create_entry_order(self, as_of_date: date, candidate: RankedCandidate) -> EntryOrder:
        """Called only after `candidate` has already been persisted (its candidate_id
        is entry_orders' foreign key target)."""

        valid_until = add_trading_sessions(as_of_date, self._config.entry_validity_sessions)
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
        return order
