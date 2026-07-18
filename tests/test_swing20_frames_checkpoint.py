from __future__ import annotations

import pandas as pd

from stock_analyzer.datasets.swing_20 import audit as audit_module
from stock_analyzer.datasets.swing_20 import prepare as prepare_module
from stock_analyzer.datasets.swing_20.audit import build_audit_frames
from stock_analyzer.datasets.swing_20.config import LabelConfig, Swing20Config, UniverseConfig
from stock_analyzer.datasets.swing_20.labels import label_frame as real_label_frame
from stock_analyzer.datasets.swing_20.prepare import _frames_checkpoint_path_for, prepare_frozen_dataset


def _price_frame(days: int = 260) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days)
    close = [10.0 + index * 0.02 for index in range(days)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [price * 1.01 for price in close],
            "Low": [price * 0.99 for price in close],
            "Close": close,
            "Volume": [1_000_000] * days,
        },
        index=dates,
    )


def _price_data(symbols: list[str]) -> dict[str, pd.DataFrame]:
    return {symbol: _price_frame() for symbol in symbols}


def test_build_audit_frames_checkpoints_periodically(tmp_path):
    checkpoint_path = tmp_path / "frames.pkl"
    price_data = _price_data(["AAA", "BBB", "CCC", "DDD", "EEE"])

    build_audit_frames(price_data, checkpoint_path=checkpoint_path, checkpoint_every=2)

    assert checkpoint_path.exists()


def test_build_audit_frames_resumes_without_recomputing(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "frames.pkl"
    price_data = _price_data(["AAA", "BBB", "CCC", "DDD"])

    # First pass only gets through AAA/BBB before "crashing" (checkpoint_every=1
    # so both are on disk), simulated by only handing it those two symbols.
    build_audit_frames({"AAA": price_data["AAA"], "BBB": price_data["BBB"]}, checkpoint_path=checkpoint_path, checkpoint_every=1)
    assert checkpoint_path.exists()

    calls: list[str] = []

    def _spy_label_frame(symbol, df, config=LabelConfig()):
        calls.append(symbol)
        return real_label_frame(symbol, df, config)

    monkeypatch.setattr(audit_module, "label_frame", _spy_label_frame)

    frames = build_audit_frames(price_data, checkpoint_path=checkpoint_path, checkpoint_every=1)

    # AAA and BBB must not be recomputed; only CCC/DDD are new work.
    assert set(calls) == {"CCC", "DDD"}
    # All 4 symbols' eligibility rows must still be present in the final result.
    assert set(frames["eligibility"]["symbol"]) == {"AAA", "BBB", "CCC", "DDD"}


def test_resumed_result_matches_single_pass_result(tmp_path):
    price_data = _price_data(["AAA", "BBB", "CCC", "DDD"])

    checkpoint_a = tmp_path / "single.pkl"
    single_pass = build_audit_frames(price_data, checkpoint_path=checkpoint_a, checkpoint_every=100)

    checkpoint_b = tmp_path / "resumed.pkl"
    build_audit_frames({"AAA": price_data["AAA"], "CCC": price_data["CCC"]}, checkpoint_path=checkpoint_b, checkpoint_every=1)
    resumed = build_audit_frames(price_data, checkpoint_path=checkpoint_b, checkpoint_every=1)

    # Row order (and therefore content) must match regardless of which
    # symbols were already checkpointed and in what order they were added.
    pd.testing.assert_frame_equal(
        single_pass["eligibility"].reset_index(drop=True),
        resumed["eligibility"].reset_index(drop=True),
    )
    pd.testing.assert_frame_equal(
        single_pass["labels"].reset_index(drop=True),
        resumed["labels"].reset_index(drop=True),
    )
    assert single_pass["quality_counts"] == resumed["quality_counts"]


def test_parallel_workers_produce_identical_result_to_sequential():
    price_data = _price_data(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"])

    sequential = build_audit_frames(price_data, workers=1)
    parallel = build_audit_frames(price_data, workers=3)

    pd.testing.assert_frame_equal(
        sequential["eligibility"].reset_index(drop=True),
        parallel["eligibility"].reset_index(drop=True),
    )
    pd.testing.assert_frame_equal(
        sequential["labels"].reset_index(drop=True),
        parallel["labels"].reset_index(drop=True),
    )
    assert sequential["quality_counts"] == parallel["quality_counts"]


def test_frames_checkpoint_path_is_fingerprinted_by_config(tmp_path):
    symbols = ["AAA", "BBB"]
    default_config = Swing20Config()
    different_universe = Swing20Config(universe=UniverseConfig(minimum_price=10.0))

    path_a = _frames_checkpoint_path_for(tmp_path, symbols, default_config)
    path_b = _frames_checkpoint_path_for(tmp_path, symbols, different_universe)

    assert path_a != path_b


def test_prepare_frozen_dataset_deletes_frames_checkpoint_after_success(tmp_path, monkeypatch):
    frame = _price_frame()

    def _fake_get_stock_data(symbol: str, period: str) -> pd.DataFrame:
        return frame

    monkeypatch.setattr(prepare_module, "get_stock_data", _fake_get_stock_data)

    symbols = ["AAA", "BBB"]
    manifest = prepare_frozen_dataset(
        symbols=symbols,
        period="5y",
        output_dir=tmp_path,
        storage_format="csv",
    )

    frames_checkpoint_path = _frames_checkpoint_path_for(tmp_path, symbols, Swing20Config())
    assert not frames_checkpoint_path.exists()
    assert manifest["symbol_count_with_prices"] == 2
