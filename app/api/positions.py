from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
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
