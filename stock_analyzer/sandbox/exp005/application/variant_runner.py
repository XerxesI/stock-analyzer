"""Variant B and Variant D orchestration -- Revision 5, Section 11.4, Stage 7.

Variant B reuses `Model2PredictionAdapter` completely unmodified (Section 12 item
4) -- no wrapper needed, `CandidateService` is constructed with it directly.

Variant D (`RankingControlAdapter`) wraps an already-fitted `Model2PredictionAdapter`
for everything EXCEPT the ranking score itself -- `model_version`, `fit_params`
(critically including `adv_edges`/`adv_labels`, the same train-fit bucketing edges
Variant B uses, read directly by `CandidateService.generate_candidates`), and
`feature_names`/`train_row_count` all stay identical to Variant B, so the ONLY
difference between the two variants is which score drives selection -- never the
data-quality bucketing methodology or provenance. `.score()` is replaced with
Section 11.4's frozen deterministic formula:

    score(seed, as_of_date, symbol) = int(sha256(f"{seed}:{as_of_date}:{symbol}")
                                          .hexdigest(), 16) / 2**256

a stable pure function of (seed, as_of_date, symbol) only -- invariant to row
order, call order, or parallelism. `CandidateService.generate_candidates` calls
`self._adapter.score(features_df)` with no `as_of_date` argument, so this adapter
carries `as_of_date` as short-lived per-call state, set explicitly by the day-loop
(Stage 8) via `set_current_date` immediately before each `generate_candidates` call
-- `CandidateService` itself is not modified to support this.

`CapacityAdmissionOrchestrator` is Section 11.2 point 5's `AdmissionOrchestrator`
implementation for EXP-005: builds each candidate's order (pure construction, no
persistence -- `stock_analyzer.sandbox.application.candidate_service.build_entry_order`)
and hands it to `AdmissionTransactionService.admit_candidate`, which performs the
one atomic admission/reservation/order write per candidate. Processes candidates in
the order given (already rank order -- Section 8.4).
"""

from __future__ import annotations

import hashlib
from datetime import date

import pandas as pd

from stock_analyzer.sandbox.application.candidate_service import build_entry_order
from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.exp005.application.admission_orchestrator import AdmissionTransactionService
from stock_analyzer.sandbox.exp005.domain.admission import ACCEPTED
from stock_analyzer.sandbox.infrastructure.model2_prediction_adapter import Model2PredictionAdapter


class ControlScoreNotConfiguredError(RuntimeError):
    """Raised if RankingControlAdapter.score() is called before set_current_date --
    the deterministic formula needs as_of_date, which CandidateService.generate_
    candidates does not pass to the adapter directly (Section 11.4)."""


def control_score(control_seed: int, as_of_date: date, symbol: str) -> float:
    """Section 11.4's frozen formula, verbatim. A module-level function (not a
    method) so it is independently, trivially unit-testable without constructing
    an adapter."""

    digest = hashlib.sha256(f"{control_seed}:{as_of_date.isoformat()}:{symbol}".encode("utf-8")).hexdigest()
    return int(digest, 16) / 2**256


class RankingControlAdapter:
    def __init__(self, model_adapter: Model2PredictionAdapter, control_seed: int) -> None:
        self._control_seed = control_seed
        self.model_version = model_adapter.model_version
        self.fit_params = model_adapter.fit_params
        self.feature_names = model_adapter.feature_names
        self.train_row_count = model_adapter.train_row_count
        self._current_date: date | None = None

    def set_current_date(self, as_of_date: date) -> None:
        self._current_date = as_of_date

    def score(self, features_df: pd.DataFrame) -> pd.Series:
        if self._current_date is None:
            raise ControlScoreNotConfiguredError(
                "RankingControlAdapter.set_current_date(...) must be called before score() for each "
                "processed date -- the deterministic control score depends on as_of_date."
            )
        if features_df.empty:
            return pd.Series(dtype=float)

        # Sorted by symbol BEFORE scoring (not just returned in features_df's own
        # row order): guarantees the Series is built in symbol-ascending order
        # regardless of the input's order, so pandas' stable sort_values (used by
        # CandidateService to rank) breaks any exact-value tie -- astronomically
        # unlikely with a 256-bit hash, but Section 11.4 requires it be defined --
        # by symbol ascending, deterministically, independent of input row order.
        symbols = sorted(features_df.index)
        scores = {symbol: control_score(self._control_seed, self._current_date, symbol) for symbol in symbols}
        return pd.Series(scores, name="model_score")


class CapacityAdmissionOrchestrator:
    def __init__(self, admission_service: AdmissionTransactionService, entry_validity_sessions: int) -> None:
        self._admission_service = admission_service
        self._entry_validity_sessions = entry_validity_sessions

    def admit_and_create_orders(self, candidates: list[RankedCandidate], as_of_date: date) -> list[EntryOrder]:
        orders: list[EntryOrder] = []
        for candidate in candidates:
            order = build_entry_order(as_of_date, candidate, self._entry_validity_sessions)
            result = self._admission_service.admit_candidate(candidate, as_of_date, order)
            if result.admission.decision == ACCEPTED:
                orders.append(result.order)
        return orders
