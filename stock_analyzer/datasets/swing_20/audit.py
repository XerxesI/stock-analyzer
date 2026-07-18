"""SWING_20 Dataset Audit orchestration."""

from __future__ import annotations

import pickle
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stock_analyzer.datasets.swing_20.baseline import baseline_summary
from stock_analyzer.datasets.swing_20.config import Swing20Config
from stock_analyzer.datasets.swing_20.events import deduplicate_positive_events, event_summary
from stock_analyzer.datasets.swing_20.labels import label_frame
from stock_analyzer.datasets.swing_20.quality import decide_trainability, merge_counts, ohlcv_quality_counts
from stock_analyzer.datasets.swing_20.schema import AuditResult
from stock_analyzer.datasets.swing_20.splits import assign_temporal_splits, split_summary
from stock_analyzer.datasets.swing_20.universe import SymbolMetadata, eligibility_frame, universe_summary


def run_audit(
    price_data: dict[str, pd.DataFrame],
    metadata: dict[str, SymbolMetadata] | None = None,
    config: Swing20Config = Swing20Config(),
) -> AuditResult:
    """Run the SWING_20 audit over preloaded OHLCV data."""

    frames = build_audit_frames(price_data, metadata=metadata, config=config)
    return run_audit_from_frames(
        labels=frames["labels"],
        eligibility=frames["eligibility"],
        quality_counts=frames["quality_counts"],
        config=config,
    )


def _compute_symbol_frames(
    symbol: str,
    df: pd.DataFrame,
    meta: SymbolMetadata,
    config: Swing20Config,
) -> tuple[str, dict[str, object]]:
    """Compute one symbol's eligibility/label/quality result.

    Kept as a module-level function (not a closure) so it can be pickled and
    sent to worker processes by :func:`build_audit_frames` when ``workers>1``.
    """

    eligibility = eligibility_frame(symbol, df, config.universe, meta)
    price_quality = ohlcv_quality_counts(
        df,
        ohlc_absolute_tolerance=config.quality.ohlc_absolute_tolerance,
        ohlc_relative_tolerance=config.quality.ohlc_relative_tolerance,
    )

    labels_result = label_frame(symbol, df, config.label)
    labels = labels_result.labels
    if not labels.empty and not eligibility.empty:
        eligibility_subset = eligibility[["date", "eligible", "exclusion_reason", "history_days", "price", "adv20"]]
        labels = labels.merge(eligibility_subset, on="date", how="left")
        labels = labels[labels["eligible"] == True].copy()  # noqa: E712 - explicit bool compare for pandas

    return symbol, {
        "eligibility": eligibility,
        "labels": labels,
        "price_quality_counts": price_quality,
        "label_quality_counts": labels_result.quality_counts,
    }


def build_audit_frames(
    price_data: dict[str, pd.DataFrame],
    metadata: dict[str, SymbolMetadata] | None = None,
    config: Swing20Config = Swing20Config(),
    progress_every: int | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_every: int = 200,
    workers: int = 1,
) -> dict[str, object]:
    """Build reusable eligibility and label frames from in-memory OHLCV data.

    This computes eligibility, OHLCV quality counts, and labels per symbol,
    which is the CPU-bound step of building a snapshot -- for a large universe
    it can take much longer than the price fetch itself, since each symbol
    involves a Python-level loop over its trading history. Symbols are fully
    independent, so ``workers>1`` fans the work out across a
    :class:`~concurrent.futures.ProcessPoolExecutor` (threads would not help:
    this is CPU-bound pure-Python work serialized by the GIL). If
    ``progress_every`` is set, progress (symbols done/total, ETA) is printed
    every that many symbols. If ``checkpoint_path`` is given, per-symbol
    results are periodically pickled to disk (every ``checkpoint_every``
    symbols); if that file already exists, already-computed symbols are
    loaded from it and skipped, so an interrupted run resumes instead of
    recomputing every symbol from scratch.
    """

    metadata = metadata or {}
    per_symbol: dict[str, dict[str, object]] = {}
    remaining_symbols = list(price_data.keys())

    if checkpoint_path is not None and checkpoint_path.exists():
        per_symbol = _load_frames_checkpoint(checkpoint_path)
        already_done = set(per_symbol)
        remaining_symbols = [symbol for symbol in price_data if symbol not in already_done]
        print(
            f"[labels] resuming from checkpoint: {len(already_done)} symbols already done, "
            f"{len(remaining_symbols)} remaining.",
            flush=True,
        )

    total = len(price_data)
    total_remaining = len(remaining_symbols)
    start_time = time.monotonic()

    def _report_and_checkpoint(position: int) -> None:
        if progress_every and (position % progress_every == 0 or position == total_remaining):
            elapsed = time.monotonic() - start_time
            rate = position / elapsed if elapsed > 0 else 0
            remaining = total_remaining - position
            eta = f"{remaining / rate / 60:.1f} min" if rate > 0 else "unknown"
            print(
                f"[labels] {len(per_symbol)}/{total} symbols processed -- ETA {eta}",
                flush=True,
            )
        if checkpoint_path is not None and position % checkpoint_every == 0:
            _write_frames_checkpoint(checkpoint_path, per_symbol)

    if workers > 1 and remaining_symbols:
        print(f"[labels] using {workers} worker processes...", flush=True)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _compute_symbol_frames,
                    symbol,
                    price_data[symbol],
                    metadata.get(symbol.upper(), SymbolMetadata(symbol=symbol.upper())),
                    config,
                ): symbol
                for symbol in remaining_symbols
            }
            for position, future in enumerate(as_completed(futures), start=1):
                symbol, result = future.result()
                per_symbol[symbol] = result
                _report_and_checkpoint(position)
    else:
        for position, symbol in enumerate(remaining_symbols, start=1):
            meta = metadata.get(symbol.upper(), SymbolMetadata(symbol=symbol.upper()))
            _, result = _compute_symbol_frames(symbol, price_data[symbol], meta, config)
            per_symbol[symbol] = result
            _report_and_checkpoint(position)

    if checkpoint_path is not None and remaining_symbols:
        _write_frames_checkpoint(checkpoint_path, per_symbol)

    # Iterate price_data's own order (not per_symbol's insertion/completion
    # order, which is non-deterministic with workers>1) so the concatenated
    # row order -- and therefore the written file's bytes/hash -- is the same
    # regardless of resume or worker count.
    all_eligibility = [per_symbol[symbol]["eligibility"] for symbol in price_data]
    all_labels = [per_symbol[symbol]["labels"] for symbol in price_data]
    price_quality_counts = [per_symbol[symbol]["price_quality_counts"] for symbol in price_data]
    label_quality_counts = [per_symbol[symbol]["label_quality_counts"] for symbol in price_data]

    eligibility_frame_all = (
        pd.concat(all_eligibility, ignore_index=True) if all_eligibility else pd.DataFrame()
    )
    labels_all = pd.concat(all_labels, ignore_index=True) if all_labels else pd.DataFrame()
    quality_counts = merge_counts(price_quality_counts + label_quality_counts)
    return {
        "eligibility": eligibility_frame_all,
        "labels": labels_all,
        "quality_counts": quality_counts,
    }


