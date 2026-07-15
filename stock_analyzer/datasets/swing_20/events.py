"""Event deduplication for SWING_20 labels."""

from __future__ import annotations

import pandas as pd


def deduplicate_positive_events(labels: pd.DataFrame) -> pd.DataFrame:
    """Group overlapping positive labels into economic events.

    For the same ticker, positive observations whose signal-date windows overlap are
    treated as one event. This keeps daily point-in-time observations available for model
    training while preventing practical metrics from treating one price move as many
    independent opportunities.

    Overlap is determined using each label's own ``window_end_date`` (the date of the
    actual last trading bar in its outcome window), not a ``signal_date + BDay(horizon)``
    approximation. Business-day arithmetic only skips weekends, so it silently
    under/overshoots the true window end whenever a market holiday or a missing ticker
    bar falls inside the horizon.
    """

    if labels.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "event_id",
                "start_date",
                "end_date",
                "raw_observation_count",
                "first_entry_date",
                "first_days_to_target",
            ]
        )
    required = {"symbol", "date", "entry_date", "window_end_date", "target_20pct_20d"}
    missing = required - set(labels.columns)
    if missing:
        raise ValueError(f"Labels frame is missing required columns: {sorted(missing)}")

    positives = labels[labels["target_20pct_20d"]].copy()
    if positives.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "event_id",
                "start_date",
                "end_date",
                "raw_observation_count",
                "first_entry_date",
                "first_days_to_target",
            ]
        )

    positives["date"] = pd.to_datetime(positives["date"])
    positives["entry_date"] = pd.to_datetime(positives["entry_date"])
    positives["window_end_date"] = pd.to_datetime(positives["window_end_date"])
    positives = positives.sort_values(["symbol", "date"])

    events: list[dict[str, object]] = []
    event_counter = 0

    for symbol, group in positives.groupby("symbol", sort=True):
        current_rows: list[pd.Series] = []
        current_end: pd.Timestamp | None = None

        for _, row in group.iterrows():
            date = pd.Timestamp(row["date"])
            window_end = pd.Timestamp(row["window_end_date"])
            if not current_rows:
                current_rows = [row]
                current_end = window_end
                continue

            if current_end is not None and date <= current_end:
                current_rows.append(row)
                current_end = max(current_end, window_end)
                continue

            event_counter += 1
            events.append(_event_from_rows(symbol, event_counter, current_rows, current_end))
            current_rows = [row]
            current_end = window_end

        if current_rows:
            event_counter += 1
            events.append(_event_from_rows(symbol, event_counter, current_rows, current_end))

    return pd.DataFrame(events)


def _event_from_rows(
    symbol: str,
    event_id: int,
    rows: list[pd.Series],
    current_end: pd.Timestamp | None,
) -> dict[str, object]:
    first = rows[0]
    return {
        "symbol": symbol,
        "event_id": event_id,
        "start_date": pd.Timestamp(first["date"]),
        "end_date": current_end,
        "raw_observation_count": len(rows),
        "first_entry_date": pd.Timestamp(first["entry_date"]),
        "first_days_to_target": first.get("days_to_target"),
    }


def event_summary(labels: pd.DataFrame, events: pd.DataFrame) -> dict[str, object]:
    """Return raw-vs-event summary metrics."""

    raw_positive = int(labels["target_20pct_20d"].sum()) if "target_20pct_20d" in labels else 0
    event_count = int(len(events))
    run_lengths = events["raw_observation_count"] if not events.empty else pd.Series(dtype=float)
    return {
        "raw_positive_observations": raw_positive,
        "deduplicated_positive_events": event_count,
        "raw_to_event_inflation_factor": (raw_positive / event_count if event_count else None),
        "median_positive_run_length": (float(run_lengths.median()) if not run_lengths.empty else None),
        "p90_positive_run_length": (float(run_lengths.quantile(0.90)) if not run_lengths.empty else None),
        "max_positive_run_length": (int(run_lengths.max()) if not run_lengths.empty else 0),
        "events_per_ticker": (
            events.groupby("symbol").size().sort_values(ascending=False).to_dict()
            if not events.empty
            else {}
        ),
        "top_10_ticker_event_share": _top_n_share(events, n=10),
    }


def _top_n_share(events: pd.DataFrame, n: int) -> float | None:
    if events.empty:
        return None
    counts = events.groupby("symbol").size().sort_values(ascending=False)
    return float(counts.head(n).sum() / counts.sum())

