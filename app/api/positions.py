from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from app.api.deps import get_db, get_current_user
from models import ArbitrageTask, OrderRecord, FillRecord, PositionSnapshot

router = APIRouter()


@router.get("/positions")
def list_positions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    query = db.query(ArbitrageTask).filter(
        ArbitrageTask.user_id == user["user_id"],
        ArbitrageTask.status.in_(["HOLDING", "OPEN_HEDGED"]),
    )
    total = query.count()
    tasks = (
        query.order_by(ArbitrageTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    result_items = []
    for task in tasks:
        fills = db.query(FillRecord).filter(FillRecord.task_id == task.id).all()
        total_fill_cost = sum(f.fill_cost for f in fills)
        total_fee = sum(f.fee_cost or 0 for f in fills)

        snap = (
            db.query(PositionSnapshot)
            .filter(PositionSnapshot.task_id == task.id)
            .order_by(PositionSnapshot.created_at.desc())
            .first()
        )

        result_items.append({
            "id": task.id,
            "task_uuid": task.task_uuid,
            "task_type": task.task_type,
            "symbol": task.symbol,
            "spot_exchange": task.spot_exchange,
            "derivative_exchange": task.derivative_exchange,
            "target_notional": task.target_notional,
            "expected_spread_bps": task.expected_spread_bps,
            "expected_funding_bps": task.expected_funding_bps,
            "status": task.status,
            "execution_status": task.execution_status,
            "auto_recovery_status": task.auto_recovery_status,
            "failure_reason": task.failure_reason,
            "filled_notional": total_fill_cost,
            "realized_pnl": snap.realized_pnl if snap else task.realized_pnl,
            "unrealized_pnl": snap.unrealized_pnl if snap else None,
            "total_fee": total_fee,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        })

    return {"items": result_items, "total": total, "page": page, "page_size": page_size}


@router.get("/positions/pnl-history")
def pnl_history(
    days: int = Query(30, ge=1, le=365),
    user: dict = Depends(get_current_user),
):
    from app.api.deps import _session_factory
    try:
        db = _session_factory()
    except Exception:
        return {"points": [], "total_realized_pnl": 0}

    try:
        cutoff = datetime.utcnow() - timedelta(days=days)

        closed = (
            db.query(ArbitrageTask)
            .filter(
                ArbitrageTask.user_id == user["user_id"],
                ArbitrageTask.status == "CLOSED",
                ArbitrageTask.finished_at >= cutoff,
                ArbitrageTask.realized_pnl != None,
            )
            .all()
        )

        daily_pnl: dict[str, float] = {}
        for task in closed:
            day = task.finished_at.strftime("%Y-%m-%d") if task.finished_at else None
            if day and task.realized_pnl is not None:
                daily_pnl[day] = daily_pnl.get(day, 0) + float(task.realized_pnl)

        fee_sums = (
            db.query(
                func.date(FillRecord.created_at).label("day"),
                func.sum(FillRecord.fee_cost).label("total_fee"),
            )
            .join(ArbitrageTask, FillRecord.task_id == ArbitrageTask.id)
            .filter(
                ArbitrageTask.user_id == user["user_id"],
                FillRecord.created_at >= cutoff,
                FillRecord.fee_cost != None,
            )
            .group_by("day")
            .order_by("day")
            .all()
        )

        fee_by_day: dict[str, float] = {}
        for row in fee_sums:
            fee_by_day[str(row.day)] = float(row.total_fee or 0)

        all_dates = sorted(set(list(daily_pnl.keys()) + list(fee_by_day.keys())))

        cumulative = 0.0
        points = []
        for d in all_dates:
            cumulative += daily_pnl.get(d, 0) - fee_by_day.get(d, 0)
            points.append({"date": d, "cumulative_pnl": round(cumulative, 2)})

        return {"points": points, "total_realized_pnl": round(cumulative, 2)}
    finally:
        db.close()
