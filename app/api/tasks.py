from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from models import ArbitrageTask

router = APIRouter()


@router.get("")
def list_tasks(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = (
        db.query(ArbitrageTask)
        .filter(ArbitrageTask.user_id == current_user["user_id"])
    )
    total = query.count()
    tasks = (
        query.order_by(ArbitrageTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for t in tasks:
        items.append({
            "id": t.id,
            "task_uuid": t.task_uuid,
            "task_type": t.task_type,
            "symbol": t.symbol,
            "spot_exchange": t.spot_exchange,
            "derivative_exchange": t.derivative_exchange,
            "target_notional": t.target_notional,
            "expected_spread_bps": t.expected_spread_bps,
            "status": t.status,
            "execution_status": t.execution_status,
            "failure_reason": t.failure_reason,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        })
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
