"""The optional market-data provider seam shared by `CandidateService`,
`EntryService`, and `MonitoringService` (EXP-005 Stage 10 closure).

Until now, all three services called
`stock_analyzer.sandbox.infrastructure.market_data_adapter.fetch_as_of` directly --
a live Yahoo Finance fetch (`stock_analyzer.data.data_fetcher.get_stock_data`).
EXP-005's replay entry point named itself "frozen-artifact replay" but never
actually severed this live dependency -- every EXP-005 date's decisions were, in
fact, still being made from whatever Yahoo returned at run time, not from a frozen
snapshot. This seam fixes that: each service now calls
`self._market_data.fetch_as_of(...)` through an injectable provider instead.

`MarketDataProvider` is a structural Protocol (duck-typed, matching this project's
existing `CashAvailabilityProvider`/`PortfolioAccountingSeam` convention) -- no
default implementation lives here. Each of the three service modules defines its
OWN small default provider inline, delegating to that module's own imported
`fetch_as_of` name at CALL time (not bound at construction) specifically so
existing tests that monkeypatch `<module>.fetch_as_of` continue to work completely
unchanged -- this seam is additive, not a behavior change, for every caller that
does not inject a provider.

EXP-005's own frozen provider
(`stock_analyzer.sandbox.exp005.infrastructure.frozen_market_data_provider.
FrozenSwing20MarketDataProvider`) has NO live/network fallback at all -- it is a
required constructor argument everywhere EXP-005 wires these services, structurally
impossible to omit.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    def fetch_as_of(self, symbol: str, as_of_date: date, period: str = "2y") -> pd.DataFrame:
        """OHLCV history for `symbol`, indexed by date, truncated to bars dated
        `<= as_of_date` -- never a bar dated after it, regardless of what the
        underlying source holds. Same contract as
        market_data_adapter.fetch_as_of, which every default provider still
        delegates to."""
        ...
