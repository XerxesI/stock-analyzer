"""Reusable symbol universes for screening and opportunity finding.

STRUCTURE:
- CORE: Baseline broad markets
- SECTOR: Stable sector plays
- THEMATIC: High-growth, theme-based opportunities
- EXPERIMENTAL: Problematic/untested markets
"""

from __future__ import annotations

from typing import Sequence


UNIVERSES: dict[str, list[str]] = {
    # ============================================================
    # 🟢 CORE - Baseline broad markets
    # ============================================================
    "sp500": [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "META",
        "GOOGL",
        "GOOG",
        "TSLA",
        "AVGO",
        "BRK-B",
        "LLY",
        "JPM",
        "V",
        "UNH",
        "XOM",
        "MA",
        "COST",
        "NFLX",
        "AMD",
        "ADBE",
    ],
    "nasdaq": [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "META",
        "GOOGL",
        "GOOG",
        "TSLA",
        "AMD",
        "NFLX",
        "ADBE",
        "INTC",
        "QCOM",
        "CSCO",
        "ORCL",
        "AVGO",
        "MU",
        "PANW",
        "SHOP",
        "CRWD",
    ],
    "europe": [
        "SAP.DE",
        "ASML.AS",
        "SIE.DE",
        "BMW.DE",
        "BBVA.MC",
        "UNVR.AS",
        "AZN.L",
        "EDF.PA",
        "TTE.PA",
        "NOVN.VX",
    ],
    # ============================================================
    # 🔵 SECTOR - Stable industry sectors
    # ============================================================
    "semiconductor": [
        "NVDA",
        "AMD",
        "INTC",
        "ARM",
        "AVGO",
        "MU",
        "ASML",
        "AMAT",
        "LRCX",
        "KLAC",
        "ON",
    ],
    "energy": [
        "NEE",
        "EXC",
        "DUK",
        "SO",
        "AEP",
        "XEL",
        "D",
        "WEC",
        "EIX",
        "AWK",
    ],
    "biotech_genomics": [
        "CRSP",
        "EDIT",
        "BEAM",
        "DNA",
        "NTLA",
        "VRTX",
        "REGN",
        "BIIB",
        "GILD",
        "AMGN",
    ],
    "cloud_saas": [
        "SNOW",
        "CRM",
        "DDOG",
        "NET",
        "OKTA",
        "MDB",
        "ZS",
        "TEAM",
        "PSTG",
        "UBER",
    ],
    "cybersecurity": [
        "CRWD",
        "PANW",
        "ZS",
        "FTNT",
        "S",
        "NET",
        "OKTA",
        "CHKP",
        "ALRM",
        "RNG",
    ],
    # ============================================================
    # 🟣 THEMATIC - High-growth themes
    # ============================================================
    "ai": [
        "NVDA",
        "AMD",
        "MSFT",
        "GOOGL",
        "META",
        "TSLA",
        "AVGO",
        "MSTR",
        "PLTR",
        "CRM",
        "SNOW",
        "UPST",
        "SMCI",
        "DELL",
        "INTC",
        "QCOM",
        "ARM",
        "SNPS",
        "ANET",
        "DDOG",
    ],
    "ai_datacenters": [
        "SMCI",
        "AVGO",
        "EQIX",
        "DLR",
        "CCI",
        "AMZN",
        "MSFT",
        "GOOGL",
        "META",
        "NVDA",
    ],
    "emerging_tech": [
        "PLTR",
        "UPST",
        "CRWD",
        "NET",
        "DDOG",
        "OKTA",
        "SMCI",
        "EQIX",
        "DLR",
        "CCI",
    ],
    "ev": [
        "TSLA",
        "RIVN",
        "LCID",
        "NIO",
        "XPEV",
        "BYDDF",
        "GM",
        "F",
        "VWAGY",
        "HYMTF",
    ],
    "energy_storage": [
        "TSLA",
        "ENPH",
        "FLNC",
        "STEM",
        "QS",
        "FREY",
        "PLUG",
        "BLNK",
        "CHPT",
        "EVGO",
    ],
    "robotics": [
        "ISRG",
        "IRBT",
        "ABB",
        "FANUY",
        "TER",
        "ADSK",
        "KTOS",
        "RBOT",
        "KUKA",
        "SYMM",
    ],
    "quantum": [
        "IONQ",
        "RGTI",
        "QBTS",
        "IBM",
        "GOOG",
        "MSFT",
        "INTC",
        "D-Wave",
        "AAPL",
        "AMZN",
    ],
    "drones": [
        "ACHR",
        "JOBY",
        "AVAV",
        "TXT",
        "GD",
        "NOC",
        "LMT",
        "RTX",
        "IRDM",
        "BA",
    ],
    "nuclear_energy": [
        "EXC",
        "NEE",
        "DUK",
        "SO",
        "UEC",
        "CCJ",
        "PII",
        "OKLO",
        "LEU",
        "URG",
    ],
    "renewable_energy": [
        "NEE",
        "PLUG",
        "FSLR",
        "ENPH",
        "RUN",
        "CWEN",
        "AERI",
        "RGEN",
        "AQN",
        "ICLN",
    ],
    # ============================================================
    # 🔴 EXPERIMENTAL - Problematic/risky markets
    # ============================================================
    "global_asia": [
        "SSNLF",
        "TSM",
        "SONY",
        "TM",
        "HMC",
        "NSANY",
        "NTDOY",
        "UNICY",
        "VWAGY",
        "CHL",
    ],
}


