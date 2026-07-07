"""Signal Registry: each signal (market phenomenon) lives in its own module with a
minimal metadata dict, per Research Protocol v1.2 section 3.3.

This is intentionally lightweight (a plain dict, not a formal schema) until there
are ~10+ signals registered - see Protocol for the reasoning.
"""

from __future__ import annotations

SIGNALS: dict[str, dict] = {
    "rs1_vs_spy": {"category": "relative_strength", "requires_volume": False, "output": "continuous"},
    "rs_slope": {"category": "relative_strength", "requires_volume": False, "output": "continuous"},
    "rs_accel": {"category": "relative_strength", "requires_volume": False, "output": "continuous"},
}
