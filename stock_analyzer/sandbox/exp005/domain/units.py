"""Exact, lossless integer fixed-point representation for every EXP-005 persisted
financial fact (Revision 5, Stage 5 corrective cycle).

The prior design computed in `Decimal` but persisted the result as SQLite `REAL`
(float) and compared for idempotency/reconciliation with a `1e-9` tolerance. That is
not exact: a float column can drift by fractions of a cent across a round-trip, and a
tolerance-based comparison can silently treat two different values -- or, at
`$10,000` scale, a genuinely wrong retry -- as identical. **No float ever represents
an accounting identity in this codebase from this module onward.** Every persisted
money/price/quantity/rate field is a plain Python `int`, counted in the fixed-point
scale below, compared with plain integer equality (`==`), never a tolerance.

Floats are permitted only as: (a) an input at construction time, converted
immediately and exactly via `Decimal(str(value))` (recovers the exact intended
decimal literal, not an approximation -- the same lesson Stage 1 already applied to
canonical config hashing); or (b) a presentation-layer view, derived from the
persisted integer at report-generation time and never round-tripped back into a
comparison or a write.

Scales (all fixed, all documented here once):
    money    -- minor units (cents), 2 decimal places      -> MONEY_SCALE = 100
    price    -- ten-thousandths of a dollar, 4 decimals    -> PRICE_SCALE = 10_000
    quantity -- ten-thousandths of a share, 4 decimals     -> QUANTITY_SCALE = 10_000
    rate     -- basis points, 4 decimals (1 bp = 0.0001)   -> RATE_SCALE = 10_000

Rounding mode: ROUND_HALF_UP for every conversion in this module, always explicit,
never Python's `round()` default (round-half-to-even). This supersedes the
round-half-to-even choice the pre-corrective-cycle design inherited from
`stock_analyzer.sandbox.config.round_price`/`round_money` -- those helpers are no
longer used for anything EXP-005 persists or compares.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

MONEY_SCALE = 100
PRICE_SCALE = 10_000
QUANTITY_SCALE = 10_000
RATE_SCALE = 10_000

DEFAULT_ROUNDING = ROUND_HALF_UP


def _to_units(value: float | str | Decimal, scale: int, rounding=DEFAULT_ROUNDING) -> int:
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return int((d * scale).quantize(Decimal(1), rounding=rounding))


def to_money_units(value: float | str | Decimal) -> int:
    return _to_units(value, MONEY_SCALE)


def to_price_units(value: float | str | Decimal) -> int:
    return _to_units(value, PRICE_SCALE)


def to_quantity_units(value: float | str | Decimal, rounding=DEFAULT_ROUNDING) -> int:
    """`rounding=ROUND_DOWN` is used specifically when deriving a BUY's quantity
    from a fixed budget (domain/accounting.py) -- rounding a quantity UP could make
    `security cost + commission` exceed the slot budget after rounding; rounding
    down guarantees it never does, with the small remainder explicitly retained as
    cash rather than silently absorbed."""

    return _to_units(value, QUANTITY_SCALE, rounding=rounding)


def to_rate_units(value: float | str | Decimal) -> int:
    return _to_units(value, RATE_SCALE)


def money_units_to_decimal(units: int) -> Decimal:
    return Decimal(units) / MONEY_SCALE


def price_units_to_decimal(units: int) -> Decimal:
    return Decimal(units) / PRICE_SCALE


def quantity_units_to_decimal(units: int) -> Decimal:
    return Decimal(units) / QUANTITY_SCALE


def rate_units_to_decimal(units: int) -> Decimal:
    return Decimal(units) / RATE_SCALE


def money_units_to_float(units: int) -> float:
    """Presentation-boundary conversion ONLY (e.g. a human-readable report) -- the
    result must never be persisted, compared for identity, or fed back into a
    to_*_units() call."""

    return float(money_units_to_decimal(units))


def price_units_to_float(units: int) -> float:
    return float(price_units_to_decimal(units))


def quantity_units_to_float(units: int) -> float:
    return float(quantity_units_to_decimal(units))


def rate_units_to_float(units: int) -> float:
    return float(rate_units_to_decimal(units))
