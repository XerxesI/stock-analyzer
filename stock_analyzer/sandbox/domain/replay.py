"""Historical Sandbox Replay metadata -- one row per replay run, in that replay's own
isolated database. See application/replay_service.py and
docs/09_experiments/EXP-004_Sandbox_Historical_Replay.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

DEVELOPMENT_HISTORICAL_REPLAY = "DEVELOPMENT_HISTORICAL_REPLAY"

RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"


@dataclass
class ReplayMetadata:
    replay_id: str
    classification: str
    signal_start_date: date
    signal_end_date: date
    outcome_data_end_date: date
    configuration_json: str
    configuration_hash: str
    started_at: datetime
    status: str = RUNNING
    code_commit_sha: str | None = None
    model_version: str | None = None
    feature_snapshot_id: str | None = None
    market_data_snapshot_id: str | None = None
    completed_at: datetime | None = None
    # The last trading date whose FULL day processing (entries + monitoring +
    # candidate generation, if a signal day) committed successfully. None means no
    # date has completed yet. A resume uses this watermark to skip every
    # already-completed date and reprocess only the one date that may have been
    # partially done when the process died -- see application/replay_service.py.
    last_completed_date: date | None = None
