"""Build a broad, non-curated common-stock universe from NASDAQ Trader's
official symbol directory, per the swing-trade spec's step 1 (Universe
Filter): NASDAQ + NYSE + NYSE American, minus ETFs/funds/warrants/rights/
units/preferred stock.

This exists mainly to get a LESS survivorship-biased validation sample
than hand-picked lists like ``ai``/``nuclear_energy`` or
``LIQUID_LARGECAP`` (which are, by construction, today's well-known
large/successful names). A random sample of ~thousands of ordinary
listed common stocks will include plenty of small, mediocre, or beaten-
down names that a curated "quality" list would never include.

CAVEAT (read before trusting a "clean" result): this is still a
CURRENT listing snapshot. Tickers that were delisted, acquired, or
went bankrupt before today are NOT in this file at all, so true
point-in-time survivorship bias is only reduced here, not eliminated.
A stock that fell 90% and is still limping along today WILL show up;
one that fell 100% and got delisted five years ago will not.

Source files (NASDAQ Trader symbol directory, pipe-delimited, updated
daily):
    http://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
    http://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt   (NYSE, NYSE American, etc.)
"""

from __future__ import annotations

import io
import random
import urllib.request
from typing import cast

import pandas as pd

NASDAQ_LISTED_URL = "http://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "http://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

REQUEST_TIMEOUT_SECONDS = 15

# Exchange codes used in otherlisted.txt's "Exchange" column.
# A = NYSE American, N = NYSE, P = NYSE Arca, Z = BATS/Cboe BZX, V = IEXG
OTHER_LISTED_KEEP_EXCHANGES = {"A", "N"}  # NYSE American + NYSE only, per the spec

# Security Name substrings (case-insensitive) that indicate the listing is
# NOT a plain common stock. Deliberately broad; false-negatives (something
# slipping through) are more acceptable here than false-positives that
# would wrongly reject legitimate common stock.
EXCLUDE_NAME_PATTERNS = [
    "preferred",
    "depositary",
    "warrant",
    " right",
    "rights",
    " unit",
    "units",
    "trust preferred",
    "convertible",
    " notes",
    "debenture",
    "acquisition corp",  # SPAC shells are common stock in name only; too speculative for this test
    "acquisition company",
    "capital trust",
]


def _clean_symbol(symbol: object) -> str:
    if symbol is None:
        return ""
    if isinstance(symbol, float) and pd.isna(symbol):
        return ""
    return str(symbol).strip().upper()


def _looks_like_common_stock(security_name: str, symbol: str) -> bool:
    name_lower = security_name.lower()
    if any(pattern in name_lower for pattern in EXCLUDE_NAME_PATTERNS):
        return False
    # NASDAQ 5th-letter suffix convention (not always reliable, used as a
    # secondary signal only): W=warrant, R=rights, U=units, P/Q=preferred-ish.
    if len(symbol) == 5 and symbol[-1] in {"W", "R", "U"}:
        return False
    # Test-directory placeholder symbols and anything with odd characters.
    if "$" in symbol or "." in symbol:
        return False
    return True


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "stock-analyzer/1.0"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_nasdaq_listed() -> pd.DataFrame:
    """Download and parse nasdaqlisted.txt (NASDAQ-listed securities)."""

    text = _fetch_text(NASDAQ_LISTED_URL)
    # Last line is a "File Creation Time" footer, not data.
    lines = text.strip().splitlines()
    data_lines = [line for line in lines if line.strip() and not line.startswith("File Creation Time")]
    df = pd.read_csv(io.StringIO("\n".join(data_lines)), sep="|")
    df = df.rename(columns={"Symbol": "symbol", "Security Name": "security_name"})
    df["exchange"] = "NASDAQ"
    df = df[(df["Test Issue"] == "N") & (df["ETF"] == "N")]
    if "NextShares" in df.columns:
        df = df[df["NextShares"] == "N"]
    return df[["symbol", "security_name", "exchange"]].copy()


def fetch_other_listed() -> pd.DataFrame:
    """Download and parse otherlisted.txt (NYSE, NYSE American, etc.)."""

    text = _fetch_text(OTHER_LISTED_URL)
    lines = text.strip().splitlines()
    data_lines = [line for line in lines if line.strip() and not line.startswith("File Creation Time")]
    df = pd.read_csv(io.StringIO("\n".join(data_lines)), sep="|")
    df = df.rename(columns={"ACT Symbol": "symbol", "Security Name": "security_name", "Exchange": "exchange"})
    df = df[(df["Test Issue"] == "N") & (df["ETF"] == "N")]
    df = df[df["exchange"].isin(OTHER_LISTED_KEEP_EXCHANGES)]
    df["exchange"] = df["exchange"].map({"A": "NYSE American", "N": "NYSE"})
    return df[["symbol", "security_name", "exchange"]].copy()


def build_full_universe() -> pd.DataFrame:
    """Combine NASDAQ + NYSE + NYSE American and filter to plain common stock.

    Returns:
        DataFrame with columns [symbol, security_name, exchange], one row
        per common stock, deduplicated by symbol.
    """

    nasdaq = fetch_nasdaq_listed()
    other = fetch_other_listed()
    combined = pd.concat([nasdaq, other], ignore_index=True)
    combined = combined.dropna(subset=["symbol", "security_name"])
    combined["symbol"] = combined["symbol"].astype(str).map(_clean_symbol)
    combined = combined[combined["symbol"] != ""]
    combined = combined.drop_duplicates(subset="symbol")

    mask = combined.apply(
        lambda row: _looks_like_common_stock(str(row["security_name"]), str(row["symbol"])),
        axis=1,
    )
    return combined[mask].reset_index(drop=True)


def sample_universe(n: int, seed: int = 42, universe_df: pd.DataFrame | None = None) -> list[str]:
    """Return a reproducible random sample of ``n`` common-stock symbols.

    Args:
        n: Sample size.
        seed: Random seed for reproducibility across runs.
        universe_df: Pre-fetched universe (from ``build_full_universe``);
            fetched fresh if not provided.

    Returns:
        List of ticker symbols.
    """

    df = universe_df if universe_df is not None else build_full_universe()
    symbols = cast(list[str], [str(symbol) for symbol in df["symbol"].tolist()])
    rng = random.Random(seed)
    if n >= len(symbols):
        return symbols
    return rng.sample(symbols, n)


if __name__ == "__main__":
    universe = build_full_universe()
    print(f"Full filtered common-stock universe: {len(universe)} symbols")
    print(universe["exchange"].value_counts())
    sample = sample_universe(20, seed=42, universe_df=universe)
    print(f"\nExample 20-symbol sample (seed=42): {sample}")