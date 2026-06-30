"""Central runtime concurrency limits used across the application."""

from __future__ import annotations

import os


_CPU_COUNT = os.cpu_count() or 4

# Global cap so nested parallel flows do not explode total thread count.
GLOBAL_ANALYSIS_CONCURRENCY = max(4, min(24, _CPU_COUNT * 2))

# Per-batch worker caps.
ANALYSIS_BATCH_WORKERS = max(4, min(12, _CPU_COUNT * 2))
UNIVERSE_SCAN_WORKERS = max(2, min(6, _CPU_COUNT))
FETCH_CONCURRENCY = max(2, min(6, _CPU_COUNT))
