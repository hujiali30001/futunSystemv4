from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_user
from models import ArbitrageTask

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
    items = (
        query.order_by(ArbitrageTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
