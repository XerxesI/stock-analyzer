"""Prepare frozen SWING_20 dataset artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pandas as pd

from stock_analyzer.data.data_fetcher import get_stock_data
from stock_analyzer.data.universe_filter import build_full_universe
from stock_analyzer.datasets.swing_20.artifacts import (
    StorageFormat,
    artifact_path,
    read_frame,
    read_manifest,
    write_frame,
    write_manifest,
)
from stock_analyzer.datasets.swing_20.audit import build_audit_frames
from stock_analyzer.datasets.swing_20.config import Swing20Config
from stock_analyzer.datasets.swing_20.universe import SymbolMetadata


def prepare_frozen_dataset(
    symbols: list[str] | None = None,
    period: str = "5y",
    output_dir: Path | str = Path("artifacts/swing_20"),
    storage_format: StorageFormat = "parquet",
    config: Swing20Config = Swing20Config(),
    max_symbols: int | None = None,
) -> dict[str, object]:
    """Build frozen universe, price, label, and manifest artifacts."""

    universe = _resolve_universe(symbols=symbols, max_symbols=max_symbols)
    metadata = _metadata_from_universe(universe)
    price_data = _fetch_price_data(universe["symbol"].tolist(), period=period)
    return write_frozen_dataset(
        price_data=price_data,
        universe=universe,
        metadata=metadata,
        period=period,
        output_dir=output_dir,
        storage_format=storage_format,
        config=config,
    )


def write_frozen_dataset(
    price_data: dict[str, pd.DataFrame],
    universe: pd.DataFrame,
    metadata: dict[str, SymbolMetadata] | None = None,
    period: str = "5y",
    output_dir: Path | str = Path("artifacts/swing_20"),
    storage_format: StorageFormat = "parquet",
    config: Swing20Config = Swing20Config(),
) -> dict[str, object]:
    """Write frozen SWING_20 artifacts from already loaded price data."""

    output_path = Path(output_dir)
    metadata = metadata or _metadata_from_universe(universe)
    frames = build_audit_frames(price_data, metadata=metadata, config=config)

    universe_path = artifact_path(output_path, "universe", storage_format)
    prices_path = artifact_path(output_path, "prices", storage_format)
    labels_path = artifact_path(output_path, "labels", storage_format)
    eligibility_path = artifact_path(output_path, "eligibility", storage_format)
    manifest_path = output_path / "manifest.json"

    prices = _price_data_to_frame(price_data)
    labels = frames["labels"]
    eligibility = frames["eligibility"]

    write_frame(universe, universe_path, storage_format)
    write_frame(prices, prices_path, storage_format)
    write_frame(labels, labels_path, storage_format)
    write_frame(eligibility, eligibility_path, storage_format)

    generated_at = datetime.now(UTC).replace(microsecond=0)
    price_symbols = {str(price_symbol).upper() for price_symbol in price_data}
    manifest = {
        "strategy": config.strategy,
        "spec_version": config.spec_version,
        "dataset_version": generated_at.strftime("swing20_%Y%m%dT%H%M%SZ"),
        "created_at": generated_at.isoformat(),
        "period": period,
        "storage_format": storage_format,
        "symbol_count_requested": int(len(universe)),
        "symbol_count_with_prices": int(len(price_data)),
        "symbols_without_prices": [
            str(symbol)
            for symbol in universe["symbol"].tolist()
            if str(symbol).upper() not in price_symbols
        ],
        "artifacts": {
            "universe": str(universe_path),
            "prices": str(prices_path),
            "labels": str(labels_path),
            "eligibility": str(eligibility_path),
        },
        "quality_counts": frames["quality_counts"],
        "limitations": [
            "Universe snapshot is based on current symbol availability unless a custom symbol list is supplied.",
            "OHLCV data comes from yfinance adjusted daily data.",
            "Corporate-action provenance is limited by the data source.",
        ],
    }
    write_manifest(manifest, manifest_path)
    manifest["manifest"] = str(manifest_path)
    return manifest


def load_frozen_dataset(dataset_dir: Path | str) -> dict[str, object]:
    """Load a frozen SWING_20 dataset written by ``write_frozen_dataset``."""

    dataset_path = Path(dataset_dir)
    manifest_path = dataset_path / "manifest.json"
    manifest = read_manifest(manifest_path)
    storage_format = cast(StorageFormat, manifest.get("storage_format"))
    artifacts = manifest.get("artifacts", {})
    if not isinstance(artifacts, dict):
        raise ValueError("Frozen dataset manifest has an invalid artifacts section.")

    def _read_artifact(name: str) -> pd.DataFrame:
        raw_path = artifacts.get(name)
        if not raw_path:
            return pd.DataFrame()
        path = Path(str(raw_path))
        if not path.is_absolute() and not path.exists():
            path = dataset_path / path.name
        return read_frame(path, storage_format=storage_format)

    return {
        "manifest": manifest,
        "universe": _read_artifact("universe"),
        "prices": _read_artifact("prices"),
        "labels": _read_artifact("labels"),
        "eligibility": _read_artifact("eligibility"),
        "quality_counts": manifest.get("quality_counts", {}),
    }


def _resolve_universe(symbols: list[str] | None, max_symbols: int | None) -> pd.DataFrame:
    if symbols:
        rows = [
            {
                "symbol": symbol.strip().upper(),
                "security_name": None,
                "exchange": None,
                "instrument_type": "COMMON_STOCK",
            }
            for symbol in symbols
            if symbol.strip()
        ]
        universe = pd.DataFrame(rows).drop_duplicates(subset="symbol")
    else:
        universe = build_full_universe().copy()
        universe["instrument_type"] = "COMMON_STOCK"

    if max_symbols is not None:
        universe = universe.head(max_symbols).copy()
    return universe.reset_index(drop=True)


def _metadata_from_universe(universe: pd.DataFrame) -> dict[str, SymbolMetadata]:
    metadata: dict[str, SymbolMetadata] = {}
    for _, row in universe.iterrows():
        symbol = str(row["symbol"]).upper()
        metadata[symbol] = SymbolMetadata(
            symbol=symbol,
            security_name=_optional_str(row.get("security_name")),
            exchange=_optional_str(row.get("exchange")),
            instrument_type=str(row.get("instrument_type") or "COMMON_STOCK"),
        )
    return metadata


def _fetch_price_data(symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
    price_data: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            price_data[symbol] = get_stock_data(symbol, period)
        except Exception:
            # Failed symbols remain visible by absence in manifest counts and universe artifact.
            continue
    return price_data


def _price_data_to_frame(price_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for symbol, df in price_data.items():
        frame = df.sort_index().copy().reset_index()
        date_col = frame.columns[0]
        frame = frame.rename(columns={date_col: "date"})
        frame.insert(0, "symbol", symbol)
        rows.append(frame)
    if not rows:
        return pd.DataFrame(columns=["symbol", "date", "Open", "High", "Low", "Close", "Volume"])
    return pd.concat(rows, ignore_index=True)


def _optional_str(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)
