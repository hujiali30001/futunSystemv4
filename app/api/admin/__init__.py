from fastapi import APIRouter
from app.api.admin import auth, limits, switches, announcements, audit, users

router = APIRouter(prefix="/admin")

router.include_router(auth.router, tags=["admin-auth"])
router.include_router(limits.router, tags=["admin-limits"])
router.include_router(switches.router, tags=["admin-switches"])
router.include_router(announcements.router, tags=["admin-announcements"])
router.include_router(audit.router, tags=["admin-audit"])
router.include_router(users.router, tags=["admin-users"])
