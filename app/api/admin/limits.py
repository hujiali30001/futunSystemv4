from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.deps import get_db, get_current_admin, require_role
from models import RiskLimitRule, AdminActionLog

router = APIRouter()


class LimitCreate(BaseModel):
    scope_type: str
    scope_id: str
    limit_type: str
    limit_value: float
    enabled: bool = True
    priority: int = 100
    symbol: str | None = None
    exchange: str | None = None


class LimitUpdate(BaseModel):
    limit_value: float | None = None
    enabled: bool | None = None
    priority: int | None = None


def _log_action(db: Session, admin_id: int, action_type: str,
                target_type: str, target_id: str | None,
                before: dict | None, after: dict | None):
    db.add(AdminActionLog(
        admin_user_id=admin_id, action_type=action_type,
        target_type=target_type, target_id=target_id,
        before_json=before, after_json=after,
    ))
    db.commit()


@router.get("/limits")
def list_limits(db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    return {"limits": db.query(RiskLimitRule).order_by(RiskLimitRule.priority.desc()).all()}


@router.post("/limits")
def create_limit(body: LimitCreate, db: Session = Depends(get_db),
                 admin=Depends(require_role("superadmin", "risk_admin"))):
    rule = RiskLimitRule(
        scope_type=body.scope_type, scope_id=body.scope_id,
        limit_type=body.limit_type, limit_value=body.limit_value,
        enabled=body.enabled, priority=body.priority,
        symbol=body.symbol, exchange=body.exchange,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    _log_action(db, admin["admin_id"], "create", "limit_rule", str(rule.id), None,
                {"scope_type": body.scope_type, "limit_value": body.limit_value})
    return {"ok": True, "limit": rule}


@router.put("/limits/{rule_id}")
def update_limit(rule_id: int, body: LimitUpdate, db: Session = Depends(get_db),
                 admin=Depends(require_role("superadmin", "risk_admin"))):
    rule = db.query(RiskLimitRule).filter(RiskLimitRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    before = {"limit_value": rule.limit_value, "enabled": rule.enabled}
    if body.limit_value is not None:
        rule.limit_value = body.limit_value
    if body.enabled is not None:
        rule.enabled = body.enabled
    if body.priority is not None:
        rule.priority = body.priority
    db.commit()
    _log_action(db, admin["admin_id"], "update", "limit_rule", str(rule_id), before,
                {"limit_value": rule.limit_value, "enabled": rule.enabled})
    return {"ok": True, "limit": rule}


@router.delete("/limits/{rule_id}")
def delete_limit(rule_id: int, db: Session = Depends(get_db),
                 admin=Depends(require_role("superadmin", "risk_admin"))):
    rule = db.query(RiskLimitRule).filter(RiskLimitRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.delete(rule)
    db.commit()
    _log_action(db, admin["admin_id"], "delete", "limit_rule", str(rule_id), None, None)
    return {"ok": True}
