from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.deps import get_db, get_current_admin, require_role
from models import Announcement, AdminActionLog

router = APIRouter()


class AnnouncementBody(BaseModel):
    title: str
    content: str
    priority: int = 100
    is_pinned: bool = False
    audience_type: str = "all"
    channels: list[str] = []
    status: str = "published"


def _log(db: Session, admin_id: int, action: str, target_type: str, target_id: str,
         before: dict | None, after: dict | None):
    db.add(AdminActionLog(
        admin_user_id=admin_id, action_type=action,
        target_type=target_type, target_id=target_id,
        before_json=before, after_json=after,
    ))
    db.commit()


@router.get("/announcements")
def list_announcements(db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    return {"announcements": db.query(Announcement).order_by(Announcement.created_at.desc()).all()}


@router.post("/announcements")
def create_announcement(body: AnnouncementBody, db: Session = Depends(get_db),
                        admin=Depends(require_role("superadmin", "ops_admin"))):
    a = Announcement(
        title=body.title, content=body.content, priority=body.priority,
        is_pinned=body.is_pinned, audience_type=body.audience_type,
        channels_json=body.channels, status=body.status,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    _log(db, admin["admin_id"], "create", "announcement", str(a.id), None, {"title": body.title})
    return {"ok": True, "announcement": a}


@router.put("/announcements/{announcement_id}")
def update_announcement(announcement_id: int, body: AnnouncementBody,
                        db: Session = Depends(get_db),
                        admin=Depends(require_role("superadmin", "ops_admin"))):
    a = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not a:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    before = {"title": a.title, "status": a.status}
    a.title = body.title
    a.content = body.content
    a.priority = body.priority
    a.is_pinned = body.is_pinned
    a.audience_type = body.audience_type
    a.channels_json = body.channels
    a.status = body.status
    db.commit()
    _log(db, admin["admin_id"], "update", "announcement", str(announcement_id),
         before, {"title": a.title, "status": a.status})
    return {"ok": True, "announcement": a}


@router.delete("/announcements/{announcement_id}")
def delete_announcement(announcement_id: int, db: Session = Depends(get_db),
                        admin=Depends(require_role("superadmin", "ops_admin"))):
    a = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not a:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db.delete(a)
    db.commit()
    _log(db, admin["admin_id"], "delete", "announcement", str(announcement_id), None, None)
    return {"ok": True}
