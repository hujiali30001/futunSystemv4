from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from models import StrategyConfig

router = APIRouter()


class StrategyCreate(BaseModel):
    name: str
    symbol: str
    spot_exchange: str
    derivative_exchange: str
    target_quote_amount: float = 100.0
    open_spread_bps_threshold: float = 100.0
    close_spread_bps_threshold: float = 10.0
    open_tiers_json: list | None = None
    close_tiers_json: list | None = None
    max_single_task_notional: float | None = None
    max_loss_usdt: float | None = None


class StrategyUpdate(BaseModel):
    name: str | None = None
    target_quote_amount: float | None = None
    open_spread_bps_threshold: float | None = None
    close_spread_bps_threshold: float | None = None
    open_tiers_json: list | None = None
    close_tiers_json: list | None = None
    max_single_task_notional: float | None = None
    max_loss_usdt: float | None = None


def _strategy_to_dict(s: StrategyConfig) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "strategy_type": s.strategy_type,
        "symbol_scope_json": s.symbol_scope_json,
        "exchange_scope_json": s.exchange_scope_json,
        "target_quote_amount": s.target_quote_amount,
        "open_spread_bps_threshold": s.open_spread_bps_threshold,
        "close_spread_bps_threshold": s.close_spread_bps_threshold,
        "open_tiers_json": s.open_tiers_json,
        "close_tiers_json": s.close_tiers_json,
        "max_single_task_notional": s.max_single_task_notional,
        "max_loss_usdt": s.max_loss_usdt,
        "is_enabled": s.is_enabled,
    }


@router.get("")
def list_strategies(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    strategies = (
        db.query(StrategyConfig)
        .filter(StrategyConfig.user_id == current_user["user_id"])
        .order_by(StrategyConfig.id.desc())
        .all()
    )
    return [_strategy_to_dict(s) for s in strategies]


@router.post("")
def create_strategy(
    body: StrategyCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sym = body.symbol.strip()
    if "/" not in sym:
        sym = sym + "/USDT"
    strategy = StrategyConfig(
        user_id=current_user["user_id"],
        strategy_type="spot_futures",
        name=body.name,
        symbol_scope_json=[sym],
        exchange_scope_json=[body.spot_exchange, body.derivative_exchange],
        target_quote_amount=body.target_quote_amount,
        open_spread_bps_threshold=body.open_spread_bps_threshold,
        close_spread_bps_threshold=body.close_spread_bps_threshold,
        open_tiers_json=body.open_tiers_json
        or [{"spread_bps": body.open_spread_bps_threshold, "ratio": 1.0}],
        close_tiers_json=body.close_tiers_json
        or [{"spread_bps": body.close_spread_bps_threshold, "ratio": 1.0}],
        max_single_task_notional=body.max_single_task_notional or 0.0,
        max_loss_usdt=body.max_loss_usdt,
        is_enabled=True,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return _strategy_to_dict(strategy)


@router.put("/{strategy_id}")
def update_strategy(
    strategy_id: int,
    body: StrategyUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    strategy = (
        db.query(StrategyConfig)
        .filter(
            StrategyConfig.id == strategy_id,
            StrategyConfig.user_id == current_user["user_id"],
        )
        .first()
    )
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(strategy, key, value)
    db.commit()
    db.refresh(strategy)
    return _strategy_to_dict(strategy)


@router.delete("/{strategy_id}")
def delete_strategy(
    strategy_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    strategy = (
        db.query(StrategyConfig)
        .filter(
            StrategyConfig.id == strategy_id,
            StrategyConfig.user_id == current_user["user_id"],
        )
        .first()
    )
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    db.delete(strategy)
    db.commit()
    return {"ok": True}


@router.patch("/{strategy_id}/toggle")
def toggle_strategy(
    strategy_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    strategy = (
        db.query(StrategyConfig)
        .filter(
            StrategyConfig.id == strategy_id,
            StrategyConfig.user_id == current_user["user_id"],
        )
        .first()
    )
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    strategy.is_enabled = not strategy.is_enabled
    db.commit()
    db.refresh(strategy)
    return _strategy_to_dict(strategy)
