"""Prepare frozen, versioned SWING_20 dataset artifacts.

Every call to :func:`prepare_frozen_dataset` / :func:`write_frozen_dataset`
writes into a brand-new, timestamp-versioned snapshot directory::

    <output_dir>/snapshots/<dataset_version>/
        manifest.json
        universe.parquet
        prices.parquet
        labels.parquet
        eligibility.parquet
        failures.parquet

A snapshot directory is never reused or overwritten by a later run, so a
past audit input can always be reproduced exactly, and download failures
are recorded (not silently dropped) so universe coverage gaps are
auditable rather than invisible.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import random
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pandas as pd

from stock_analyzer.data.data_fetcher import get_stock_data
from stock_analyzer.data.universe_filter import build_full_universe
from stock_analyzer.datasets.swing_20.artifacts import (
    StorageFormat,
    artifact_path,
    file_sha256,
    read_frame,
    read_manifest,
    write_frame,
    write_manifest,
)
from stock_analyzer.datasets.swing_20.audit import build_audit_frames
from stock_analyzer.datasets.swing_20.config import Swing20Config
from stock_analyzer.datasets.swing_20.universe import SymbolMetadata

DEFAULT_SAMPLE_SEED = 42

# A daily bar for the current session can be fetched mid-day, before High/Low
# have caught up with a live Open/Close snapshot -- yfinance does not mark it
# as provisional. Every snapshot therefore excludes the current US market
# calendar date unconditionally, so the frozen data is reproducible regardless
# of what time of day (or in what local timezone) it was built.
DATA_CUTOFF_POLICY = "EXCLUDE_CURRENT_NEW_YORK_DATE"
NY_TIMEZONE = ZoneInfo("America/New_York")


def prepare_frozen_dataset(
    symbols: list[str] | None = None,
    universe_source: str = "full_us",
    period: str = "5y",
    output_dir: Path | str = Path("artifacts/swing_20"),
    storage_format: StorageFormat = "parquet",
    config: Swing20Config = Swing20Config(),
    max_symbols: int | None = None,
    seed: int = DEFAULT_SAMPLE_SEED,
    progress_every: int = 50,
    checkpoint_every: int = 200,
) -> dict[str, object]:
    """Build a new frozen universe, price, label, and manifest snapshot.

    Fetching can take hours for the full US universe, so progress is logged
    every ``progress_every`` symbols and the in-progress fetch is checkpointed
    to disk every ``checkpoint_every`` symbols. If the process is interrupted
    or crashes mid-fetch, re-running with the same symbols/period resumes from
    the last checkpoint instead of re-fetching everything from scratch. The
    checkpoint is deleted once the snapshot has been written successfully.
    """

    universe = _resolve_universe(
        symbols=symbols, universe_source=universe_source, max_symbols=max_symbols, seed=seed
    )
    metadata = _metadata_from_universe(universe)
    requested_symbols = universe["symbol"].tolist()
    checkpoint_path = _checkpoint_path_for(Path(output_dir), requested_symbols, period)

    print(f"[phase 1/2] fetching prices for {len(requested_symbols)} symbols...", flush=True)
    fetch_start = time.monotonic()
    price_data, failures = _fetch_price_data(
        requested_symbols,
        period=period,
        checkpoint_path=checkpoint_path,
        progress_every=progress_every,
        checkpoint_every=checkpoint_every,
    )
    print(
        f"[phase 1/2] fetch complete in {time.monotonic() - fetch_start:.1f}s "
        f"({len(price_data)} ok, {len(failures)} failed)",
        flush=True,
    )

    print("[phase 2/2] building labels/eligibility and writing snapshot...", flush=True)
    build_start = time.monotonic()
    manifest = write_frozen_dataset(
        price_data=price_data,
        universe=universe,
        metadata=metadata,
        universe_source="symbols" if symbols else universe_source,
        period=period,
        output_dir=output_dir,
        storage_format=storage_format,
        config=config,
        failures=failures,
        sample_seed=seed if max_symbols is not None else None,
        progress_every=progress_every,
        checkpoint_every=checkpoint_every,
    )
    print(f"[phase 2/2] snapshot build complete in {time.monotonic() - build_start:.1f}s", flush=True)
    checkpoint_path.unlink(missing_ok=True)
    return manifest


def write_frozen_dataset(
    price_data: dict[str, pd.DataFrame],
    universe: pd.DataFrame,
    metadata: dict[str, SymbolMetadata] | None = None,
    universe_source: str = "custom",
    period: str = "5y",
    output_dir: Path | str = Path("artifacts/swing_20"),
    storage_format: StorageFormat = "parquet",
    config: Swing20Config = Swing20Config(),
    failures: dict[str, str] | None = None,
    sample_seed: int | None = None,
    progress_every: int = 50,
    checkpoint_every: int = 200,
) -> dict[str, object]:
    """Write a new, versioned SWING_20 snapshot from already loaded price data.

    Writes to ``<output_dir>/snapshots/<dataset_version>/`` where
    ``dataset_version`` is derived from the current UTC timestamp. If that
    directory already exists (e.g. two runs within the same second), a
    numeric suffix is appended until a fresh, never-before-used directory is
    found. A prior snapshot is therefore never silently overwritten.

    Any bar dated on or after today's US market calendar date (per
    :data:`DATA_CUTOFF_POLICY`) is dropped from ``price_data`` before labels
    and eligibility are computed, so the frozen snapshot never depends on a
    same-day bar that may still be incomplete.
    """

    root_dir = Path(output_dir)
    metadata = metadata or _metadata_from_universe(universe)
    failures = dict(failures or {})

    requested_end_date = _current_ny_calendar_date()
    price_data, cutoff_removed_rows, cutoff_affected_symbols, cutoff_emptied_symbols = (
        _apply_current_day_cutoff(price_data, requested_end_date)
    )
    for symbol in cutoff_emptied_symbols:
        failures.setdefault(symbol, "EMPTY_AFTER_CURRENT_DAY_CUTOFF")
    effective_end_date = _max_price_date(price_data)
    if cutoff_removed_rows:
        print(
            f"[build] current-day cutoff removed {cutoff_removed_rows} row(s) across "
            f"{len(cutoff_affected_symbols)} symbol(s) (effective end date {effective_end_date}).",
            flush=True,
        )

    frames_checkpoint_path = _frames_checkpoint_path_for(root_dir, list(price_data.keys()), config)
    print(f"[build] computing labels/eligibility for {len(price_data)} symbols...", flush=True)
    labels_start = time.monotonic()
    frames = build_audit_frames(
        price_data,
        metadata=metadata,
        config=config,
        progress_every=progress_every,
        checkpoint_path=frames_checkpoint_path,
        checkpoint_every=checkpoint_every,
    )
    print(f"[build] labels/eligibility done in {time.monotonic() - labels_start:.1f}s", flush=True)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0)
    base_version = generated_at.strftime("swing20_%Y%m%dT%H%M%SZ")
    snapshot_dir, dataset_version = _allocate_snapshot_dir(root_dir, base_version)

    universe_path = artifact_path(snapshot_dir, "universe", storage_format)
    prices_path = artifact_path(snapshot_dir, "prices", storage_format)
    labels_path = artifact_path(snapshot_dir, "labels", storage_format)
    eligibility_path = artifact_path(snapshot_dir, "eligibility", storage_format)
    failures_path = artifact_path(snapshot_dir, "failures", storage_format)
    manifest_path = snapshot_dir / "manifest.json"

    print(f"[build] writing artifacts to {snapshot_dir}...", flush=True)
    write_start = time.monotonic()
    prices = _price_data_to_frame(price_data)
    labels = frames["labels"]
    eligibility = frames["eligibility"]
    failures_frame = _failures_to_frame(failures)

    write_frame(universe, universe_path, storage_format)
    write_frame(prices, prices_path, storage_format)
    write_frame(labels, labels_path, storage_format)
    write_frame(eligibility, eligibility_path, storage_format)
    write_frame(failures_frame, failures_path, storage_format)
    print(f"[build] artifacts written in {time.monotonic() - write_start:.1f}s", flush=True)

    artifact_paths = {
        "universe": universe_path,
        "prices": prices_path,
        "labels": labels_path,
        "eligibility": eligibility_path,
        "failures": failures_path,
    }
    print("[build] computing SHA-256 hashes...", flush=True)
    hash_start = time.monotonic()
    artifact_hashes = {name: file_sha256(path) for name, path in artifact_paths.items()}
    print(f"[build] hashing done in {time.monotonic() - hash_start:.1f}s", flush=True)

    price_symbols = {str(price_symbol).upper() for price_symbol in price_data}
    requested_symbols = [str(symbol).upper() for symbol in universe["symbol"].tolist()]
    symbols_without_prices = [
        symbol
        for symbol in requested_symbols
        if symbol not in price_symbols and symbol not in failures
    ]

    manifest = {
        "strategy": config.strategy,
        "spec_version": config.spec_version,
        "dataset_version": dataset_version,
        "created_at": generated_at.isoformat(),
        "period": period,
        "universe_source": universe_source,
        "sample_seed": sample_seed,
        "storage_format": storage_format,
        "symbol_count_requested": int(len(universe)),
        "symbol_count_with_prices": int(len(price_data)),
        "symbol_count_failed": len(failures),
        "symbols_without_prices": symbols_without_prices,
        "data_cutoff_policy": DATA_CUTOFF_POLICY,
        "snapshot_market_timezone": "America/New_York",
        "requested_end_date": str(requested_end_date),
        "effective_end_date": str(effective_end_date) if effective_end_date is not None else None,
        "rows_removed_as_incomplete_current_day": cutoff_removed_rows,
        "symbols_affected_by_current_day_removal": cutoff_affected_symbols,
        "artifacts": {name: str(path) for name, path in artifact_paths.items()},
        "artifact_hashes": artifact_hashes,
        "quality_counts": frames["quality_counts"],
        "provenance": _provenance(),
        "limitations": [
            "Universe snapshot is based on current symbol availability unless a custom symbol list is supplied.",
            "OHLCV data comes from yfinance adjusted daily data.",
            "Corporate-action provenance is limited by the data source.",
        ],
    }
    write_manifest(manifest, manifest_path)
    manifest["manifest"] = str(manifest_path)
    manifest["snapshot_dir"] = str(snapshot_dir)
    frames_checkpoint_path.unlink(missing_ok=True)
    return manifest


def load_frozen_dataset(dataset_dir: Path | str) -> dict[str, object]:
    """Load a frozen SWING_20 snapshot written by ``write_frozen_dataset``.

    ``dataset_dir`` must be a specific snapshot directory (as returned in
    ``manifest["snapshot_dir"]``), not the shared ``artifacts/swing_20``
    root that contains multiple snapshots.
    """

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
        if not path.exists():
            return pd.DataFrame()
        return read_frame(path, storage_format=storage_format)

    return {
        "manifest": manifest,
        "universe": _read_artifact("universe"),
        "prices": _read_artifact("prices"),
        "labels": _read_artifact("labels"),
        "eligibility": _read_artifact("eligibility"),
        "failures": _read_artifact("failures"),
        "quality_counts": manifest.get("quality_counts", {}),
    }


def verify_frozen_dataset(dataset_dir: Path | str) -> dict[str, bool]:
    """Recompute artifact hashes and compare them against the manifest.

    Returns a mapping of artifact name to whether its on-disk content still
    matches the SHA-256 hash recorded in ``manifest.json`` at snapshot time.
    A missing hash entry (e.g. a snapshot written before this check existed)
    or a missing file counts as a failed check.
    """

    dataset_path = Path(dataset_dir)
    manifest = read_manifest(dataset_path / "manifest.json")
    recorded_hashes = manifest.get("artifact_hashes", {})
    artifacts = manifest.get("artifacts", {})
    if not isinstance(artifacts, dict) or not isinstance(recorded_hashes, dict):
        return {}

    results: dict[str, bool] = {}
    for name, raw_path in artifacts.items():
        path = Path(str(raw_path))
        if not path.is_absolute() and not path.exists():
            path = dataset_path / path.name
        if not path.exists():
            results[name] = False
            continue
        expected = recorded_hashes.get(name)
        results[name] = expected is not None and file_sha256(path) == expected
    return results


def _allocate_snapshot_dir(root_dir: Path, base_version: str) -> tuple[Path, str]:
    """Create and return a fresh, never-before-used snapshot directory."""

    snapshots_root = root_dir / "snapshots"
    candidate_version = base_version
    attempt = 0
    while True:
        candidate_dir = snapshots_root / candidate_version
        try:
            candidate_dir.mkdir(parents=True, exist_ok=False)
            return candidate_dir, candidate_version
        except FileExistsError:
            attempt += 1
            candidate_version = f"{base_version}-{attempt}"


def _current_ny_calendar_date() -> date:
    """Return today's calendar date in the US market timezone.

    Always converts via :data:`NY_TIMEZONE` rather than consulting the host
    machine's local timezone, so the result is the same regardless of where
    the snapshot job runs.
    """

    return datetime.now(NY_TIMEZONE).date()


def _apply_current_day_cutoff(
    price_data: dict[str, pd.DataFrame],
    cutoff_date: date,
) -> tuple[dict[str, pd.DataFrame], int, list[str], list[str]]:
    """Drop any bar dated on or after ``cutoff_date`` from every symbol's frame.

    Returns the trimmed price data, the total number of rows removed, the
    symbols that had at least one row removed, and the subset of those left
    with no bars at all (e.g. a symbol whose only fetched bar was today's).
    """

    cutoff_ts = pd.Timestamp(cutoff_date)
    trimmed: dict[str, pd.DataFrame] = {}
    removed_rows = 0
    affected_symbols: list[str] = []
    emptied_symbols: list[str] = []

    for symbol, df in price_data.items():
        index_dates = pd.DatetimeIndex(df.index)
        if index_dates.tz is not None:
            index_dates = index_dates.tz_localize(None)
        keep_mask = index_dates < cutoff_ts
        removed = int((~keep_mask).sum())
        if removed:
            removed_rows += removed
            affected_symbols.append(symbol)
        filtered = df.loc[keep_mask]
        if filtered.empty:
            emptied_symbols.append(symbol)
            continue
        trimmed[symbol] = filtered

    return trimmed, removed_rows, sorted(affected_symbols), sorted(emptied_symbols)


def _max_price_date(price_data: dict[str, pd.DataFrame]) -> date | None:
    """Return the latest bar date across every symbol's frame, or ``None`` if empty."""

    max_date: date | None = None
    for df in price_data.values():
        if df.empty:
            continue
        candidate = pd.Timestamp(df.index.max()).date()
        if max_date is None or candidate > max_date:
            max_date = candidate
    return max_date


def _resolve_universe(
    symbols: list[str] | None,
    universe_source: str,
    max_symbols: int | None,
    seed: int = DEFAULT_SAMPLE_SEED,
) -> pd.DataFrame:
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
    elif universe_source == "full_us":
        universe = build_full_universe().copy()
        universe["instrument_type"] = "COMMON_STOCK"
    else:
        raise ValueError(f"Unsupported universe source: {universe_source}")

    universe = universe.reset_index(drop=True)
    if max_symbols is not None and max_symbols < len(universe):
        universe = _deterministic_sample(universe, max_symbols, seed)
    return universe.reset_index(drop=True)


def _deterministic_sample(universe: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """Return a reproducible random (not positional) subset of ``universe``.

    Using ``head(n)`` would silently bias the sample toward whatever order
    the upstream source happens to list symbols in (e.g. alphabetical, which
    over-weights early letters). A seeded random sample is reproducible
    across runs with the same ``seed`` while avoiding that ordering bias.
    """

    symbols = [str(symbol) for symbol in universe["symbol"].tolist()]
    rng = random.Random(seed)
    chosen = set(rng.sample(symbols, n))
    return universe[universe["symbol"].isin(chosen)].copy()


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


def _fetch_price_data(
    symbols: list[str],
    period: str,
    checkpoint_path: Path | None = None,
    progress_every: int = 50,
    checkpoint_every: int = 200,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Fetch OHLCV data per symbol, recording (not discarding) any failure.

    Every symbol that does not end up with usable price data gets an entry
    in the returned failures mapping describing why, so download coverage
    gaps are traceable in the frozen snapshot instead of silently vanishing.

    A large universe can take hours to fetch one symbol at a time. Progress
    (symbols done / total, ok vs. failed, ETA) is printed every
    ``progress_every`` symbols. If ``checkpoint_path`` is given, accumulated
    results are pickled to disk every ``checkpoint_every`` symbols; if that
    file already exists when this is called, already-fetched/failed symbols
    are loaded from it and skipped, so an interrupted run resumes instead of
    starting over.
    """

    price_data: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    if checkpoint_path is not None and checkpoint_path.exists():
        price_data, failures = _load_checkpoint(checkpoint_path)
        already_done = set(price_data) | set(failures)
        symbols = [symbol for symbol in symbols if symbol not in already_done]
        print(
            f"[fetch] resuming from checkpoint: {len(already_done)} symbols already done, "
            f"{len(symbols)} remaining.",
            flush=True,
        )

    total = len(price_data) + len(failures) + len(symbols)
    start_time = time.monotonic()
    for position, symbol in enumerate(symbols, start=1):
        try:
            df = get_stock_data(symbol, period)
        except Exception as exc:  # noqa: BLE001 - every failure reason must be recorded, never swallowed
            failures[symbol] = f"{type(exc).__name__}: {exc}"
        else:
            if df is None or df.empty:
                failures[symbol] = "EMPTY_OR_MISSING_DATA"
            else:
                price_data[symbol] = df

        if position % progress_every == 0 or position == len(symbols):
            done = len(price_data) + len(failures)
            elapsed = time.monotonic() - start_time
            rate = position / elapsed if elapsed > 0 else 0
            remaining = len(symbols) - position
            eta = f"{remaining / rate / 60:.1f} min" if rate > 0 else "unknown"
            print(
                f"[fetch] {done}/{total} symbols processed "
                f"({len(price_data)} ok, {len(failures)} failed) -- ETA {eta}",
                flush=True,
            )

        if checkpoint_path is not None and position % checkpoint_every == 0:
            _write_checkpoint(checkpoint_path, price_data, failures)

    if checkpoint_path is not None and symbols:
        _write_checkpoint(checkpoint_path, price_data, failures)

    return price_data, failures


def _checkpoint_path_for(output_dir: Path, symbols: list[str], period: str) -> Path:
    """Return a stable checkpoint path fingerprinted by the exact fetch request.

    Fingerprinting on the resolved symbol list and period (rather than a fixed
    filename) means a checkpoint from a different universe/sample/period is
    never mistakenly resumed from.
    """

    fingerprint = hashlib.sha256(("|".join(sorted(symbols)) + "|" + period).encode("utf-8")).hexdigest()[:16]
    return output_dir / "_checkpoints" / f"fetch_{fingerprint}.pkl"


def _frames_checkpoint_path_for(output_dir: Path, symbols: list[str], config: Swing20Config) -> Path:
    """Return a stable checkpoint path fingerprinted by symbols and label/eligibility config.

    Unlike the raw price fetch, computed eligibility/labels depend on
    ``config`` (thresholds, target definition, tolerances). Fingerprinting on
    a canonical dump of the config too means a checkpoint from a prior run
    with different settings is never mistakenly reused.
    """

    config_key = json.dumps(asdict(config), sort_keys=True, default=str)
    fingerprint = hashlib.sha256(("|".join(sorted(symbols)) + "|" + config_key).encode("utf-8")).hexdigest()[:16]
    return output_dir / "_checkpoints" / f"frames_{fingerprint}.pkl"


def _write_checkpoint(
    checkpoint_path: Path,
    price_data: dict[str, pd.DataFrame],
    failures: dict[str, str],
) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = checkpoint_path.with_suffix(".pkl.tmp")
    with tmp_path.open("wb") as handle:
        pickle.dump({"price_data": price_data, "failures": failures}, handle)
    tmp_path.replace(checkpoint_path)


def _load_checkpoint(checkpoint_path: Path) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    with checkpoint_path.open("rb") as handle:
        payload = pickle.load(handle)
    return payload["price_data"], payload["failures"]


def _failures_to_frame(failures: dict[str, str]) -> pd.DataFrame:
    if not failures:
        return pd.DataFrame(columns=["symbol", "reason"])
    return pd.DataFrame(
        [{"symbol": symbol, "reason": reason} for symbol, reason in sorted(failures.items())]
    )


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


def _provenance() -> dict[str, object]:
    """Best-effort reproducibility metadata: code version and dependency versions."""

    return {
        "git_commit": _git_commit(),
        "python_version": sys.version.split()[0],
        "pandas_version": pd.__version__,
        "yfinance_version": _yfinance_version(),
    }


def _git_commit() -> str | None:
    repo_root = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    commit = result.stdout.strip()
    return commit or None


def _yfinance_version() -> str | None:
    try:
        import yfinance as yf
    except Exception:
        return None
    return getattr(yf, "__version__", None)
