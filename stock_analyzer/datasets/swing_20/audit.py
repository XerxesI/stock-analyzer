"""SWING_20 Dataset Audit orchestration."""

from __future__ import annotations

import pickle
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stock_analyzer.datasets.swing_20.artifacts import price_data_to_frame
from stock_analyzer.datasets.swing_20.baseline import baseline_summary
from stock_analyzer.datasets.swing_20.config import Swing20Config
from stock_analyzer.datasets.swing_20.events import deduplicate_positive_events, event_summary
from stock_analyzer.datasets.swing_20.labels import label_frame
from stock_analyzer.datasets.swing_20.quality import (
    DATA_QUALITY_EXCLUSION_REASON,
    decide_trainability,
    evaluate_symbol_price_quality,
    merge_counts,
    ohlcv_quality_counts,
)
from stock_analyzer.datasets.swing_20.schema import AuditResult
from stock_analyzer.datasets.swing_20.splits import assign_temporal_splits, split_summary
from stock_analyzer.datasets.swing_20.universe import SymbolMetadata, eligibility_frame, universe_summary

_EMPTY_QUARANTINE_SUMMARY: dict[str, object] = {
    "data_quality_excluded_symbol_count": 0,
    "data_quality_excluded_symbols": [],
    "data_quality_exclusion_reason_counts": {},
    "observations_removed_by_data_quality": 0,
    "positive_labels_removed_by_data_quality": 0,
    "events_removed_by_data_quality": 0,
}


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
        prices=price_data_to_frame(price_data),
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


def apply_data_quality_quarantine(
    labels: pd.DataFrame,
    eligibility: pd.DataFrame,
    prices: pd.DataFrame,
    quality_counts: dict[str, int],
    config: Swing20Config = Swing20Config(),
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int], dict[str, object]]:
    """Drop symbols whose price history is not economically interpretable.

    A symbol is quarantined -- its rows removed from ``labels`` and
    ``eligibility`` entirely, not just the offending rows -- when
    :func:`~stock_analyzer.datasets.swing_20.quality.evaluate_symbol_price_quality`
    flags any non-positive OHLC value, High below Low, or a material OHLC
    deviation anywhere in its history. A corrupt adjustment series (e.g. a
    botched reverse-split back-calculation) taints every return computed from
    it, not only the specific rows that fail the raw check, so the whole
    symbol goes, not a per-row patch.

    This never touches the frozen snapshot artifacts on disk: it filters the
    in-memory frames passed to it and is applied at audit time, after
    ``load_frozen_dataset`` / ``build_audit_frames`` have already run. The raw
    frozen ``prices``/``labels``/``eligibility`` still contain the quarantined
    symbol, preserving what the data source actually returned.

    Returns ``(clean_labels, clean_eligibility, clean_quality_counts, summary)``.
    ``clean_quality_counts`` recomputes the OHLC-derived counts (the ones
    ``decide_trainability`` gates on) from only the surviving symbols, so a
    quarantined symbol's bad prices can no longer trip the hard blocker. The
    label-generation diagnostics (``missing_entry_open_count`` and friends)
    are passed through unchanged: recomputing them would mean re-running
    ``label_frame`` per symbol, the exact CPU cost this quarantine exists to
    avoid paying twice, and one quarantined symbol's tiny contribution to
    those counts does not change their interpretation.
    """

    evaluation = evaluate_symbol_price_quality(
        prices,
        ohlc_absolute_tolerance=config.quality.ohlc_absolute_tolerance,
        ohlc_relative_tolerance=config.quality.ohlc_relative_tolerance,
    )
    quarantined = evaluation[evaluation["is_quarantined"]] if not evaluation.empty else evaluation
    quarantined_symbols = set(quarantined["symbol"]) if not quarantined.empty else set()

    if not quarantined_symbols:
        return labels, eligibility, quality_counts, dict(_EMPTY_QUARANTINE_SUMMARY)

    removed_labels = labels[labels["symbol"].isin(quarantined_symbols)] if not labels.empty else labels
    removed_positive = int(removed_labels["target_20pct_20d"].sum()) if not removed_labels.empty else 0
    removed_events = len(deduplicate_positive_events(removed_labels)) if not removed_labels.empty else 0

    clean_labels = labels[~labels["symbol"].isin(quarantined_symbols)].copy() if not labels.empty else labels
    clean_eligibility = (
        eligibility[~eligibility["symbol"].isin(quarantined_symbols)].copy()
        if not eligibility.empty
        else eligibility
    )

    clean_evaluation = evaluation[~evaluation["is_quarantined"]]
    clean_price_quality_counts = merge_counts(list(clean_evaluation["quality_counts"]))
    clean_quality_counts = {**quality_counts, **clean_price_quality_counts}

    excluded_symbols_detail = [
        {
            "symbol": row["symbol"],
            "exclusion_reason": DATA_QUALITY_EXCLUSION_REASON,
            "affected_row_count": int(row["affected_row_count"]),
            "non_positive_price_rows": int(row["non_positive_price_rows"]),
            "material_ohlc_inconsistency_rows": int(row["material_ohlc_inconsistency_rows"]),
            "first_affected_date": (
                str(row["first_affected_date"].date()) if row["first_affected_date"] is not None else None
            ),
            "last_affected_date": (
                str(row["last_affected_date"].date()) if row["last_affected_date"] is not None else None
            ),
        }
        for _, row in quarantined.sort_values("symbol").iterrows()
    ]

    summary = {
        "data_quality_excluded_symbol_count": len(quarantined_symbols),
        "data_quality_excluded_symbols": excluded_symbols_detail,
        "data_quality_exclusion_reason_counts": {DATA_QUALITY_EXCLUSION_REASON: len(quarantined_symbols)},
        "observations_removed_by_data_quality": int(len(removed_labels)),
        "positive_labels_removed_by_data_quality": removed_positive,
        "events_removed_by_data_quality": removed_events,
    }
    return clean_labels, clean_eligibility, clean_quality_counts, summary


