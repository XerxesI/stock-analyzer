"""Validation framework per Research Protocol v1.2.

Modules:
    labeling  - ATR-scaled triple-barrier labels, High/Low-based MFE/MAE, R-multiple
    regime    - market regime tagging (trend + volatility), Phase 1: 2 dimensions
    ic_test   - walk-forward IC testing with multi-horizon support and hold-out split
    feature_stability - diagnostic (pre-registered hypothesis only) segment analysis
"""

from __future__ import annotations

from stock_analyzer.validation.labeling import (
    DEFAULT_LABELING_CONFIG,
    LabelingConfig,
    label_at,
    label_frame,
)
from stock_analyzer.validation.regime import build_market_regime, tag_observations
from stock_analyzer.validation.ic_test import (
    HorizonICResult,
    SegmentDiagnosticResult,
    diagnostic_segment_ic,
    quintile_summary,
    run_walk_forward_ic,
    spearman_ic,
    split_train_holdout,
    time_split,
)

__all__ = [
    "DEFAULT_LABELING_CONFIG",
    "LabelingConfig",
    "label_at",
    "label_frame",
    "build_market_regime",
    "tag_observations",
    "HorizonICResult",
    "SegmentDiagnosticResult",
    "diagnostic_segment_ic",
    "quintile_summary",
    "run_walk_forward_ic",
    "spearman_ic",
    "split_train_holdout",
    "time_split",
]
