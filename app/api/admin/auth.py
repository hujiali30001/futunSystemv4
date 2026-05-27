from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
import os

from app.api.deps import get_db, get_current_admin
from models import AdminUser

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET = os.getenv("ADMIN_JWT_SECRET_KEY", os.getenv("JWT_SECRET_KEY", "furun-dev-secret-change-in-production"))


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


@router.post("/login", response_model=AdminLoginResponse)
def admin_login(body: AdminLoginRequest, db: Session = Depends(get_db)):
    admin = db.query(AdminUser).filter(AdminUser.username == body.username).first()
    if not admin or not pwd_context.verify(body.password, admin.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    payload = {
        "admin_id": admin.id,
        "username": admin.username,
        "role": admin.role,
        "exp": datetime.utcnow() + timedelta(hours=24),
    }
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    return AdminLoginResponse(access_token=token, role=admin.role)


@router.get("/me")
def admin_me(admin: dict = Depends(get_current_admin)):
    return admin
