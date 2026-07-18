"""Human-readable daily report (MVP 2 spec section 14).

Written to artifacts/sandbox/daily/<as_of_date>/sandbox_daily_report.md -- generated
output, excluded from git like every other artifacts/ path.
"""

from __future__ import annotations

from pathlib import Path

from stock_analyzer.sandbox.reporting.report_data import DailyReportData

DEFAULT_OUTPUT_ROOT = "artifacts/sandbox/daily"


def _table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "_none_\n"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, sep]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
    return "\n".join(lines) + "\n"


def render_markdown(data: DailyReportData) -> str:
    parts = [
        f"# SWING_20 Sandbox Daily Report -- {data.as_of_date}",
        "",
        f"Run status: {data.run_status.get('status')}",
        "",
        "## Shadow top-10",
        _table(data.shadow_top10, ["symbol", "rank", "model_score", "signal_close", "max_entry_price", "actionable", "exclusion_reason"]),
        "## Actionable candidates (this run)",
        _table(data.actionable_candidates, ["symbol", "rank", "model_score", "max_entry_price"]),
        "## Exclusions",
        _table(data.exclusions, ["symbol", "rank", "exclusion_reason"]),
        "## Pending entries",
        _table(data.pending_entries, ["symbol", "signal_date", "valid_until", "max_entry_price", "status"]),
        "## Filled today",
        _table(data.filled_today, ["symbol", "fill_date", "fill_price"]),
        "## Expired or skipped today",
        _table(data.expired_or_skipped_today, ["symbol", "status", "no_fill_reason"]),
        "## Open positions",
        _table(
            data.open_positions,
            ["symbol", "entry_date", "entry_price", "current_close", "unrealized_return", "current_holding_day_count", "current_recommendation"],
        ),
        "## Exits today",
        _table(data.exits_today, ["symbol", "entry_price", "exit_price", "exit_reason", "realized_return"]),
        "",
        f"**Realized P&L today:** {data.realized_pnl_today}",
        f"**Unrealized P&L (open positions):** {data.unrealized_pnl_open}",
        "",
        "## Data quality alerts",
        _table(data.data_quality_alerts, ["symbol", "event_type", "details"]),
    ]
    return "\n".join(parts) + "\n"


def write_markdown_report(data: DailyReportData, output_root: str = DEFAULT_OUTPUT_ROOT) -> Path:
    out_dir = Path(output_root) / data.as_of_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "sandbox_daily_report.md"
    out_path.write_text(render_markdown(data), encoding="utf-8")
    return out_path
