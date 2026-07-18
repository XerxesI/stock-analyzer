"""Machine-readable daily report (MVP 2 spec section 14).

Written to artifacts/sandbox/daily/<as_of_date>/sandbox_daily_report.json --
generated output, excluded from git like every other artifacts/ path.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from stock_analyzer.sandbox.reporting.report_data import DailyReportData

DEFAULT_OUTPUT_ROOT = "artifacts/sandbox/daily"


def write_json_report(data: DailyReportData, output_root: str = DEFAULT_OUTPUT_ROOT) -> Path:
    out_dir = Path(output_root) / data.as_of_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "sandbox_daily_report.json"
    out_path.write_text(json.dumps(asdict(data), indent=2, default=str), encoding="utf-8")
    return out_path
