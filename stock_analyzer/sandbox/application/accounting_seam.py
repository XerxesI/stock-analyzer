"""The optional position-sizing/accounting seam shared by `EntryService` and
`MonitoringService` (EXP-005 Stage 6, per the ChatGPT-reviewed "aligned dual-
accounting" resolution to the Section 8.3/11.3 contradiction).

**What this seam is, precisely:** core sandbox's fill/exit *decision* logic (ADR-007
fillability against the raw market price, target-price/time-exit triggers) is never
touched by this seam and never will be -- that stays exactly as it is today,
regardless of whether a seam is injected. What this seam changes is narrower:
**position sizing**, i.e. how many shares a fill represents. Today that is
`SandboxConfig.virtual_notional / raw_fill_price` (`DefaultAccountingSeam`, below --
preserves this byte-for-byte when no seam is injected). EXP-005 injects a different
implementation that sizes from its own $10,000 slot budget net of commission, against
the slippage-adjusted effective price (Section 8.3) -- but critically, that different
quantity is still the ONE quantity used everywhere a quantity is recorded for that
fill: `VirtualPosition.quantity`, both BUY and SELL `VirtualTransaction.quantity`, and
both BUY and SELL `Execution.quantity_units` (EXP-005's own audit ledger). Nothing
about *whether* or *when* a fill/exit happens, or `VirtualPosition.entry_price`/
`target_price` (which stay on the raw price, so core's own MFE/MAE/realized-return
percentages remain a self-consistent raw-price policy/shadow metric, never a reported
EXP-005 financial figure) is affected by which seam is injected.

Also carries lifecycle notification hooks (`on_filled`/`on_expired`/`on_closed`), so
EXP-005 can convert/release its own `slot_reservations` row and append its own
`executions` row -- inside the SAME atomic transaction `EntryService`/
`MonitoringService` already open for their own core-table writes, so a fill or close
event is one all-or-nothing unit across both core and EXP-005 tables, never a state
where one side's tables reflect the event and the other's do not.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.position import VirtualPosition
from stock_analyzer.sandbox.domain.transaction import VirtualTransaction


class PortfolioAccountingSeam(Protocol):
    def size_buy(self, order: EntryOrder, raw_fill_price: float, fill_date: date) -> float:
        """Returns the quantity (shares) to use for this BUY fill. Called BEFORE
        the VirtualPosition/VirtualTransaction are constructed -- its return value
        becomes VirtualPosition.quantity and every transaction/execution quantity
        for this position, on both the BUY and the later SELL. An implementation
        that also needs the full accounting detail (effective price, commission,
        slippage, cash flow) for on_filled below must compute and cache it now,
        keyed by order.order_id, and reuse that SAME cached result there -- never
        recompute independently, which could silently diverge."""
        ...

    def on_filled(
        self, order: EntryOrder, position: VirtualPosition, transaction: VirtualTransaction, raw_fill_price: float
    ) -> None:
        """Called after the position and BUY transaction rows are built (but
        inside the same still-open atomic transaction, before commit) -- default
        no-op. Implementations must use non-committing writes only."""
        ...

    def on_expired(self, order: EntryOrder, as_of_date: date, reason: str) -> None:
        """Called after the order's status is set to EXPIRED (inside the same
        still-open atomic transaction, before commit) -- default no-op."""
        ...

    def on_closed(
        self, position: VirtualPosition, transaction: VirtualTransaction, exit_date: date, exit_price: float, exit_reason: str
    ) -> None:
        """Called after the position is closed and the SELL transaction row is
        built (inside the same still-open atomic transaction, before commit) --
        default no-op."""
        ...


class DefaultAccountingSeam:
    """Preserves today's sizing/behavior exactly: quantity = virtual_notional /
    raw_fill_price, no cost model, no lifecycle side effects. This is the default
    for every non-EXP-005 caller of EntryService/MonitoringService -- their control
    flow, database writes, and output are completely unaffected by this seam's
    existence."""

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config

    def size_buy(self, order: EntryOrder, raw_fill_price: float, fill_date: date) -> float:
        return self._config.virtual_notional / raw_fill_price

    def on_filled(
        self, order: EntryOrder, position: VirtualPosition, transaction: VirtualTransaction, raw_fill_price: float
    ) -> None:
        pass

    def on_expired(self, order: EntryOrder, as_of_date: date, reason: str) -> None:
        pass

    def on_closed(
        self, position: VirtualPosition, transaction: VirtualTransaction, exit_date: date, exit_price: float, exit_reason: str
    ) -> None:
        pass
