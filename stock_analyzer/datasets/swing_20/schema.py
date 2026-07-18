"""Typed audit result containers for SWING_20."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

TrainabilityStatus = Literal["TRAINABLE", "CONDITIONALLY_TRAINABLE", "NOT_TRAINABLE_AS_DEFINED"]


@dataclass(frozen=True)
class AuditDecision:
    """Final trainability decision."""

    status: TrainabilityStatus
    hard_blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    recommended_next_step: str = ""


@dataclass(frozen=True)
class AuditResult:
    """JSON-compatible SWING_20 audit result."""

    strategy: str
    spec_version: str
    generated_at: str
    date_range: dict[str, str | None]
    universe: dict[str, Any]
    labels: dict[str, Any]
    splits: dict[str, Any]
    baseline: dict[str, Any]
    quality: dict[str, Any]
    data_quality_quarantine: dict[str, Any]
    target_already_reached_at_entry: dict[str, Any]
    decision: AuditDecision

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["decision"] = asdict(self.decision)
        return payload

