"""EXP-005's frozen, hash-verified market-data provider -- Stage 10 closure (P1
review). NO live/network fallback of any kind: every OHLCV bar this provider ever
returns comes from a pre-loaded, hash-verified copy of one specific SWING_20
snapshot's `prices.parquet`, loaded and verified once at construction. Implements
`stock_analyzer.sandbox.application.market_data_provider.MarketDataProvider` (the
same `.fetch_as_of(symbol, as_of_date, period)` contract every other provider
honors) -- `session_bar` (market_data_adapter.py) is reused completely unchanged;
this class only replaces the FETCH, never the exact-session-lookup rule.

Construction requires a feature-snapshot directory, not a bare SWING_20 snapshot
directory: this is what lets it verify (via
`exp005.infrastructure.frozen_artifacts.verify_frozen_lineage`) that the OHLCV
data being served is the SAME upstream SWING_20 snapshot the injected prediction
adapter's model was fit against -- a replay wired to mismatched upstream sources
would silently produce meaningless results otherwise.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import verify_frozen_lineage


class FrozenDataRangeError(RuntimeError):
    """Raised when fetch_as_of is asked for a date outside the frozen artifact's
    own observed date range -- a caller/configuration error, never silently
    answered with an empty result that could be confused with "genuinely no data
    that day for this one symbol.\""""


class FrozenDataIntegrityError(RuntimeError):
    """Raised at construction if the loaded prices data contains duplicate
    (symbol, date) rows or malformed (null/empty) symbol/date keys -- fails
    closed rather than silently picking one of several conflicting rows."""


_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _validate_no_malformed_keys(df: pd.DataFrame) -> None:
    if df["symbol"].isna().any() or (df["symbol"].astype(str).str.strip() == "").any():
        raise FrozenDataIntegrityError("frozen prices data contains a null/empty symbol key.")
    if df["date"].isna().any():
        raise FrozenDataIntegrityError("frozen prices data contains a null date key.")


def _validate_no_duplicates(df: pd.DataFrame) -> None:
    duplicate_mask = df.duplicated(subset=["symbol", "date"], keep=False)
    if duplicate_mask.any():
        examples = df.loc[duplicate_mask, ["symbol", "date"]].drop_duplicates().head(5)
        raise FrozenDataIntegrityError(
            f"frozen prices data contains duplicate (symbol, date) rows -- examples: "
            f"{examples.to_dict('records')}."
        )


class FrozenSwing20MarketDataProvider:
    def __init__(self, feature_snapshot_dir: str | Path) -> None:
        lineage = verify_frozen_lineage(feature_snapshot_dir)

        df = lineage.prices_df.copy()
        _validate_no_malformed_keys(df)
        _validate_no_duplicates(df)

        df["date"] = pd.to_datetime(df["date"]).dt.date
        self._min_date = df["date"].min()
        self._max_date = df["date"].max()

        by_symbol: dict[str, pd.DataFrame] = {}
        for symbol, group in df.groupby("symbol", sort=False):
            g = group.sort_values("date").set_index("date")[_OHLCV_COLUMNS]
            g.index = pd.DatetimeIndex(g.index)
            by_symbol[symbol] = g
        self._by_symbol = by_symbol

        self.feature_snapshot_id = lineage.feature_snapshot_id
        self.swing20_snapshot_id = lineage.swing20_snapshot_id
        self.prices_hash = lineage.artifact_hashes["prices"]
        self.feature_dataset_hash = lineage.feature_dataset_hash

    def fetch_as_of(self, symbol: str, as_of_date: date, period: str = "2y") -> pd.DataFrame:
        """`period` is accepted for interface compatibility only -- the frozen
        snapshot already covers one fixed historical range; there is no rolling
        window to further restrict, and returning MORE history than a live fetch
        would is always safe (never a source of look-ahead), unlike returning
        less."""

        if as_of_date < self._min_date or as_of_date > self._max_date:
            raise FrozenDataRangeError(
                f"as_of_date {as_of_date} is outside the frozen artifact's own observed date "
                f"range [{self._min_date}, {self._max_date}] (SWING_20 snapshot "
                f"{self.swing20_snapshot_id!r})."
            )

        symbol_prices = self._by_symbol.get(symbol)
        if symbol_prices is None:
            return pd.DataFrame(columns=_OHLCV_COLUMNS)

        keep_mask = symbol_prices.index.date <= as_of_date
        result = symbol_prices.loc[keep_mask]
        if not result.empty and (result.index.date > as_of_date).any():
            # Defense-in-depth: the mask above already enforces this; this
            # assertion exists so a future refactor that weakens the mask fails
            # loudly instead of silently leaking a future bar.
            raise FrozenDataRangeError(
                f"internal invariant violated: a bar dated after {as_of_date} was about to be "
                f"returned for {symbol!r}."
            )
        return result
