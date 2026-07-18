"""Assembles the data both the JSON and Markdown daily reports render, from persisted
state only (MVP 2 spec section 14) -- a single query pass shared by both renderers so
they never drift out of sync with each other."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stock_analyzer.sandbox.infrastructure.sqlite_repository import SandboxRepository


@dataclass
class DailyReportData:
    as_of_date: str
    run_status: dict
    shadow_top10: list[dict]
    actionable_candidates: list[dict]
    exclusions: list[dict]
    pending_entries: list[dict]
    filled_today: list[dict]
    expired_or_skipped_today: list[dict]
    open_positions: list[dict]
    exits_today: list[dict]
    realized_pnl_today: float
    unrealized_pnl_open: float
    data_quality_alerts: list[dict]


def build_daily_report_data(repo: SandboxRepository, as_of_date: date) -> DailyReportData:
    candidates = repo.get_candidates_for_date(as_of_date)
    shadow = [c for c in candidates if c.shadow_top10]
    actionable = [c for c in candidates if c.actionable]
    exclusions = [c for c in shadow if not c.actionable]

    pending_orders = [o for o in repo.get_pending_orders()]
    filled_today = repo.get_orders_filled_on(as_of_date)
    expired_or_skipped_today = repo.get_orders_expired_or_skipped_on(as_of_date)

    open_positions = repo.get_open_positions()
    exits_today = repo.get_positions_closed_on(as_of_date)
    snapshots_today = {s.position_id: s for s in repo.get_snapshots_for_date(as_of_date)}

    dq_alerts = repo.get_data_quality_events_for_date(as_of_date)

    realized_pnl_today = sum(
        (p.exit_price - p.entry_price) * p.quantity for p in exits_today if p.exit_price is not None
    )
    unrealized_pnl_open = sum(
        (p.current_close - p.entry_price) * p.quantity for p in open_positions if p.current_close is not None
    )

    def _candidate_dict(c) -> dict:
        return {
            "symbol": c.symbol,
            "rank": c.daily_rank,
            "model_score": c.model_score,
            "signal_close": c.signal_close,
            "atr14": c.atr14,
            "max_entry_price": c.max_entry_price,
            "actionable": c.actionable,
            "exclusion_reason": c.exclusion_reason,
            "adv_quintile": c.adv_quintile,
            "market_regime": c.market_regime,
        }

    def _order_dict(o) -> dict:
        return {
            "order_id": o.order_id,
            "symbol": o.symbol,
            "signal_date": o.signal_date.isoformat(),
            "valid_until": o.valid_until.isoformat(),
            "max_entry_price": o.max_entry_price,
            "status": o.status,
            "fill_date": o.fill_date.isoformat() if o.fill_date else None,
            "fill_price": o.fill_price,
            "no_fill_reason": o.no_fill_reason,
        }

    def _position_dict(p) -> dict:
        snapshot = snapshots_today.get(p.position_id)
        return {
            "position_id": p.position_id,
            "symbol": p.symbol,
            "entry_date": p.entry_date.isoformat(),
            "entry_price": p.entry_price,
            "quantity": p.quantity,
            "target_price": p.target_price,
            "planned_time_exit_date": p.planned_time_exit_date.isoformat(),
            "status": p.status,
            "current_holding_day_count": p.current_holding_day_count,
            "current_close": p.current_close,
            "unrealized_return": p.unrealized_return,
            "mfe": p.mfe,
            "mae": p.mae,
            "current_recommendation": snapshot.recommendation if snapshot else None,
        }

    def _exit_dict(p) -> dict:
        return {
            "position_id": p.position_id,
            "symbol": p.symbol,
            "entry_date": p.entry_date.isoformat(),
            "entry_price": p.entry_price,
            "exit_date": p.exit_date.isoformat() if p.exit_date else None,
            "exit_price": p.exit_price,
            "exit_reason": p.exit_reason,
            "realized_return": p.realized_return,
        }

    def _dq_dict(e) -> dict:
        return {"symbol": e.symbol, "event_type": e.event_type, "details": e.details}

    return DailyReportData(
        as_of_date=as_of_date.isoformat(),
        run_status={"status": "COMPLETED"},
        shadow_top10=[_candidate_dict(c) for c in sorted(shadow, key=lambda c: c.daily_rank)],
        actionable_candidates=[_candidate_dict(c) for c in sorted(actionable, key=lambda c: c.daily_rank)],
        exclusions=[_candidate_dict(c) for c in exclusions],
        pending_entries=[_order_dict(o) for o in pending_orders],
        filled_today=[_order_dict(o) for o in filled_today],
        expired_or_skipped_today=[_order_dict(o) for o in expired_or_skipped_today],
        open_positions=[_position_dict(p) for p in open_positions],
        exits_today=[_exit_dict(p) for p in exits_today],
        realized_pnl_today=round(realized_pnl_today, 2),
        unrealized_pnl_open=round(unrealized_pnl_open, 2),
        data_quality_alerts=[_dq_dict(e) for e in dq_alerts],
    )
