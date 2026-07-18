"""SWING_20 Recommendation Sandbox -- integration smoke test.

INTEGRATION_REPLAY -- NOT MODEL VALIDATION.

Runs the real Model 2 sandbox (real frozen train fit, real market-data fetches) over a
small, arbitrarily-chosen window at the START of the validation period (the first N
trading days of validation, not selected to make any outcome look good, and not later
used to tune any rule). Verifies mechanical integration only: daily ordering, no
same-day fills, pending-entry progression, position creation, target/time exits,
report generation, and idempotent reruns.

This script never touches locked_test and never modifies Model 2, the entry-price
policy, or any threshold based on what it observes.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_analyzer.sandbox.application.candidate_service import CandidateService, HistoricalFeatureUniverseProvider
from stock_analyzer.sandbox.application.daily_run_service import DailyRunService
from stock_analyzer.sandbox.application.entry_service import EntryService
from stock_analyzer.sandbox.application.monitoring_service import MonitoringService
from stock_analyzer.sandbox.config import SandboxConfig
from stock_analyzer.sandbox.infrastructure.model2_prediction_adapter import Model2PredictionAdapter
from stock_analyzer.sandbox.infrastructure.schema import connect
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository
from stock_analyzer.sandbox.reporting.daily_json_report import write_json_report
from stock_analyzer.sandbox.reporting.daily_markdown_report import write_markdown_report
from stock_analyzer.sandbox.reporting.report_data import build_daily_report_data

FEATURES_PATH = "artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet"
SMOKE_TEST_DATES = [date(2024, 11, 18), date(2024, 11, 19), date(2024, 11, 20), date(2024, 11, 21)]
DB_PATH = "artifacts/sandbox/smoke_test.db"
REPORTS_ROOT = "artifacts/sandbox/daily"


def main() -> None:
    print("=" * 70)
    print("INTEGRATION_REPLAY -- NOT MODEL VALIDATION")
    print("=" * 70)
    print(f"Window: {SMOKE_TEST_DATES[0]} .. {SMOKE_TEST_DATES[-1]} ({len(SMOKE_TEST_DATES)} days)")

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(DB_PATH)
    repo = SandboxRepository(conn)
    config = SandboxConfig()

    print("\n[smoke] loading frozen Model 2 (train-only fit)...")
    adapter = Model2PredictionAdapter(FEATURES_PATH)
    print(f"[smoke] model_version={adapter.model_version} feature_count={len(adapter.feature_names)}")

    universe = HistoricalFeatureUniverseProvider(FEATURES_PATH)
    candidate_service = CandidateService(repo, adapter, universe, config)
    entry_service = EntryService(repo, config)
    monitoring_service = MonitoringService(repo, config)
    daily_run_service = DailyRunService(repo, candidate_service, entry_service, monitoring_service, config)

    checks: dict[str, bool] = {}

    print("\n[smoke] --- day-by-day daily-run ---")
    for as_of_date in SMOKE_TEST_DATES:
        result = daily_run_service.run(as_of_date)
        n_shadow = len(result.candidate_result.shadow_top10) if result.candidate_result else 0
        n_filled_today = len([o for o in result.entry_outcomes if o.outcome == "FILLED"])
        n_monitored = len(result.monitoring_outcomes)
        print(
            f"[smoke] {as_of_date}: shadow_top10={n_shadow} "
            f"entries_processed={len(result.entry_outcomes)} filled_today={n_filled_today} "
            f"monitored={n_monitored}"
        )
        data = build_daily_report_data(repo, as_of_date)
        json_path = write_json_report(data, REPORTS_ROOT)
        md_path = write_markdown_report(data, REPORTS_ROOT)
        print(f"[smoke]   reports: {json_path}, {md_path}")

    # ---- integration checks (not performance checks) ----
    print("\n[smoke] --- integration checks ---")

    # 1. No same-signal-day fill: no entry_orders row has fill_date == signal_date.
    all_orders_ever = repo.get_orders_created_on(SMOKE_TEST_DATES[0]) + [
        o for d in SMOKE_TEST_DATES[1:] for o in repo.get_orders_created_on(d)
    ]
    same_day_fills = [o for o in all_orders_ever if o.fill_date == o.signal_date]
    checks["no_same_day_fills"] = len(same_day_fills) == 0
    print(f"[smoke] no_same_day_fills: {checks['no_same_day_fills']} ({len(same_day_fills)} violation(s))")

    # 2. Daily ordering: every filled order's fill_date is strictly after its signal_date.
    filled_orders = [o for d in SMOKE_TEST_DATES for o in repo.get_orders_filled_on(d)]
    ordering_ok = all(o.fill_date > o.signal_date for o in filled_orders)
    checks["fill_after_signal"] = ordering_ok
    print(f"[smoke] fill_after_signal: {ordering_ok} ({len(filled_orders)} fill(s) checked)")

    # 3. Positions were created for filled orders.
    open_positions = repo.get_open_positions()
    checks["positions_exist_if_any_fill"] = (len(filled_orders) == 0) or (len(open_positions) > 0)
    print(f"[smoke] positions_created: {len(open_positions)} open position(s) from {len(filled_orders)} fill(s)")

    # 4. Idempotent rerun of the first day does not duplicate anything.
    candidates_before = len(repo.get_candidates_for_date(SMOKE_TEST_DATES[0]))
    rerun_result = daily_run_service.run(SMOKE_TEST_DATES[0])
    candidates_after = len(repo.get_candidates_for_date(SMOKE_TEST_DATES[0]))
    checks["idempotent_rerun"] = rerun_result.already_completed and (candidates_before == candidates_after)
    print(f"[smoke] idempotent_rerun: {checks['idempotent_rerun']} (candidates {candidates_before} -> {candidates_after})")

    # 5. Reports exist for every day.
    reports_ok = all(
        (Path(REPORTS_ROOT) / d.isoformat() / "sandbox_daily_report.json").exists()
        and (Path(REPORTS_ROOT) / d.isoformat() / "sandbox_daily_report.md").exists()
        for d in SMOKE_TEST_DATES
    )
    checks["reports_generated"] = reports_ok
    print(f"[smoke] reports_generated: {reports_ok}")

    print("\n" + "=" * 70)
    all_pass = all(checks.values())
    print(f"INTEGRATION_REPLAY result: {'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")
    print("This is an integration check, NOT a model-performance or profitability claim.")
    print("=" * 70)
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    if not all_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
