from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from models import User

router = APIRouter()


class UserUpdate(BaseModel):
    node_id: str | None = None
    is_trading_enabled: bool | None = None
    status: str | None = None


def _user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "status": user.status,
        "risk_level": user.risk_level,
        "home_region": user.home_region,
        "is_trading_enabled": user.is_trading_enabled,
        "node_id": user.node_id,
        "email": user.email,
        "feishu_webhook_url": user.feishu_webhook_url,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


@router.get("/users")
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin=Depends(require_role("superadmin", "ops_admin")),
):
    query = db.query(User).order_by(User.id)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [_user_to_dict(u) for u in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    admin=Depends(require_role("superadmin", "ops_admin")),
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.node_id is not None:
        user.node_id = body.node_id
    if body.is_trading_enabled is not None:
        user.is_trading_enabled = body.is_trading_enabled
    if body.status is not None:
        user.status = body.status
    db.commit()
    db.refresh(user)
    return _user_to_dict(user)
