"""SWING_20 Recommendation Sandbox -- Development Historical Replay.

DEVELOPMENT_HISTORICAL_REPLAY -- NOT INDEPENDENT MODEL VALIDATION -- NOT FOR POLICY
OPTIMIZATION.

Runs the real Model 2 sandbox sequentially, day by day, over the pre-registered period
in docs/09_experiments/EXP-004_Sandbox_Historical_Replay.md Part 1 (signal dates
2024-11-18..2025-09-03, matching the SWING_20 validation split; outcome data through
2025-10-20). Uses an isolated SQLite database, never the smoke-test or any future
forward/live sandbox database. Do not tune any sandbox policy constant based on this
replay's results.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_analyzer.data.data_fetcher import get_stock_data
from stock_analyzer.sandbox.application.candidate_service import CandidateService, HistoricalFeatureUniverseProvider
from stock_analyzer.sandbox.application.entry_service import EntryService
from stock_analyzer.sandbox.application.monitoring_service import MonitoringService
from stock_analyzer.sandbox.application.replay_service import ReplayAlreadyCompletedError, ReplayService
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.domain.replay import DEVELOPMENT_HISTORICAL_REPLAY, ReplayMetadata
from stock_analyzer.sandbox.infrastructure.model2_prediction_adapter import Model2PredictionAdapter
from stock_analyzer.sandbox.infrastructure.schema import connect
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository
from stock_analyzer.sandbox.reporting.replay_metrics import build_replay_metrics, load_target_label_lookup

FEATURES_PATH = "artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet"
REPLAY_ID = "development_replay_2024_11_2025_10"
SIGNAL_START = date(2024, 11, 18)
SIGNAL_END = date(2025, 9, 3)
OUTCOME_END = date(2025, 10, 20)
REPLAY_ROOT = Path("artifacts/sandbox/replays") / REPLAY_ID
DB_PATH = REPLAY_ROOT / "replay.db"
METRICS_PATH = REPLAY_ROOT / "replay_metrics.json"


def _git_commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5, check=False
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def _signal_dates() -> list[date]:
    df = pd.read_parquet(FEATURES_PATH, columns=["date", "split"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    validation_dates = sorted(df.loc[df["split"] == "validation", "date"].unique())
    return [d for d in validation_dates if SIGNAL_START <= d <= SIGNAL_END]


def _outcome_only_dates() -> list[date]:
    spy = get_stock_data("SPY", "5y")
    spy_dates = pd.DatetimeIndex(spy.index).date
    return sorted({d for d in spy_dates if SIGNAL_END < d <= OUTCOME_END})


def main() -> None:
    REPLAY_ROOT.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("DEVELOPMENT_HISTORICAL_REPLAY -- NOT INDEPENDENT MODEL VALIDATION -- NOT FOR POLICY OPTIMIZATION")
    print("=" * 70)

    signal_dates = _signal_dates()
    outcome_dates = _outcome_only_dates()
    all_dates = sorted(signal_dates + outcome_dates)
    print(f"[replay] signal dates: {len(signal_dates)} ({signal_dates[0]} .. {signal_dates[-1]})")
    print(f"[replay] outcome-only dates: {len(outcome_dates)} ({outcome_dates[0]} .. {outcome_dates[-1]})")
    print(f"[replay] total dates to process: {len(all_dates)}")

    conn = connect(str(DB_PATH))
    repo = SandboxRepository(conn)
    config = SandboxConfig()

    print("\n[replay] loading frozen Model 2 (train-only fit)...")
    adapter = Model2PredictionAdapter(FEATURES_PATH)
    universe = HistoricalFeatureUniverseProvider(FEATURES_PATH)
    candidate_service = CandidateService(repo, adapter, universe, config)
    entry_service = EntryService(repo, config)
    monitoring_service = MonitoringService(repo, config)
    replay_service = ReplayService(repo, candidate_service, entry_service, monitoring_service, config)

    replay_metadata = ReplayMetadata(
        replay_id=REPLAY_ID,
        classification=DEVELOPMENT_HISTORICAL_REPLAY,
        signal_start_date=SIGNAL_START,
        signal_end_date=SIGNAL_END,
        outcome_data_end_date=OUTCOME_END,
        configuration_json=json.dumps(asdict(config), sort_keys=True),
        configuration_hash=config.config_hash(),
        started_at=datetime.now(timezone.utc),
        code_commit_sha=_git_commit_sha(),
        model_version=adapter.model_version,
        feature_snapshot_id=Path(FEATURES_PATH).parent.name,
        market_data_snapshot_id="live_yfinance_2y_window",
    )

    print(f"\n[replay] starting replay_id={REPLAY_ID}...")
    try:
        result = replay_service.run(replay_metadata, all_dates, progress_every=20)
    except ReplayAlreadyCompletedError as exc:
        print(f"[replay] {exc}")
        print("[replay] delete the isolated database to force a genuine rerun, or use a new replay_id.")
        raise SystemExit(1)

    print(f"\n[replay] completed. {len(result.unresolved_position_ids)} unresolved position(s) at outcome end.")

    print("\n[replay] building attribution funnel + counterfactual metrics...")
    label_lookup = load_target_label_lookup(FEATURES_PATH)
    metrics = build_replay_metrics(repo, SIGNAL_START, SIGNAL_END, OUTCOME_END, label_lookup=label_lookup)

    METRICS_PATH.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    print(f"[replay] wrote {METRICS_PATH}")

    print("\n" + "=" * 70)
    print("SUMMARY (observational only -- not used to tune any policy)")
    print("=" * 70)
    print(json.dumps(metrics["funnel"], indent=2))
    print(json.dumps(metrics["position_lifecycle"], indent=2, default=str))


if __name__ == "__main__":
    main()
