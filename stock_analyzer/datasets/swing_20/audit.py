"""SWING_20 Dataset Audit orchestration."""

from __future__ import annotations

from datetime import datetime, timezone

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


def build_audit_frames(
    price_data: dict[str, pd.DataFrame],
    metadata: dict[str, SymbolMetadata] | None = None,
    config: Swing20Config = Swing20Config(),
) -> dict[str, object]:
    """Build reusable eligibility and label frames from in-memory OHLCV data."""

    metadata = metadata or {}
    all_eligibility: list[pd.DataFrame] = []
    all_labels: list[pd.DataFrame] = []
    label_quality_counts: list[dict[str, int]] = []
    price_quality_counts: list[dict[str, int]] = []

    for symbol, df in price_data.items():
        meta = metadata.get(symbol.upper(), SymbolMetadata(symbol=symbol.upper()))
        eligibility = eligibility_frame(symbol, df, config.universe, meta)
        all_eligibility.append(eligibility)
        price_quality_counts.append(ohlcv_quality_counts(df))

        labels_result = label_frame(symbol, df, config.label)
        label_quality_counts.append(labels_result.quality_counts)
        labels = labels_result.labels
        if not labels.empty and not eligibility.empty:
            eligibility_subset = eligibility[["date", "eligible", "exclusion_reason", "history_days", "price", "adv20"]]
            labels = labels.merge(eligibility_subset, on="date", how="left")
            labels = labels[labels["eligible"] == True].copy()  # noqa: E712 - explicit bool compare for pandas
        all_labels.append(labels)

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
    events = deduplicate_positive_events(labels_with_splits, config.label.horizon_days)

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