_EMPTY_GAP_DIAGNOSTICS: dict[str, object] = {
    "excluded_row_count": 0,
    "excluded_by_split": {},
    "excluded_by_symbol": {},
    "observations_before": 0,
    "observations_after": 0,
    "raw_positive_before": 0,
    "raw_positive_after": 0,
    "positive_rate_before": None,
    "positive_rate_after": None,
    "deduplicated_events_before": 0,
    "deduplicated_events_after": 0,
}


def exclude_target_already_reached_at_entry(
    labels: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Drop rows where the target was already reached before the modeled entry.

    SWING_20's modeled strategy buys at the next trading day's Open. A row
    where that Open already sits at or above the target return relative to
    the signal-day Close (``target_already_reached_at_entry``, set in
    :func:`~stock_analyzer.datasets.swing_20.labels.label_at`) represents a
    move that happened *before* the model could have entered -- a live user
    could never have captured it. These rows are removed from the primary
    label population used for splits/events/the trainability decision (never
    scored as ordinary positives, and never recoded as negatives either),
    but nothing is deleted: the caller retains the full input frame, and this
    function reports before/after counts for diagnostics.
    """

    if labels.empty or "target_already_reached_at_entry" not in labels.columns:
        return labels, dict(_EMPTY_GAP_DIAGNOSTICS)

    excluded_mask = labels["target_already_reached_at_entry"] == True  # noqa: E712
    if not excluded_mask.any():
        summary = dict(_EMPTY_GAP_DIAGNOSTICS)
        summary["observations_before"] = summary["observations_after"] = int(len(labels))
        positives = int(labels["target_20pct_20d"].sum())
        summary["raw_positive_before"] = summary["raw_positive_after"] = positives
        rate = float(positives / len(labels)) if len(labels) else None
        summary["positive_rate_before"] = summary["positive_rate_after"] = rate
        events = len(deduplicate_positive_events(labels))
        summary["deduplicated_events_before"] = summary["deduplicated_events_after"] = events
        return labels, summary

    excluded = labels[excluded_mask]
    primary = labels[~excluded_mask].copy()

    events_before = len(deduplicate_positive_events(labels))
    events_after = len(deduplicate_positive_events(primary))
    positives_before = int(labels["target_20pct_20d"].sum())
    positives_after = int(primary["target_20pct_20d"].sum())

    summary = {
        "excluded_row_count": int(len(excluded)),
        "excluded_by_split": (
            {str(k): int(v) for k, v in excluded["split"].value_counts().items()}
            if "split" in excluded.columns
            else {}
        ),
        "excluded_by_symbol": {str(k): int(v) for k, v in excluded["symbol"].value_counts().items()},
        "observations_before": int(len(labels)),
        "observations_after": int(len(primary)),
        "raw_positive_before": positives_before,
        "raw_positive_after": positives_after,
        "positive_rate_before": float(positives_before / len(labels)) if len(labels) else None,
        "positive_rate_after": float(positives_after / len(primary)) if len(primary) else None,
        "deduplicated_events_before": events_before,
        "deduplicated_events_after": events_after,
    }
    return primary, summary


def run_audit_from_frames(
    labels: pd.DataFrame,
    eligibility: pd.DataFrame | None = None,
    quality_counts: dict[str, int] | None = None,
    config: Swing20Config = Swing20Config(),
    prices: pd.DataFrame | None = None,
) -> AuditResult:
    """Run the SWING_20 audit from frozen intermediate frames.

    If ``prices`` is given, a symbol-level data-quality quarantine runs first
    (see :func:`apply_data_quality_quarantine`): symbols with an economically
    invalid price history are dropped from ``labels`` and ``eligibility``
    before any further audit computation, so the decision, universe stats,
    splits, and baseline all reflect the model-eligible universe rather than
    the raw frozen one. Without ``prices`` (e.g. a caller that never loaded
    it), quarantine is skipped and the audit runs on the frames as given.
    """

    eligibility = eligibility if eligibility is not None else pd.DataFrame()
    quality_counts = dict(quality_counts or {})
    quarantine_summary = dict(_EMPTY_QUARANTINE_SUMMARY)
    if prices is not None and not prices.empty:
        labels, eligibility, quality_counts, quarantine_summary = apply_data_quality_quarantine(
            labels, eligibility, prices, quality_counts, config
        )

    warnings = [
        "UNIVERSE_MEMBERSHIP_NOT_POINT_IN_TIME",
        "SECTOR_NOT_POINT_IN_TIME",
        "MARKET_CAP_NOT_POINT_IN_TIME",
    ]
    if quarantine_summary["data_quality_excluded_symbol_count"]:
        warnings.append("DATA_QUALITY_SYMBOLS_QUARANTINED")

    labels_all = labels.copy()
    labels_with_splits = assign_temporal_splits(labels_all, config.splits) if not labels_all.empty else labels_all
    primary_labels, gap_diagnostics = exclude_target_already_reached_at_entry(labels_with_splits)
    if gap_diagnostics["excluded_row_count"]:
        warnings.append("TARGET_ALREADY_REACHED_AT_ENTRY_EXCLUDED")

    events = deduplicate_positive_events(primary_labels)

    splits = split_summary(primary_labels)
    events_info = event_summary(primary_labels, events)
    decision = decide_trainability(primary_labels, splits, quality_counts, warnings=warnings)

    date_range = _date_range(primary_labels)
    label_summary = _label_summary(primary_labels) | events_info
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
        universe=universe_summary(eligibility),
        labels=label_summary,
        splits=splits,
        baseline=baseline_summary(primary_labels),
        quality=quality,
        data_quality_quarantine=quarantine_summary,
        target_already_reached_at_entry=gap_diagnostics,
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
