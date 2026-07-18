from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20 import prepare as prepare_module
from stock_analyzer.datasets.swing_20.prepare import (
    _checkpoint_path_for,
    _fetch_price_data,
    _load_checkpoint,
    prepare_frozen_dataset,
)


def _price_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=5)
    return pd.DataFrame(
        {
            "Open": [10.0] * 5,
            "High": [10.5] * 5,
            "Low": [9.5] * 5,
            "Close": [10.0] * 5,
            "Volume": [1_000] * 5,
        },
        index=dates,
    )


def _fake_get_stock_data(calls: list[str]):
    def _inner(symbol: str, period: str) -> pd.DataFrame:
        calls.append(symbol)
        return _price_frame()

    return _inner


def test_fetch_price_data_checkpoints_periodically(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(prepare_module, "get_stock_data", _fake_get_stock_data(calls))

    checkpoint_path = tmp_path / "fetch.pkl"
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]

    price_data, failures = _fetch_price_data(
        symbols, period="5y", checkpoint_path=checkpoint_path, progress_every=2, checkpoint_every=2
    )

    assert set(price_data) == set(symbols)
    assert failures == {}
    # A checkpoint must exist mid-run (checkpoint_every=2, 5 symbols -> at least one write).
    assert checkpoint_path.exists()


def test_fetch_price_data_resumes_from_checkpoint_without_refetching(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "fetch.pkl"

    # Simulate a prior run that got through AAA and BBB before crashing.
    prior_calls: list[str] = []
    monkeypatch.setattr(prepare_module, "get_stock_data", _fake_get_stock_data(prior_calls))
    _fetch_price_data(["AAA", "BBB"], period="5y", checkpoint_path=checkpoint_path, checkpoint_every=1)
    assert checkpoint_path.exists()

    # Resume with the full original symbol list; AAA/BBB must not be re-fetched.
    resumed_calls: list[str] = []
    monkeypatch.setattr(prepare_module, "get_stock_data", _fake_get_stock_data(resumed_calls))
    price_data, failures = _fetch_price_data(
        ["AAA", "BBB", "CCC", "DDD"], period="5y", checkpoint_path=checkpoint_path, checkpoint_every=1
    )

    assert resumed_calls == ["CCC", "DDD"]
    assert set(price_data) == {"AAA", "BBB", "CCC", "DDD"}
    assert failures == {}


def test_checkpoint_records_failures_too_so_they_are_not_retried_on_resume(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "fetch.pkl"

    def _first_run(symbol: str, period: str) -> pd.DataFrame:
        if symbol == "BAD":
            raise ValueError("boom")
        return _price_frame()

    monkeypatch.setattr(prepare_module, "get_stock_data", _first_run)
    _fetch_price_data(["AAA", "BAD"], period="5y", checkpoint_path=checkpoint_path, checkpoint_every=1)

    price_data, failures = _load_checkpoint(checkpoint_path)
    assert "BAD" in failures
    assert "AAA" in price_data

    resumed_calls: list[str] = []
    monkeypatch.setattr(prepare_module, "get_stock_data", _fake_get_stock_data(resumed_calls))
    _fetch_price_data(["AAA", "BAD", "CCC"], period="5y", checkpoint_path=checkpoint_path, checkpoint_every=1)

    # BAD already has a recorded failure; it must not be retried.
    assert resumed_calls == ["CCC"]


def test_checkpoint_path_is_fingerprinted_by_symbols_and_period(tmp_path):
    path_a = _checkpoint_path_for(tmp_path, ["AAA", "BBB"], "5y")
    path_b = _checkpoint_path_for(tmp_path, ["AAA", "CCC"], "5y")
    path_c = _checkpoint_path_for(tmp_path, ["AAA", "BBB"], "2y")

    assert path_a != path_b
    assert path_a != path_c


def test_prepare_frozen_dataset_deletes_checkpoint_after_success(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(prepare_module, "get_stock_data", _fake_get_stock_data(calls))

    manifest = prepare_frozen_dataset(
        symbols=["AAA", "BBB"],
        period="5y",
        output_dir=tmp_path,
        storage_format="csv",
    )

    checkpoint_path = _checkpoint_path_for(tmp_path, ["AAA", "BBB"], "5y")
    assert not checkpoint_path.exists()
    assert manifest["symbol_count_with_prices"] == 2
