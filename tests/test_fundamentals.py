"""Unit tests for fundamentals scoring, focused on the sector risk factor."""

from __future__ import annotations

import stock_analyzer.data.fundamentals as fundamentals
from stock_analyzer.data.fundamentals import _sector_risk_scale, score_fundamental_factors

def _factors(debt_to_equity: float, sector: str) -> dict[str, float]:
    payload = {
        "pe": 20.0,
        "roe": 0.15,
        "profit_margin": 0.12,
        "revenue_growth": 0.1,
        "debt_to_equity": debt_to_equity,
    }
    result = score_fundamental_factors(payload, mode="balanced", sector=sector)
    return result["factors"]  # type: ignore[return-value]


def test_default_scale_is_not_degenerate():
    # A default of 2 made every non-special sector saturate the risk factor to 0.
    assert fundamentals.SECTOR_RISK_SCALES["default"] >= 100


def test_sector_risk_scale_covers_common_gics_sectors():
    assert _sector_risk_scale("Technology") == 150
    assert _sector_risk_scale("Communication Services") == 150
    assert _sector_risk_scale("Healthcare") == 100
    assert _sector_risk_scale("Consumer Defensive") == 100
    assert _sector_risk_scale("Consumer Cyclical") == 150
    assert _sector_risk_scale("Industrials") == 150
    assert _sector_risk_scale("Basic Materials") == 150
    assert _sector_risk_scale("Real Estate") == 300
    assert _sector_risk_scale("Financial Services") == 200
    assert _sector_risk_scale("Energy") == 300
    assert _sector_risk_scale("Utilities") == 300
    assert _sector_risk_scale("") == fundamentals.SECTOR_RISK_SCALES["default"]
    assert _sector_risk_scale(None) == fundamentals.SECTOR_RISK_SCALES["default"]


def test_risk_factor_differentiates_leverage_in_same_sector():
    # MSFT-like (D/E~30) vs NBIS-like (D/E~132) in tech-family sectors: the low-leverage
    # name must score a clearly higher (safer) risk factor than the high-leverage one.
    low_leverage = _factors(30.27, "Technology")["risk"]
    high_leverage = _factors(132.43, "Communication Services")["risk"]

    assert 0.0 < high_leverage < low_leverage < 1.0
    # High leverage lands in the moderately-low band rather than saturating to 0.00.
    assert 0.05 <= high_leverage <= 0.3
    # And the difference is material, not rounding noise.
    assert low_leverage - high_leverage > 0.3


def test_risk_factor_not_stuck_at_zero_for_moderate_leverage():
    # The pre-fix bug forced these both to 0.00; neither should now be degenerate.
    assert _factors(30.27, "Technology")["risk"] != 0.0
    assert _factors(132.43, "Communication Services")["risk"] != 0.0
