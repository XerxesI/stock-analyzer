"""Tests for the shared diagnostics helpers (Stage 13) -- Section 27's
END_OF_EXPERIMENT vs MISSING_MARKET_DATA censoring classification, and the
symbol-session lookup primitives factored out of mfe_mae.py.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from stock_analyzer.sandbox.exp005.diagnostics._shared import (
    END_OF_EXPERIMENT,
    MISSING_MARKET_DATA,
    compute_forward_horizon,
    full_market_calendar,
    symbol_sessions,
)

SYMBOL = "AAA"


def _bar(symbol: str, d: date, o: float, h: float, low: float, c: float) -> dict:
    return {"symbol": symbol, "date": pd.Timestamp(d), "Open": o, "High": h, "Low": low, "Close": c}


def test_full_market_calendar_is_the_sorted_deduplicated_union_across_symbols():
    prices = pd.DataFrame(
        [
            _bar("AAA", date(2026, 1, 5), 1, 1, 1, 1),
            _bar("AAA", date(2026, 1, 7), 1, 1, 1, 1),
            _bar("BBB", date(2026, 1, 5), 1, 1, 1, 1),
            _bar("BBB", date(2026, 1, 6), 1, 1, 1, 1),
        ]
    )
    calendar = full_market_calendar(prices)
    assert calendar == (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7))


def test_fully_observed_horizon_is_not_censored():
    calendar = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8))
    prices = pd.DataFrame(
        [_bar(SYMBOL, d, 10, 11, 9, 10) for d in calendar]
    )
    sessions = symbol_sessions(prices, SYMBOL)

    result = compute_forward_horizon(sessions, calendar, date(2026, 1, 5), 3, date(2026, 1, 8))

    assert result.is_censored is False
    assert result.censoring_reason is None
    assert result.sessions_observed == 3
    assert list(result.window.index) == [date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)]


def test_horizon_extending_past_outcome_data_end_date_is_end_of_experiment():
    calendar = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8))
    prices = pd.DataFrame([_bar(SYMBOL, d, 10, 11, 9, 10) for d in calendar])
    sessions = symbol_sessions(prices, SYMBOL)

    # horizon=3 from Jan 5 nominally wants Jan 6/7/8, but outcome_data_end_date=Jan 7
    result = compute_forward_horizon(sessions, calendar, date(2026, 1, 5), 3, date(2026, 1, 7))

    assert result.is_censored is True
    assert result.censoring_reason == END_OF_EXPERIMENT
    assert result.sessions_observed == 2
    assert list(result.window.index) == [date(2026, 1, 6), date(2026, 1, 7)]


def test_horizon_running_off_the_calendar_entirely_is_end_of_experiment():
    calendar = (date(2026, 1, 5), date(2026, 1, 6))
    prices = pd.DataFrame([_bar(SYMBOL, d, 10, 11, 9, 10) for d in calendar])
    sessions = symbol_sessions(prices, SYMBOL)

    result = compute_forward_horizon(sessions, calendar, date(2026, 1, 5), 5, date(2026, 1, 6))

    assert result.is_censored is True
    assert result.censoring_reason == END_OF_EXPERIMENT
    assert result.sessions_observed == 1


def test_symbol_specific_gap_within_window_is_missing_market_data():
    calendar = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8))
    # AAA has no bar for Jan 7 even though the overall calendar (from other symbols) does.
    prices = pd.DataFrame(
        [
            _bar(SYMBOL, date(2026, 1, 5), 10, 11, 9, 10),
            _bar(SYMBOL, date(2026, 1, 6), 10, 11, 9, 10),
            _bar(SYMBOL, date(2026, 1, 8), 10, 11, 9, 10),
        ]
    )
    sessions = symbol_sessions(prices, SYMBOL)

    result = compute_forward_horizon(sessions, calendar, date(2026, 1, 5), 3, date(2026, 1, 8))

    assert result.is_censored is True
    assert result.censoring_reason == MISSING_MARKET_DATA
    assert result.sessions_observed == 2  # Jan 6 and Jan 8 observed; Jan 7 missing


def test_missing_market_data_takes_priority_over_end_of_experiment():
    calendar = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8))
    # AAA has a gap at Jan 6 AND the horizon is also trimmed by outcome_data_end_date.
    prices = pd.DataFrame(
        [
            _bar(SYMBOL, date(2026, 1, 5), 10, 11, 9, 10),
            _bar(SYMBOL, date(2026, 1, 7), 10, 11, 9, 10),
        ]
    )
    sessions = symbol_sessions(prices, SYMBOL)

    result = compute_forward_horizon(sessions, calendar, date(2026, 1, 5), 3, date(2026, 1, 7))

    assert result.is_censored is True
    assert result.censoring_reason == MISSING_MARKET_DATA


def test_zero_available_calendar_sessions_after_reference_is_end_of_experiment_with_empty_window():
    calendar = (date(2026, 1, 5),)
    prices = pd.DataFrame([_bar(SYMBOL, date(2026, 1, 5), 10, 11, 9, 10)])
    sessions = symbol_sessions(prices, SYMBOL)

    result = compute_forward_horizon(sessions, calendar, date(2026, 1, 5), 3, date(2026, 1, 5))

    assert result.is_censored is True
    assert result.censoring_reason == END_OF_EXPERIMENT
    assert result.sessions_observed == 0
    assert result.window.empty
