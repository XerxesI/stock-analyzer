from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

import pytest

from stock_analyzer.sandbox.domain.candidate import RankedCandidate
from stock_analyzer.sandbox.domain.entry_order import EntryOrder
from stock_analyzer.sandbox.domain.position import VirtualPosition
from stock_analyzer.sandbox.domain.run import SandboxRun
from stock_analyzer.sandbox.infrastructure.schema import init_db
from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository
from stock_analyzer.sandbox.reporting.daily_json_report import write_json_report
from stock_analyzer.sandbox.reporting.daily_markdown_report import render_markdown, write_markdown_report
from stock_analyzer.sandbox.reporting.report_data import build_daily_report_data

AS_OF = date(2026, 6, 15)


@pytest.fixture
def repo() -> SandboxRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return SandboxRepository(conn)


def _seed(repo: SandboxRepository) -> None:
    run_id = SandboxRun.make_id(AS_OF, "generate-candidates")
    repo.create_run(
        SandboxRun(
            run_id=run_id,
            as_of_date=AS_OF,
            command="generate-candidates",
            started_at=datetime.now(timezone.utc),
            configuration_hash="test",
        )
    )
    actionable = RankedCandidate(
        candidate_id=RankedCandidate.make_id(AS_OF, "AAA"),
        run_id=run_id,
        as_of_date=AS_OF,
        symbol="AAA",
        daily_rank=1,
        model_score=0.8,
        signal_close=10.0,
        atr14=0.5,
        max_entry_price=10.2,
        shadow_top10=True,
        actionable=True,
        exclusion_reason=None,
        adv_quintile="adv_q1",
        market_regime="Bull_Normal",
    )
    excluded = RankedCandidate(
        candidate_id=RankedCandidate.make_id(AS_OF, "BBB"),
        run_id=run_id,
        as_of_date=AS_OF,
        symbol="BBB",
        daily_rank=2,
        model_score=0.7,
        signal_close=20.0,
        atr14=None,
        max_entry_price=None,
        shadow_top10=True,
        actionable=False,
        exclusion_reason="MISSING_ATR",
        adv_quintile="adv_q2",
        market_regime="Bull_Normal",
    )
    repo.insert_ranked_candidate(actionable)
    repo.insert_ranked_candidate(excluded)

    order = EntryOrder(
        order_id=EntryOrder.make_id(actionable.candidate_id),
        candidate_id=actionable.candidate_id,
        symbol="AAA",
        signal_date=AS_OF,
        created_date=AS_OF,
        valid_until=date(2026, 6, 17),
        max_entry_price=10.2,
        status="PENDING",
    )
    repo.create_entry_order(order)


def test_build_daily_report_data_reflects_persisted_state(repo: SandboxRepository):
    _seed(repo)

    data = build_daily_report_data(repo, AS_OF)

    assert data.as_of_date == AS_OF.isoformat()
    assert len(data.shadow_top10) == 2
    assert [c["symbol"] for c in data.actionable_candidates] == ["AAA"]
    assert [c["symbol"] for c in data.exclusions] == ["BBB"]
    assert data.exclusions[0]["exclusion_reason"] == "MISSING_ATR"
    assert len(data.pending_entries) == 1
    assert data.pending_entries[0]["symbol"] == "AAA"


def test_json_report_is_written_and_parseable(repo: SandboxRepository, tmp_path):
    _seed(repo)
    data = build_daily_report_data(repo, AS_OF)

    out_path = write_json_report(data, output_root=str(tmp_path))

    assert out_path.exists()
    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed["as_of_date"] == AS_OF.isoformat()
    assert len(parsed["shadow_top10"]) == 2


def test_markdown_report_contains_key_sections(repo: SandboxRepository, tmp_path):
    _seed(repo)
    data = build_daily_report_data(repo, AS_OF)

    text = render_markdown(data)

    assert "# SWING_20 Sandbox Daily Report" in text
    assert "AAA" in text
    assert "BBB" in text
    assert "MISSING_ATR" in text

    out_path = write_markdown_report(data, output_root=str(tmp_path))
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8") == text
