"""EXP-005 -- the first REAL Variant B replay.

Runs the sole sanctioned real-run entry point,
`stock_analyzer.sandbox.exp005.application.real_run.run_real_experiment`, over the
period Section 5 of `docs/09_experiments/EXP-005_Portfolio_Policy_Feasibility_Pilot.md`
now fixes explicitly (restored from EXP-004's own already-frozen period, since
Revision 2's original Section 5 text is not recoverable from this repo's history):
signal dates 2024-11-18..2025-09-03 (Model 2's SWING_20 validation split), outcome
data through 2025-10-20 -- 230 real trading sessions.

Stages 0-15 of EXP-005's implementation are locked (fifth independent review passed,
see docs/09_experiments/EXP-005_Stage15_Completion_Report.md). Per the standing
authorization, this script may generate the final manifest and execute ONE real
Variant B replay -- Variant D's 50 control seeds are explicitly NOT run here; that
only happens in a separate step, and only if Variant B passes every pre-registered
absolute feasibility criterion (a confirmed failure on any of them already determines
the overall verdict, per the three-tier logic in
`exp005/diagnostics/financial_performance.py`).

This script performs the freeze-validation gate as an explicit, visible step BEFORE
`run_real_experiment` (which re-runs the identical checks internally regardless) --
if the manifest fails to build with the expected 230-session calendar, or the gate
raises for any reason, this script stops before creating any replay database or
producing any P&L result.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_analyzer.sandbox.domain.replay import DEVELOPMENT_HISTORICAL_REPLAY, ReplayMetadata
from stock_analyzer.sandbox.exp005.application.real_run import (
    RealRunGateError,
    run_real_experiment,
    verify_database_schema_matches_manifest,
    verify_real_run_preconditions,
)
from stock_analyzer.sandbox.exp005.config import VARIANT_B, Exp005Config
from stock_analyzer.sandbox.exp005.freeze_validation import FreezeValidationError, validate_freeze
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import sha256_of_file, verify_frozen_lineage
from stock_analyzer.sandbox.exp005.manifest import (
    build_experiment_manifest,
    compute_frozen_calendar,
    write_manifest_artifact,
)
from stock_analyzer.sandbox.infrastructure.schema import connect

FEATURE_SNAPSHOT_DIR = "artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z"
SIGNAL_START = date(2024, 11, 18)
SIGNAL_END = date(2025, 9, 3)
OUTCOME_END = date(2025, 10, 20)
EXPECTED_SESSION_COUNT = 230

REPLAY_ID = "exp005_real_variant_b_2024_11_2025_10"
RUN_ROOT = PROJECT_ROOT / "artifacts" / "sandbox" / "exp005" / "real_runs" / REPLAY_ID
MANIFEST_PATH = RUN_ROOT / "experiment_manifest.json"
DB_PATH = RUN_ROOT / "replay.db"
RUN_LOG_PATH = RUN_ROOT / "run_log.json"


def _git_commit_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _working_tree_is_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() == ""


def _sha256_of_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    print("=" * 78)
    print("EXP-005 -- REAL Variant B replay (Stages 0-15 locked)")
    print("=" * 78)

    if not _working_tree_is_clean():
        print("[ABORT] git working tree is not clean -- a real EXP-005 run requires a "
              "committed, clean state. Commit or stash first.")
        raise SystemExit(1)
    commit_sha = _git_commit_sha()
    print(f"[gate] code_commit_sha = {commit_sha}")

    RUN_ROOT.mkdir(parents=True, exist_ok=True)

    config = Exp005Config(variant_id=VARIANT_B)
    print(f"[manifest] building manifest for variant={config.variant_id}, "
          f"period=[{SIGNAL_START} .. {SIGNAL_END}] (outcome through {OUTCOME_END})...")
    manifest = build_experiment_manifest(
        config, FEATURE_SNAPSHOT_DIR, SIGNAL_START, SIGNAL_END, OUTCOME_END, code_commit_sha=commit_sha,
    )

    print(f"[manifest] calendar_session_count = {manifest.calendar_session_count}")
    if manifest.calendar_session_count != EXPECTED_SESSION_COUNT:
        print(
            f"[ABORT] manifest's calendar_session_count ({manifest.calendar_session_count}) does not "
            f"match the expected {EXPECTED_SESSION_COUNT} sessions (EXP-004's own already-computed "
            "total for this identical period) -- the frozen prices artifact's data coverage may have "
            "changed. Stopping before writing the manifest artifact or running anything."
        )
        raise SystemExit(1)

    write_manifest_artifact(manifest, MANIFEST_PATH)
    manifest_artifact_hash = sha256_of_file(MANIFEST_PATH)
    print(f"[manifest] wrote {MANIFEST_PATH}")
    print(f"[manifest] manifest_artifact_hash = {manifest_artifact_hash}")

    print("\n[gate] running the full freeze-validation gate BEFORE any replay database "
          "is created...")
    try:
        validate_freeze(manifest)
    except FreezeValidationError as e:
        print(f"[ABORT] freeze validation failed: {e}")
        raise SystemExit(1)
    print("[gate] validate_freeze: PASSED")

    lineage = verify_frozen_lineage(FEATURE_SNAPSHOT_DIR)
    trading_dates_tuple, calendar_version = compute_frozen_calendar(lineage.prices_df, SIGNAL_START, OUTCOME_END)
    # ReplayService._validate_trading_dates does `trading_dates != sorted(trading_dates)`
    # with no type coercion -- a tuple never equals sorted()'s list even with
    # identical elements, so this MUST be a real list, not the tuple
    # compute_frozen_calendar returns.
    trading_dates = list(trading_dates_tuple)
    print(f"[gate] re-derived trading_dates: {len(trading_dates)} sessions "
          f"({trading_dates[0]} .. {trading_dates[-1]})")
    if len(trading_dates) != EXPECTED_SESSION_COUNT:
        print(
            f"[ABORT] re-derived session count ({len(trading_dates)}) does not match the expected "
            f"{EXPECTED_SESSION_COUNT}. Stopping before running anything."
        )
        raise SystemExit(1)
    if calendar_version != manifest.calendar_version:
        print("[ABORT] re-derived calendar_version does not match the manifest's own recorded "
              "calendar_version. Stopping before running anything.")
        raise SystemExit(1)

    replay_metadata_template = ReplayMetadata(
        replay_id=REPLAY_ID,
        classification=DEVELOPMENT_HISTORICAL_REPLAY,
        signal_start_date=SIGNAL_START,
        signal_end_date=SIGNAL_END,
        outcome_data_end_date=OUTCOME_END,
        configuration_json="{}",
        configuration_hash="placeholder-overwritten-by-run_real_experiment",
        started_at=datetime.now(timezone.utc),
    )

    conn = connect(str(DB_PATH))
    try:
        verify_database_schema_matches_manifest(conn, manifest)
        print("[gate] verify_database_schema_matches_manifest: PASSED")

        verify_real_run_preconditions(
            manifest, config, FEATURE_SNAPSHOT_DIR, trading_dates, replay_metadata_template,
        )
        print("[gate] verify_real_run_preconditions: PASSED -- all provenance checks satisfied.")
    except RealRunGateError as e:
        conn.close()
        DB_PATH.unlink(missing_ok=True)
        print(f"[ABORT] freeze-validation gate failed: {e}")
        print("[ABORT] no replay database or P&L result was created.")
        raise SystemExit(1)

    print("\n[replay] gate fully passed. Starting the REAL Variant B replay...")
    print(f"[replay] replay_id = {REPLAY_ID}")
    print(f"[replay] db_path = {DB_PATH}")
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()
    result = run_real_experiment(
        conn, MANIFEST_PATH, FEATURE_SNAPSHOT_DIR, config, replay_metadata_template, trading_dates,
    )
    duration_seconds = time.monotonic() - t0
    completed_at = datetime.now(timezone.utc)
    conn.close()

    db_sha256 = _sha256_of_file(DB_PATH)

    print(f"\n[replay] COMPLETED in {duration_seconds:.1f}s")
    print(f"[replay] dates processed: {len(result.dates_processed)}")
    print(f"[replay] unresolved open positions at outcome end: {len(result.unresolved_position_ids)}")
    print(f"[replay] replay.db sha256 = {db_sha256}")

    run_log = {
        "replay_id": REPLAY_ID,
        "variant_id": config.variant_id,
        "control_seed": config.control_seed,
        "code_commit_sha": commit_sha,
        "manifest_path": str(MANIFEST_PATH),
        "manifest_artifact_hash": manifest_artifact_hash,
        "feature_snapshot_dir": FEATURE_SNAPSHOT_DIR,
        "signal_start_date": SIGNAL_START.isoformat(),
        "signal_end_date": SIGNAL_END.isoformat(),
        "outcome_data_end_date": OUTCOME_END.isoformat(),
        "dates_processed": len(result.dates_processed),
        "unresolved_position_ids": result.unresolved_position_ids,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": duration_seconds,
        "db_path": str(DB_PATH),
        "db_sha256": db_sha256,
    }
    RUN_LOG_PATH.write_text(json.dumps(run_log, indent=2), encoding="utf-8")
    print(f"[replay] wrote {RUN_LOG_PATH}")


if __name__ == "__main__":
    main()
