from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.risk.daily_loss import DailyLossTracker
from app.risk.stop_loss import StopLossChecker

router = APIRouter()


@router.get("/status")
def risk_status(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    user_id = user["user_id"]

    daily_tracker = DailyLossTracker(db)
    daily = daily_tracker.check(user_id)

    stop_checker = StopLossChecker(db)
    stop_checks = stop_checker.check(user_id)

    return {
        "daily_loss": {
            "today_date": str(daily.date),
            "realized_pnl": round(daily.realized_pnl, 2),
            "limit_usdt": daily.limit,
            "exceeded": daily.exceeded,
        },
        "stop_loss_alerts": [
            {
                "strategy_id": c.strategy_id,
                "strategy_name": c.strategy_name,
                "max_loss_usdt": c.max_loss_usdt,
                "current_unrealized_pnl": round(c.current_unrealized_pnl, 2),
                "triggered": c.triggered,
            }
            for c in stop_checks
        ],
        "can_open_new_positions": not daily.exceeded,
        "checked_at": datetime.utcnow().isoformat(),
    }
