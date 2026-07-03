"""Core-satellite portfolio construction with a regime overlay.

Emits explicit fixed target weights (unlike portfolio.build_portfolio, which
derives weights from the old rank and cannot hold a fixed core asset).

Layout:
  CORE      = fixed weight in broad ETF(s), equal-weighted among them
  SATELLITE = remaining weight, equal-weighted across the momentum picks
  OVERLAY   = when regime_ok is False, the satellite sleeve is parked in CASH
              (reduces equity exposure in downtrends -> smaller drawdowns)
"""

from __future__ import annotations

from typing import Any, Sequence

CASH = "CASH"


def build_core_satellite(
    core_assets: Sequence[str],
    satellite_symbols: Sequence[str],
    core_weight: float = 0.70,
    regime_ok: bool = True,
) -> list[dict[str, Any]]:
    """Return target positions as ``[{"symbol": ..., "weight": ...}, ...]``.

    Weights sum to 1.0. When ``regime_ok`` is False (or there are no satellite
    picks), the satellite sleeve (1 - core_weight) is held as CASH.
    """

    core_weight = max(0.0, min(1.0, float(core_weight)))
    weights: dict[str, float] = {}

    if core_assets:
        per_core = core_weight / len(core_assets)
        for symbol in core_assets:
            weights[symbol] = weights.get(symbol, 0.0) + per_core

    satellite_weight = 1.0 - core_weight
    if regime_ok and satellite_symbols and satellite_weight > 0:
        per_sat = satellite_weight / len(satellite_symbols)
        for symbol in satellite_symbols:
            weights[symbol] = weights.get(symbol, 0.0) + per_sat
    elif satellite_weight > 0:
        # regime off, or nothing to buy -> hold the sleeve in cash
        weights[CASH] = weights.get(CASH, 0.0) + satellite_weight

    positions = [{"symbol": s, "weight": w} for s, w in weights.items() if w > 0]
    if not positions:
        return [{"symbol": CASH, "weight": 1.0}]
    return positions
