"""Stage 15: synthetic end-to-end fixture covering the FULL EXP-005 pipeline --
Revision 5, Section 12 item 15.

Drives `run_real_experiment` for real (only the model adapter and feature-universe
provider are faked, exactly as `test_exp005_real_run.py`'s own integration test
does, since a real Model2 fit needs real training data this fixture has no reason
to reproduce) against a small, fully synthetic two-symbol frozen price history, so
every OTHER real component genuinely runs: CandidateService's real
scoring/ranking/data-quality logic, the real CapacityAdmissionOrchestrator/
PortfolioLedger (capacity competition under a deliberately tight max_slots=1),
EntryService's real ADR-007 fill rule, MonitoringService's real target/time-exit
logic, the real Exp005AccountingSeam/PortfolioRepository execution ledger, and
then -- after the replay completes -- the real Stage 11 diagnostics loading
boundary, Stage 12-13 per-item diagnostics, and Stage 14 report aggregation, all
against the actual persisted database.

The synthetic price series is engineered so the outcome is fully predictable:
AAA is ranked above BBB every signal day and is the only symbol admitted (the
single slot fills immediately with AAA on day 0), so BBB is rejected NO_CAPACITY
on both of the two signal days. AAA fills at the open the next session, holds for
four sessions, then exits SELL_TARGET via an ambiguous intraday high touch
(exercising Section 20's exclusion rule through the full aggregation pipeline, not
just a unit test). This is what makes the specific assertions below possible --
this is not a "just don't crash" smoke test.

Per the frozen pre-registration and the standing authorization for this session:
this is a SYNTHETIC fixture, not a real Variant B/D run. No real financial result
is produced here, and none may be until this stage passes independent review and a
NEW manifest is generated specifically for that purpose.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pandas as pd
import pytest

import stock_analyzer.sandbox.exp005.application.real_run as real_run_module
from stock_analyzer.sandbox.domain.recommendation import SELL_TARGET
from stock_analyzer.sandbox.domain.replay import DEVELOPMENT_HISTORICAL_REPLAY, ReplayMetadata
from stock_analyzer.sandbox.exp005.application.real_run import run_real_experiment
from stock_analyzer.sandbox.exp005.config import VARIANT_B, Exp005Config, PortfolioConfig
from stock_analyzer.sandbox.exp005.diagnostics import mfe_mae, opportunity_cost
from stock_analyzer.sandbox.exp005.diagnostics._shared import full_market_calendar
from stock_analyzer.sandbox.exp005.diagnostics.diagnostics import load_diagnostics_context
from stock_analyzer.sandbox.exp005.diagnostics.financial_performance import (
    compute_feasibility_verdict,
    compute_financial_performance,
)
from stock_analyzer.sandbox.exp005.diagnostics.report_generator import compute_run_summary
from stock_analyzer.sandbox.exp005.domain.admission import NO_CAPACITY
from stock_analyzer.sandbox.exp005.infrastructure.frozen_artifacts import sha256_of_dataframe, sha256_of_file
from stock_analyzer.sandbox.exp005.infrastructure.repository import PortfolioRepository
from stock_analyzer.sandbox.exp005.infrastructure.schema import init_exp005_schema
from stock_analyzer.sandbox.exp005.manifest import build_experiment_manifest, write_manifest_artifact
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository

REPLAY_ID = "replay-stage15-synthetic"


def _weekdays(start: date, count: int) -> list[date]:
    dates: list[date] = []
    current = start
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current = date.fromordinal(current.toordinal() + 1)
    return dates


def _bar(symbol: str, d: date, o: float, h: float, low: float, c: float) -> dict:
    return {"symbol": symbol, "date": pd.Timestamp(d), "Open": o, "High": h, "Low": low, "Close": c, "Volume": 100_000}


def _flat_bar(symbol: str, d: date, close: float) -> dict:
    return _bar(symbol, d, close, close + 1.0, close - 1.0, close)


def _build_prices_df(warmup_dates: list[date], active_dates: list[date]) -> pd.DataFrame:
    rows = []
    for d in warmup_dates:
        rows.append(_flat_bar("AAA", d, 100.0))
        rows.append(_flat_bar("BBB", d, 50.0))

    # AAA: signal day0 (flat, still ~100) -> fills day1 at open=100.3 -> climbs
    # toward its +20% target (100.3 * 1.2 = 120.36) -> exits day6 via an
    # AMBIGUOUS intraday high touch (open=118 < target < high=121).
    aaa_active = [
        _flat_bar("AAA", active_dates[0], 100.0),                              # idx0: signal day
        _bar("AAA", active_dates[1], 100.3, 101.3, 99.3, 101.0),               # idx1: fill day (FILLED_AT_OPEN)
        _bar("AAA", active_dates[2], 101.5, 105.0, 101.0, 104.0),              # idx2: HOLD
        _bar("AAA", active_dates[3], 104.5, 109.0, 104.0, 108.0),              # idx3: HOLD
        _bar("AAA", active_dates[4], 108.5, 113.0, 108.0, 112.0),              # idx4: HOLD
        _bar("AAA", active_dates[5], 112.5, 118.0, 112.0, 116.0),              # idx5: HOLD (high 118 < target 120.36)
        _bar("AAA", active_dates[6], 118.0, 121.0, 117.0, 119.0),              # idx6: SELL_TARGET, ambiguous touch
    ]
    for d in active_dates[7:]:
        aaa_active.append(_flat_bar("AAA", d, 119.0))
    rows.extend(aaa_active)

    for d in active_dates:
        rows.append(_flat_bar("BBB", d, 50.0))

    return pd.DataFrame(rows)


def _write_parquet(path, df: pd.DataFrame) -> None:
    df.to_parquet(path)


def _build_frozen_snapshots(tmp_path, prices_df: pd.DataFrame):
    swing20_dir = tmp_path / "swing_20" / "snapshots" / "swing20_stage15"
    swing20_dir.mkdir(parents=True)
    other_df = pd.DataFrame({"symbol": ["AAA", "BBB"], "value": [1, 2]})
    artifact_dfs = {"universe": other_df, "prices": prices_df, "labels": other_df, "eligibility": other_df, "failures": other_df}
    artifact_hashes, artifacts_paths = {}, {}
    for name, df in artifact_dfs.items():
        path = swing20_dir / f"{name}.parquet"
        _write_parquet(path, df)
        artifact_hashes[name] = sha256_of_file(path)
        artifacts_paths[name] = str(path)
    import json

    with open(swing20_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump({"dataset_version": "swing20_stage15", "artifacts": artifacts_paths, "artifact_hashes": artifact_hashes}, f)

    feature_dir = tmp_path / "swing_20_features" / "snapshots" / "swing20_features_stage15"
    feature_dir.mkdir(parents=True)
    # Trivial content: the REAL universe/model are monkeypatched away (below), so
    # this file's DATA is never read for candidate generation -- only its hash is
    # verified (Stage 9-10's lineage check), which must reconcile with the manifest.
    features_df = pd.DataFrame({"symbol": ["AAA", "BBB"], "date": pd.to_datetime(["2026-01-01", "2026-01-01"]), "f1": [1.0, 2.0]})
    _write_parquet(feature_dir / "features.parquet", features_df)
    with open(feature_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "dataset_version": "swing20_features_stage15",
                "source_swing20_snapshot_id": "swing20_stage15",
                "source_swing20_snapshot_dir": str(swing20_dir),
                "source_swing20_artifact_hashes": artifact_hashes,
                "feature_dataset_hash": sha256_of_dataframe(features_df),
            },
            f,
        )
    return feature_dir, swing20_dir


class _SyntheticModelAdapter:
    model_version = "synthetic-stage15"
    fit_params = {"adv_edges": [0, 1], "adv_labels": ["adv_q1"]}
    feature_names = ("f1",)
    train_row_count = 100

    def score(self, features_df: pd.DataFrame) -> pd.Series:
        # AAA always outranks BBB, deterministically, so capacity competition is
        # fully predictable.
        return pd.Series({symbol: (2.0 if symbol == "AAA" else 1.0) for symbol in features_df.index})


class _SyntheticUniverseProvider:
    def __init__(self, rows_by_date: dict[date, pd.DataFrame]) -> None:
        self._rows_by_date = rows_by_date

    def features_for_date(self, as_of_date: date) -> pd.DataFrame:
        return self._rows_by_date.get(as_of_date, pd.DataFrame())


def _universe_row(symbol: str) -> dict:
    return {"symbol": symbol, "adv20": 5_000_000.0, "spy_trend": "Bull", "spy_volatility_bucket": "Normal"}


_MUTATION_GUARD_TABLES = (
    "portfolio_admissions", "slot_reservations", "executions", "portfolio_equity_snapshots",
    "virtual_positions", "virtual_transactions", "entry_orders",
)


def _table_fingerprint(conn: sqlite3.Connection) -> dict[str, tuple]:
    """A cheap, exact snapshot of every row in every decision-time/accounting
    table this diagnostics pass must never mutate -- compared before/after to
    prove the diagnostics calls below are strictly read-only."""

    fingerprint = {}
    for table in _MUTATION_GUARD_TABLES:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        fingerprint[table] = tuple(tuple(row) for row in rows)
    return fingerprint


def _make_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    init_exp005_schema(conn)
    return conn


@pytest.fixture(autouse=True)
def _default_clean_commit(monkeypatch):
    monkeypatch.setattr(real_run_module, "_current_code_commit_sha", lambda *a, **kw: "abc123")
    monkeypatch.setattr(real_run_module, "_working_tree_is_clean", lambda *a, **kw: True)


def test_synthetic_end_to_end_pipeline(tmp_path, monkeypatch):
    warmup_dates = _weekdays(date(2026, 1, 5), 25)
    active_dates = _weekdays(warmup_dates[-1], 16)[1:]  # 15 active weekdays after warmup
    assert len(active_dates) == 15

    prices_df = _build_prices_df(warmup_dates, active_dates)
    feature_dir, swing20_dir = _build_frozen_snapshots(tmp_path, prices_df)

    config = Exp005Config(
        variant_id=VARIANT_B,
        portfolio=PortfolioConfig(starting_capital=20_000.0, max_slots=1, slot_budget=20_000.0),
    )
    signal_start, signal_end, outcome_end = active_dates[0], active_dates[1], active_dates[-1]
    manifest = build_experiment_manifest(config, feature_dir, signal_start, signal_end, outcome_end, code_commit_sha="abc123")
    manifest_path = tmp_path / "experiment_manifest.json"
    write_manifest_artifact(manifest, manifest_path)

    rows_by_date = {
        active_dates[0]: pd.DataFrame([_universe_row("AAA"), _universe_row("BBB")]).set_index("symbol"),
        active_dates[1]: pd.DataFrame([_universe_row("AAA"), _universe_row("BBB")]).set_index("symbol"),
    }
    monkeypatch.setattr(real_run_module, "Model2PredictionAdapter", lambda path: _SyntheticModelAdapter())
    monkeypatch.setattr(real_run_module, "HistoricalFeatureUniverseProvider", lambda path: _SyntheticUniverseProvider(rows_by_date))

    conn = _make_connection()
    replay_metadata_template = ReplayMetadata(
        replay_id=REPLAY_ID,
        classification=DEVELOPMENT_HISTORICAL_REPLAY,
        signal_start_date=signal_start,
        signal_end_date=signal_end,
        outcome_data_end_date=outcome_end,
        configuration_json="{}",
        configuration_hash="placeholder",
        started_at=datetime.now(timezone.utc),
    )

    # --- The full real pipeline, actually running: candidate generation, capacity
    # admission, entry fills, monitoring, accounting -- nothing here is a spy/fake
    # except the model/universe (see module docstring).
    result = run_real_experiment(conn, manifest_path, feature_dir, config, replay_metadata_template, active_dates)
    assert result is not None

    sandbox_repo = SandboxRepository(conn)
    stored_replay = sandbox_repo.get_replay_metadata(REPLAY_ID)
    assert stored_replay is not None
    assert stored_replay.status == "COMPLETED"

    filled_orders = sandbox_repo.list_filled_orders()
    assert [o.symbol for o in filled_orders] == ["AAA"]
    assert filled_orders[0].fill_reason == "FILLED_AT_OPEN"

    positions = sandbox_repo.list_all_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "AAA"
    assert positions[0].status == "CLOSED"
    assert positions[0].exit_reason == SELL_TARGET

    portfolio_repo = PortfolioRepository(conn)
    admissions = portfolio_repo.list_admissions_for_experiment(REPLAY_ID)
    no_capacity = [a for a in admissions if a.decision == NO_CAPACITY]
    assert len(no_capacity) == 2
    assert {a.symbol for a in no_capacity} == {"BBB"}

    # --- Mutation guard: everything from here on is diagnostics-only. Snapshot
    # every decision-time/accounting table now, and re-check bit-for-bit at the
    # very end of the test that not one row changed.
    fingerprint_before = _table_fingerprint(conn)

    # --- Stage 11: the real, unpatched diagnostics loading boundary, against the
    # database this real replay actually produced.
    context = load_diagnostics_context(conn, manifest_path, feature_dir, REPLAY_ID)
    calendar = full_market_calendar(context.prices_df)

    # --- Stage 12-13/14: the real per-item diagnostics and report aggregation.
    summary = compute_run_summary(context, REPLAY_ID, VARIANT_B, None, calendar)

    assert summary.buy.filled_count == 1
    assert summary.buy.expired_count == 0
    assert summary.buy.fill_rate == pytest.approx(1.0)
    assert summary.buy.entry_session_ambiguity_count == 0  # FILLED_AT_OPEN

    # MonitoringService processes every open position each day INCLUDING the entry
    # day itself (entry happens before monitoring within the same day's
    # processing), so idx1 (fill day, holding day 1, not yet at target) also gets
    # its own HOLD snapshot -- idx1..idx5 = 5, not just idx2..idx5.
    assert summary.hold.hold_decision_count == 5

    assert summary.sell.closed_position_count == 1
    assert summary.sell.target_exit_count == 1
    assert summary.sell.time_exit_count == 0
    assert summary.sell.mean_realized_return_pct is not None
    assert summary.sell.mean_realized_return_pct > 0  # AAA closed for a gain
    assert summary.sell.mean_mfe_captured_pct is not None

    assert summary.capacity.no_capacity_count == 2
    assert summary.capacity.hypothetical_fill_rate is not None  # BBB's own hypothetical-fill check ran without error

    # --- Corrected target-exit MFE boundary (Stage 11-15 closure, finding 4):
    # AAA's SELL_TARGET was an ambiguous intraday touch -- MFE must never be
    # reported below the known realized return.
    aaa_position = positions[0]
    mfe_result = mfe_mae.compute_mfe_mae(context, aaa_position)
    assert mfe_result.mfe_pct >= mfe_result.realized_or_mtm_return_pct
    assert mfe_result.peak_to_exit_giveback_pct >= 0.0
    assert mfe_result.exit_efficiency is None or mfe_result.exit_efficiency <= 1.0 + 1e-9

    # --- Historical capacity occupants reconstructed from LOGICAL dates (Stage
    # 11-15 closure, finding 3): BBB's first admission (idx0, same day as AAA's
    # own acceptance) sees AAA as a still-pending RESERVATION; BBB's second
    # admission (idx1, the day AAA actually fills) sees AAA as an OPEN POSITION
    # instead, since entries are processed before that day's own admission phase.
    bbb_admissions = sorted((a for a in admissions if a.symbol == "BBB"), key=lambda a: a.as_of_date)
    assert len(bbb_admissions) == 2
    day0_result = opportunity_cost.compute_opportunity_cost(context, bbb_admissions[0], calendar)
    assert [r.symbol for r in day0_result.occupying_reservations] == ["AAA"]
    assert day0_result.occupying_open_positions == ()
    day1_result = opportunity_cost.compute_opportunity_cost(context, bbb_admissions[1], calendar)
    assert day1_result.occupying_reservations == ()
    assert [p.symbol for p in day1_result.occupying_open_positions] == ["AAA"]

    # --- Censored observations excluded from complete-horizon aggregates
    # (Stage 11-15 closure, finding 2): only 8 trailing sessions exist after
    # AAA's exit, so the 20-session SELL horizon cannot be fully observed.
    assert summary.sell.horizon_complete_count[20] < 20
    assert (
        summary.sell.horizon_censored_end_of_experiment_count[20]
        + summary.sell.horizon_censored_missing_market_data_count[20]
    ) > 0
    # The 1-session horizon, by contrast, is fully observed.
    assert summary.sell.horizon_complete_count[1] == 1
    assert summary.sell.horizon_censored_end_of_experiment_count[1] == 0

    # --- The new financial-performance report and feasibility verdict (Stage
    # 11-15 closure, finding 1): the module that actually answers "did this
    # policy make money," entirely absent before this closure cycle.
    financial_report = compute_financial_performance(context, REPLAY_ID, VARIANT_B, None)
    assert financial_report.starting_equity == pytest.approx(20_000.0)
    assert financial_report.net_pnl > 0  # AAA's single closed trade was a gain
    assert financial_report.closed_trade_count == 1
    assert financial_report.win_count == 1
    assert financial_report.loss_count == 0
    assert financial_report.largest_closed_winning_trade is not None
    assert financial_report.largest_closed_winning_trade.symbol == "AAA"

    feasibility_criteria = context.manifest.feasibility_criteria
    # No Variant D seeds were run in this synthetic fixture (out of scope for
    # Stage 15 -- see the completion report), so the percentile comparison is
    # explicitly undetermined (None). With exactly one closed trade, that same
    # trade is necessarily 100% of net P&L -- a CONFIRMED failure of the
    # largest-winner concentration criterion (threshold 50%). Per the
    # three-tier verdict logic (Stage 11-15 second closure, finding 2), a
    # confirmed failure always wins over an unrelated undetermined criterion,
    # so the overall verdict must be False here, not None.
    verdict = compute_feasibility_verdict(financial_report, [], feasibility_criteria)
    percentile_criterion = next(c for c in verdict.criteria if c.name == "beats_control_percentile")
    assert percentile_criterion.passed is None
    concentration_criterion = next(
        c for c in verdict.criteria if c.name == "largest_winner_concentration_within_threshold"
    )
    assert concentration_criterion.passed is False
    assert verdict.verdict is False
    positive_pnl_criterion = next(c for c in verdict.criteria if c.name == "positive_net_pnl")
    assert positive_pnl_criterion.passed is True

    # --- No diagnostic call above mutated any decision-time/accounting table.
    fingerprint_after = _table_fingerprint(conn)
    assert fingerprint_after == fingerprint_before
