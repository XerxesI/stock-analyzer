"""Shared helpers for the post-hoc diagnostics package -- Revision 5, Stage 12-13.

Not itself a diagnostic: `symbol_sessions`/`next_session`/`previous_session` are the
same frozen-OHLCV session-lookup primitives used by every diagnostic in this package
(originally written once in mfe_mae.py, Stage 12, and factored out here in Stage 13
once entry_timing/hold_quality/sell_quality/opportunity_cost all needed the identical
lookup). `full_market_calendar`/`compute_forward_horizon` implement Section 27's
censoring rule for every FIXED-HORIZON post-hoc outcome (Sections 21, 22, 23, 24) --
Section 20's MFE/MAE complete-path is not fixed-horizon and is not censored the same
way (its window is always the actual entry-to-exit/outcome-end span, see mfe_mae.py).

**Censoring classification (Section 27):** `is_censored` is true whenever the nominal
horizon extends past `outcome_data_end_date`, or a genuine gap exists in the frozen
source for this symbol within an otherwise in-window horizon. These are reported as
distinct reasons, never merged into one flag:

- `MISSING_MARKET_DATA` -- the full multi-symbol frozen calendar has a session date
  in [reference_date, outcome_data_end_date] that THIS symbol has no bar for. This
  takes priority when both conditions are present, since it is the more specific,
  "something is actually wrong with this symbol's data" case.
- `END_OF_EXPERIMENT` -- the horizon's nominal sessions run past
  `outcome_data_end_date` (or past the frozen calendar's own last date), and the
  symbol's own data is otherwise complete for whatever fell inside the window.

`sessions_observed`/`window` always reflect whatever was ACTUALLY observed -- a
censored observation is retained with its real partial data, never discarded or
faked as zero/complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

END_OF_EXPERIMENT = "END_OF_EXPERIMENT"
MISSING_MARKET_DATA = "MISSING_MARKET_DATA"


def symbol_sessions(prices_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Sorted, deduplicated OHLCV rows for one symbol, indexed by date."""

    rows = prices_df[prices_df["symbol"] == symbol].copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.date
    rows = rows.sort_values("date").drop_duplicates(subset="date", keep="last")
    return rows.set_index("date")[["Open", "High", "Low", "Close"]]


def next_session(sessions: pd.DataFrame, after: date) -> date | None:
    later = sessions.index[sessions.index > after]
    return later.min() if len(later) else None


def previous_session(sessions: pd.DataFrame, before: date) -> date | None:
    earlier = sessions.index[sessions.index < before]
    return earlier.max() if len(earlier) else None


def full_market_calendar(prices_df: pd.DataFrame) -> tuple[date, ...]:
    """The sorted, deduplicated union of session dates across EVERY symbol in the
    frozen prices artifact -- the same derivation manifest.compute_frozen_calendar
    uses (Section 5's `_outcome_only_dates` convention), reused here so a symbol's
    own gap can be told apart from the experiment simply having no more data at all
    on that date for anyone."""

    dates = pd.to_datetime(prices_df["date"]).dt.date.unique()
    return tuple(sorted(dates))


@dataclass(frozen=True)
class ForwardHorizon:
    window: pd.DataFrame
    sessions_observed: int
    is_censored: bool
    censoring_reason: str | None  # None, MISSING_MARKET_DATA, or END_OF_EXPERIMENT


def compute_forward_horizon(
    sessions: pd.DataFrame,
    calendar: tuple[date, ...],
    reference_date: date,
    horizon_sessions: int,
    outcome_data_end_date: date,
) -> ForwardHorizon:
    """The window of up to `horizon_sessions` calendar sessions strictly after
    `reference_date`, trimmed to `outcome_data_end_date`, with this symbol's
    actually-observed bars for those dates -- plus the Section 27 censoring
    classification for whatever was NOT observed."""

    later_dates = [d for d in calendar if d > reference_date]
    nominal_dates = later_dates[:horizon_sessions]
    in_window_dates = [d for d in nominal_dates if d <= outcome_data_end_date]

    window = sessions.loc[sessions.index.isin(in_window_dates)]
    sessions_observed = len(window)

    missing_bar_dates = [d for d in in_window_dates if d not in sessions.index]
    if missing_bar_dates:
        return ForwardHorizon(window, sessions_observed, True, MISSING_MARKET_DATA)
    if sessions_observed < horizon_sessions:
        return ForwardHorizon(window, sessions_observed, True, END_OF_EXPERIMENT)
    return ForwardHorizon(window, sessions_observed, False, None)
