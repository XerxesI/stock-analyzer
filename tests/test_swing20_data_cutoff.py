from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from stock_analyzer.datasets.swing_20 import prepare as prepare_module
from stock_analyzer.datasets.swing_20.artifacts import read_frame
from stock_analyzer.datasets.swing_20.prepare import (
    _apply_current_day_cutoff,
    _current_ny_calendar_date,
    write_frozen_dataset,
)


def _daily_frame(dates: list[str]) -> pd.DataFrame:
    index = pd.to_datetime(dates)
    return pd.DataFrame(
        {
            "Open": [100.0] * len(dates),
            "High": [101.0] * len(dates),
            "Low": [99.0] * len(dates),
            "Close": [100.0] * len(dates),
            "Volume": [1_000] * len(dates),
        },
        index=index,
    )


def _universe_frame(symbol: str = "AAA") -> pd.DataFrame:
    return pd.DataFrame(
        [{"symbol": symbol, "security_name": None, "exchange": None, "instrument_type": "COMMON_STOCK"}]
    )


class _FixedDatetime(datetime):
    """A ``datetime`` stand-in whose ``now()`` always returns a fixed instant."""

    fixed_instant: datetime

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001 - matches datetime.now's signature
        return cls.fixed_instant.astimezone(tz) if tz is not None else cls.fixed_instant


def _freeze_now(monkeypatch, instant: datetime) -> None:
    frozen = type("_FrozenDatetime", (_FixedDatetime,), {"fixed_instant": instant})
    monkeypatch.setattr(prepare_module, "datetime", frozen)


def test_apply_current_day_cutoff_drops_only_todays_ny_date():
    df = _daily_frame(["2026-07-13", "2026-07-14", "2026-07-15"])
    cutoff = pd.Timestamp("2026-07-15").date()

    trimmed, removed_rows, affected, emptied = _apply_current_day_cutoff({"AAA": df}, cutoff)

    assert [str(d) for d in trimmed["AAA"].index.date] == ["2026-07-13", "2026-07-14"]
    assert removed_rows == 1
    assert affected == ["AAA"]
    assert emptied == []


def test_apply_current_day_cutoff_keeps_previous_day_bars_untouched():
    df = _daily_frame(["2026-07-13", "2026-07-14"])
    cutoff = pd.Timestamp("2026-07-15").date()

    trimmed, removed_rows, affected, emptied = _apply_current_day_cutoff({"AAA": df}, cutoff)

    assert len(trimmed["AAA"]) == 2
    assert removed_rows == 0
    assert affected == []
    assert emptied == []


def test_apply_current_day_cutoff_drops_symbol_left_with_no_bars():
    # A symbol whose only fetched bar is today's has nothing left after the cutoff.
    df = _daily_frame(["2026-07-15"])
    cutoff = pd.Timestamp("2026-07-15").date()

    trimmed, removed_rows, affected, emptied = _apply_current_day_cutoff({"AAA": df}, cutoff)

    assert "AAA" not in trimmed
    assert removed_rows == 1
    assert emptied == ["AAA"]


def test_current_ny_calendar_date_uses_new_york_timezone_not_local_clock(monkeypatch):
    # 2026-07-16 02:30 UTC is still 2026-07-15 22:30 in America/New_York
    # (EDT, UTC-4) -- the same absolute instant must resolve to the same NY
    # calendar date no matter what timezone the host machine is set to,
    # because the conversion never consults local time.
    _freeze_now(monkeypatch, datetime(2026, 7, 16, 2, 30, tzinfo=ZoneInfo("UTC")))

    assert _current_ny_calendar_date() == datetime(2026, 7, 15).date()


def test_write_frozen_dataset_excludes_current_ny_day_and_records_manifest_fields(tmp_path, monkeypatch):
    # 20:00 UTC on 2026-07-15 is 16:00 EDT the same day -- still "today" in NY.
    _freeze_now(monkeypatch, datetime(2026, 7, 15, 20, 0, tzinfo=ZoneInfo("UTC")))

    df = _daily_frame(["2026-07-13", "2026-07-14", "2026-07-15"])
    manifest = write_frozen_dataset(
        price_data={"AAA": df},
        universe=_universe_frame(),
        period="synthetic",
        output_dir=tmp_path,
        storage_format="csv",
    )

    assert manifest["data_cutoff_policy"] == "EXCLUDE_CURRENT_NEW_YORK_DATE"
    assert manifest["snapshot_market_timezone"] == "America/New_York"
    assert manifest["requested_end_date"] == "2026-07-15"
    assert manifest["effective_end_date"] == "2026-07-14"
    assert manifest["rows_removed_as_incomplete_current_day"] == 1
    assert manifest["symbols_affected_by_current_day_removal"] == ["AAA"]

    # Read the prices artifact directly rather than via load_frozen_dataset():
    # this synthetic 3-row frame is too short to produce any 20-day-horizon
    # labels, and an empty labels CSV is a separate, pre-existing edge case
    # unrelated to the cutoff behavior under test here.
    prices = read_frame(Path(manifest["artifacts"]["prices"]), storage_format="csv")
    price_dates = set(pd.to_datetime(prices["date"]).dt.strftime("%Y-%m-%d"))

    # The removed date must not appear anywhere in the frozen prices, so no
    # label (signal, entry, or future bar) can depend on it.
    assert "2026-07-15" not in price_dates
    assert price_dates == {"2026-07-13", "2026-07-14"}
