"""Provisional sandbox operational defaults.

None of these are optimized investment rules -- see
docs/02_mvp/MVP_2_Recommendation_Sandbox_Specification.md sections 8, 9, 10, 18 and
docs/04_decisions/ADR-007-Next-Day-Entry-Simulation.md. They are frozen defaults for
MVP 2 and must not be tuned against validation or Locked Test data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

# Identifies the frozen Model 2 implementation this sandbox calls. Tied to the commit
# recorded in docs/09_experiments/EXP-003_SWING20_Locked_Test.md Part 1.
MODEL_VERSION = "swing20_model2@8857532adf518206cecc8c901866a128c9d170cf"

PRICE_DECIMALS = 4
MONEY_DECIMALS = 2


@dataclass(frozen=True)
class SandboxConfig:
    """Provisional, non-optimized sandbox policy constants (MVP 2 spec sections 8-11)."""

    max_close_extension_pct: float = 0.02
    atr_extension_multiple: float = 0.25
    entry_validity_sessions: int = 2
    virtual_notional: float = 1000.0
    target_return: float = 0.20
    holding_horizon_days: int = 20  # entry day = holding day 1 (see spec section 11)
    max_actionable_candidates: int = 3
    shadow_top_n: int = 10

    def config_hash(self) -> str:
        """Deterministic hash of this configuration, stored on every sandbox_runs row
        so a later run can detect whether the same policy was in effect."""

        canonical = json.dumps(asdict(self), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def round_price(value: float) -> float:
    return round(float(value), PRICE_DECIMALS)


def round_money(value: float) -> float:
    return round(float(value), MONEY_DECIMALS)
