"""Plain domain dataclasses for the Recommendation Sandbox. No persistence, no I/O."""

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.data_quality import DataQualityEvent
from stock_analyzer.sandbox.domain.entry_order import EntryOrder, EntryOrderAttempt
from stock_analyzer.sandbox.domain.position import PositionSnapshot, VirtualPosition
from stock_analyzer.sandbox.domain.recommendation import Recommendation
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.domain.transaction import VirtualTransaction

__all__ = [
    "RankedCandidate",
    "DataQualityEvent",
    "EntryOrder",
    "EntryOrderAttempt",
    "PositionSnapshot",
    "VirtualPosition",
    "Recommendation",
    "SandboxRun",
    "VirtualTransaction",
]
