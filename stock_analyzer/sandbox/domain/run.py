"""One row per CLI invocation attempt -- the top-level audit/idempotency anchor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"


@dataclass
class SandboxRun:
    run_id: str
    as_of_date: date
    command: str
    started_at: datetime
    configuration_hash: str
    status: str = RUNNING
    completed_at: datetime | None = None
    model_version: str | None = None
    data_snapshot_id: str | None = None
    code_commit_sha: str | None = None
    error_message: str | None = None

    @staticmethod
    def make_id(as_of_date: date, command: str) -> str:
        return f"{as_of_date.isoformat()}:{command}"
