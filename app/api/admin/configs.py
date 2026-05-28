from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from models import PlatformConfig

router = APIRouter()


class ConfigUpdate(BaseModel):
    config_value: str


@router.get("/configs")
def list_configs(
    db: Session = Depends(get_db),
    admin=Depends(require_role("superadmin", "ops_admin")),
):
    rows = db.query(PlatformConfig).order_by(PlatformConfig.config_key).all()
    return [
        {
            "id": r.id,
            "config_key": r.config_key,
            "config_value": r.config_value,
            "config_type": r.config_type,
            "description": r.description,
            "updated_by": r.updated_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


@router.get("/configs/{config_key}")
def get_config(
    config_key: str,
    db: Session = Depends(get_db),
    admin=Depends(require_role("superadmin", "ops_admin")),
):
    row = db.query(PlatformConfig).filter(PlatformConfig.config_key == config_key).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return {
        "id": row.id,
        "config_key": row.config_key,
        "config_value": row.config_value,
        "config_type": row.config_type,
        "description": row.description,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.put("/configs/{config_key}")
def update_config(
    config_key: str,
    body: ConfigUpdate,
    db: Session = Depends(get_db),
    admin=Depends(require_role("superadmin", "ops_admin")),
):
    row = db.query(PlatformConfig).filter(PlatformConfig.config_key == config_key).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Config not found")
    row.config_value = body.config_value
    row.updated_by = admin["admin_user_id"]
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "config_key": row.config_key,
        "config_value": row.config_value,
        "config_type": row.config_type,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