UNIVERSES_META: dict[str, dict[str, str | float]] = {
    # CORE universes - baseline, low volatility, fundamental
    "sp500": {"category": "core", "risk": "medium", "volatility": "medium"},
    "nasdaq": {"category": "core", "risk": "medium-high", "volatility": "medium-high"},
    "europe": {"category": "core", "risk": "medium", "volatility": "medium"},
    # SECTOR universes - stable, sector-focused
    "semiconductor": {"category": "sector", "risk": "high", "volatility": "high"},
    "energy": {"category": "sector", "risk": "high", "volatility": "high"},
    "biotech_genomics": {"category": "sector", "risk": "high", "volatility": "very_high"},
    "cloud_saas": {"category": "sector", "risk": "medium-high", "volatility": "high"},
    "cybersecurity": {"category": "sector", "risk": "high", "volatility": "high"},
    # THEMATIC universes - growth-focused, trend-driven
    "ai": {"category": "thematic", "risk": "very_high", "volatility": "very_high"},
    "ai_datacenters": {"category": "thematic", "risk": "very_high", "volatility": "very_high"},
    "emerging_tech": {"category": "thematic", "risk": "high", "volatility": "very_high"},
    "ev": {"category": "thematic", "risk": "high", "volatility": "very_high"},
    "energy_storage": {"category": "thematic", "risk": "high", "volatility": "high"},
    "robotics": {"category": "thematic", "risk": "high", "volatility": "high"},
    "quantum": {"category": "thematic", "risk": "very_high", "volatility": "very_high"},
    "drones": {"category": "thematic", "risk": "high", "volatility": "high"},
    "nuclear_energy": {"category": "thematic", "risk": "high", "volatility": "medium-high"},
    "renewable_energy": {"category": "thematic", "risk": "medium", "volatility": "medium"},
    # EXPERIMENTAL universes - unreliable, sparse
    "korea": {"category": "experimental", "risk": "very_high", "volatility": "very_high"},
    "china": {"category": "experimental", "risk": "very_high", "volatility": "very_high"},
    "japan": {"category": "experimental", "risk": "high", "volatility": "high"},
    "global_asia": {"category": "experimental", "risk": "high", "volatility": "high"},
}


def get_universe(market: str, symbols: Sequence[str] | None = None) -> list[str]:
    """Return a symbol universe for the given market or explicit symbol list."""

    if symbols:
        return [symbol.strip().upper() for symbol in symbols if symbol.strip()]

    normalized = market.strip().lower()
    if normalized in UNIVERSES:
        return UNIVERSES[normalized][:]
    raise ValueError(f"Unknown market universe '{market}'.")


def list_universes() -> dict[str, dict[str, object]]:
    """Return metadata about available universes."""

    categories = {
        "core": ["sp500", "nasdaq", "europe"],
        "sector": ["semiconductor", "energy", "biotech_genomics", "cloud_saas", "cybersecurity"],
        "thematic": ["ai", "ai_datacenters", "emerging_tech", "ev", "energy_storage", "robotics", "quantum", "drones", "nuclear_energy", "renewable_energy"],
        "experimental": ["korea", "china", "japan", "global_asia"],
    }
    return categories


def get_meta(market: str) -> dict[str, str | float]:
    """Return metadata for a given universe."""
    if market.lower() in UNIVERSES_META:
        return UNIVERSES_META[market.lower()].copy()
    raise ValueError(f"No metadata for universe '{market}'.")


def get_universes_by_category(category: str) -> list[str]:
    """Get all universes for a given category."""
    categories = list_universes()
    if category.lower() in categories:
        return categories[category.lower()]
    raise ValueError(f"Unknown category '{category}'. Options: {list(categories.keys())}")


def get_universes_by_risk(risk_level: str) -> list[str]:
    """Get all universes matching a risk level."""
    matching = [u for u, meta in UNIVERSES_META.items() if meta.get("risk") == risk_level]
    if not matching:
        raise ValueError(f"No universes with risk level '{risk_level}'.")
    return matching

