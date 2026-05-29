from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from models import ArbitrageTask, StrategyConfig, FillRecord, PositionSnapshot

router = APIRouter()


@router.get("/summary")
def dashboard_summary(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    user_id = user["user_id"]

    strategies = (
        db.query(StrategyConfig)
        .filter(StrategyConfig.user_id == user_id)
        .all()
    )
    active_strategies = sum(1 for s in strategies if s.is_enabled)

    positions = (
        db.query(ArbitrageTask)
        .filter(
            ArbitrageTask.user_id == user_id,
            ArbitrageTask.status.in_(["HOLDING", "OPEN_HEDGED", "OPEN_PARTIAL", "RUNNING"]),
        )
        .all()
    )
    open_positions_count = len(positions)

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_tasks = (
        db.query(ArbitrageTask)
        .filter(
            ArbitrageTask.user_id == user_id,
            ArbitrageTask.finished_at >= today_start,
            ArbitrageTask.status.in_(["CLOSED", "SUCCEEDED"]),
        )
        .all()
    )
    today_trades_count = len(today_tasks)

    today_pnl_rows = (
        db.query(PositionSnapshot)
        .filter(
            PositionSnapshot.user_id == user_id,
            PositionSnapshot.closed_at >= today_start,
            PositionSnapshot.snapshot_type.in_(["close", "partial_close"]),
        )
        .all()
    )
    today_realized_pnl = round(sum(float(r.realized_pnl or 0) for r in today_pnl_rows), 2)

    today_fees = (
        db.query(FillRecord)
        .filter(
            FillRecord.task_uuid.in_(
                db.query(ArbitrageTask.task_uuid).filter(
                    ArbitrageTask.user_id == user_id,
                    ArbitrageTask.finished_at >= today_start,
                ).subquery()
            ),
        )
        .all()
    )
    today_total_fee = round(sum(float(f.fee_cost or 0) for f in today_fees), 2)

    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    week_pnl_rows = (
        db.query(PositionSnapshot)
        .filter(
            PositionSnapshot.user_id == user_id,
            PositionSnapshot.closed_at >= seven_days_ago,
            PositionSnapshot.snapshot_type.in_(["close", "partial_close"]),
        )
        .all()
    )
    week_pnl = round(sum(float(r.realized_pnl or 0) for r in week_pnl_rows), 2)

    total_tasks = (
        db.query(ArbitrageTask)
        .filter(ArbitrageTask.user_id == user_id)
        .count()
    )

    return {
        "stats": {
            "active_strategies": active_strategies,
            "open_positions": open_positions_count,
            "today_trades": today_trades_count,
            "total_trades": total_tasks,
        },
        "pnl": {
            "today": today_realized_pnl,
            "week": week_pnl,
            "today_fees": today_total_fee,
            "today_net": round(today_realized_pnl - today_total_fee, 2),
        },
        "checked_at": datetime.utcnow().isoformat(),
    }
