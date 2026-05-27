from fastapi import APIRouter
from app.api.admin import auth

router = APIRouter(prefix="/admin")

router.include_router(auth.router, tags=["admin-auth"])
