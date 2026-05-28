from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import ALGORITHM, SECRET_KEY, get_current_user, get_db
from models import User

router = APIRouter()


class AuthRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _create_token(user: User) -> str:
    payload = {
        "user_id": user.id,
        "username": user.username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


@router.post("/register", response_model=TokenResponse)
def register(body: AuthRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == body.username).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    if len(body.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at least 6 characters",
        )
    user = User(
        username=body.username,
        password_hash=bcrypt.hashpw(
            body.password.encode(), bcrypt.gensalt()
        ).decode(),
        status="active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = _create_token(user)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(body: AuthRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or user.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = _create_token(user)
    return TokenResponse(access_token=token)


@router.get("/me")
def me(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == current_user["user_id"]).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": user.id,
        "username": user.username,
        "status": user.status,
        "is_trading_enabled": user.is_trading_enabled,
        "node_id": user.node_id,
    }


class TradeToggleResponse(BaseModel):
    is_trading_enabled: bool


@router.patch("/me/trade-toggle", response_model=TradeToggleResponse)
def trade_toggle(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == current_user["user_id"]).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_trading_enabled = not user.is_trading_enabled
    db.commit()
    db.refresh(user)
    return TradeToggleResponse(is_trading_enabled=user.is_trading_enabled)
