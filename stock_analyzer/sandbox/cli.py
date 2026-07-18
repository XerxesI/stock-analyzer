"""SWING_20 Recommendation Sandbox CLI.

    python -m stock_analyzer.sandbox generate-candidates --as-of YYYY-MM-DD
    python -m stock_analyzer.sandbox process-entries     --as-of YYYY-MM-DD
    python -m stock_analyzer.sandbox monitor             --as-of YYYY-MM-DD
    python -m stock_analyzer.sandbox daily-run           --as-of YYYY-MM-DD

No `python -m stock_analyzer.*` CLI pattern existed in this repo before MVP 2; every
prior entry point was a standalone scripts/*.py file. This introduces the pattern
scoped to the sandbox package only.

There is intentionally no `execute-recommendations` command: BUY execution happens
inside `process-entries` (the fill price IS the recommendation) and SELL execution
happens inside `monitor` (the exit decision and its execution are the same
computation). An earlier version of this CLI had an `execute-recommendations` command
that did nothing -- removed per review, since a command that appears to execute
recommendations but performs no work is misleading. If a future revision separates
recommendation persistence from execution (recommendation event -> a distinct
execution service -> transaction event), that would be a new, explicit, tested
command, not a no-op.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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

DEFAULT_DB_PATH = "artifacts/sandbox/sandbox.db"


def _build_services(args: argparse.Namespace):
    conn = connect(args.db_path)
    repo = SandboxRepository(conn)
    config = SandboxConfig()

    adapter = Model2PredictionAdapter(args.train_features_path)
    universe = HistoricalFeatureUniverseProvider(args.features_path)
    candidate_service = CandidateService(repo, adapter, universe, config)
    entry_service = EntryService(repo, config)
    monitoring_service = MonitoringService(repo, config)
    daily_run_service = DailyRunService(repo, candidate_service, entry_service, monitoring_service, config)
    return repo, candidate_service, entry_service, monitoring_service, daily_run_service


def _write_reports(repo: SandboxRepository, as_of_date: date, output_root: str) -> None:
    data = build_daily_report_data(repo, as_of_date)
    json_path = write_json_report(data, output_root)
    md_path = write_markdown_report(data, output_root)
    print(f"[sandbox] wrote {json_path}")
    print(f"[sandbox] wrote {md_path}")


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as-of", required=True, help="Completed trading day, YYYY-MM-DD.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--train-features-path",
        required=True,
        help="Frozen train+validation feature dataset (features.parquet) Model 2 was fit on.",
    )
    parser.add_argument(
        "--features-path",
        required=True,
        help="Frozen feature dataset to read the day's symbol universe/features from "
        "(historical replay only -- see HistoricalFeatureUniverseProvider).",
    )
    parser.add_argument("--reports-root", default="artifacts/sandbox/daily")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m stock_analyzer.sandbox")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("generate-candidates", "process-entries", "monitor", "daily-run"):
        sub = subparsers.add_parser(name)
        _add_common_args(sub)

    args = parser.parse_args(argv)
    as_of_date = date.fromisoformat(args.as_of)
    repo, candidate_service, entry_service, monitoring_service, daily_run_service = _build_services(args)

    if args.command == "generate-candidates":
        result = candidate_service.generate_candidates(as_of_date)
        print(
            f"[sandbox] {as_of_date}: shadow_top10={len(result.shadow_top10)} "
            f"actionable={len(result.actionable)} orders_created={len(result.entry_orders)}"
        )
    elif args.command == "process-entries":
        outcomes = entry_service.process_entries(as_of_date)
        print(f"[sandbox] {as_of_date}: processed {len(outcomes)} pending order(s)")
        for o in outcomes:
            print(f"  {o.symbol}: {o.outcome}")
    elif args.command == "monitor":
        outcomes = monitoring_service.monitor(as_of_date)
        print(f"[sandbox] {as_of_date}: monitored {len(outcomes)} open position(s)")
        for o in outcomes:
            print(f"  {o.symbol}: {o.recommendation}")
    elif args.command == "daily-run":
        result = daily_run_service.run(as_of_date)
        if result.already_completed:
            print(f"[sandbox] {as_of_date}: daily-run already completed (run_id={result.run_id}); not re-executed.")
        else:
            print(
                f"[sandbox] {as_of_date}: entries={len(result.entry_outcomes)} "
                f"monitored={len(result.monitoring_outcomes)} "
                f"shadow_top10={len(result.candidate_result.shadow_top10) if result.candidate_result else 0}"
            )

    _write_reports(repo, as_of_date, args.reports_root)


if __name__ == "__main__":
    main()