def _write_frames_checkpoint(checkpoint_path: Path, per_symbol: dict[str, dict[str, object]]) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = checkpoint_path.with_suffix(".pkl.tmp")
    with tmp_path.open("wb") as handle:
        pickle.dump(per_symbol, handle)
    tmp_path.replace(checkpoint_path)


def _load_frames_checkpoint(checkpoint_path: Path) -> dict[str, dict[str, object]]:
    with checkpoint_path.open("rb") as handle:
        return pickle.load(handle)


def run_audit_from_frames(
    labels: pd.DataFrame,
    eligibility: pd.DataFrame | None = None,
    quality_counts: dict[str, int] | None = None,
    config: Swing20Config = Swing20Config(),
) -> AuditResult:
    """Run the SWING_20 audit from frozen intermediate frames."""

    warnings = [
        "UNIVERSE_MEMBERSHIP_NOT_POINT_IN_TIME",
        "SECTOR_NOT_POINT_IN_TIME",
        "MARKET_CAP_NOT_POINT_IN_TIME",
    ]

    labels_all = labels.copy()
    labels_with_splits = assign_temporal_splits(labels_all, config.splits) if not labels_all.empty else labels_all
    events = deduplicate_positive_events(labels_with_splits)

    quality_counts = quality_counts or {}
    splits = split_summary(labels_with_splits)
    events_info = event_summary(labels_with_splits, events)
    decision = decide_trainability(labels_with_splits, splits, quality_counts, warnings=warnings)

    date_range = _date_range(labels_with_splits)
    label_summary = _label_summary(labels_with_splits) | events_info
    quality = {
        "counts": quality_counts,
        "hard_blockers": decision.hard_blockers,
        "warnings": decision.warnings,
        "point_in_time_limitations": [
            "Current symbol sources may be survivorship-biased.",
            "Sector and market-cap metadata are not treated as point-in-time in MVP 1.",
            "yfinance adjusted OHLCV does not provide full corporate-action provenance.",
        ],
    }

    return AuditResult(
        strategy=config.strategy,
        spec_version=config.spec_version,
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        date_range=date_range,
        universe=universe_summary(eligibility if eligibility is not None else pd.DataFrame()),
        labels=label_summary,
        splits=splits,
        baseline=baseline_summary(labels_with_splits),
        quality=quality,
        decision=decision,
    )


def _date_range(labels: pd.DataFrame) -> dict[str, str | None]:
    if labels.empty or "date" not in labels.columns:
        return {"start": None, "end": None}
    dates = pd.to_datetime(labels["date"])
    return {"start": str(dates.min().date()), "end": str(dates.max().date())}


def _label_summary(labels: pd.DataFrame) -> dict[str, object]:
    if labels.empty:
        return {
            "observations": 0,
            "raw_positive_observations": 0,
            "positive_rate": None,
        }
    positives = int(labels["target_20pct_20d"].sum())
    return {
        "observations": int(len(labels)),
        "raw_positive_observations": positives,
        "positive_rate": float(positives / len(labels)) if len(labels) else None,
        "mfe_20d_median": float(labels["mfe_20d"].median()) if "mfe_20d" in labels else None,
        "mae_20d_median": float(labels["mae_20d"].median()) if "mae_20d" in labels else None,
        "close_return_20d_median": (
            float(labels["close_return_20d"].median()) if "close_return_20d" in labels else None
        ),
    }
