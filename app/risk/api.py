from datetime import datetime

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.risk.daily_loss import DailyLossTracker
from app.risk.stop_loss import StopLossChecker
from app.risk.notifier import UserNotifier
from models import User

router = APIRouter()


def _db():
    from app.api.deps import _session_factory
    return _session_factory()


@router.get("/status")
def risk_status(user: dict = Depends(get_current_user)):
    uid = user["user_id"]
    try:
        db = _db()
    except Exception:
        return _empty()

    try:
        user_record = db.query(User).filter(User.id == uid).first()
        daily = DailyLossTracker(db).check(uid)
        stop_checks = StopLossChecker(db).check(uid)

        has_alerts = daily.exceeded or any(c.triggered for c in stop_checks)
        email_ok = bool(user_record and user_record.email and user_record.smtp_config_json)
        feishu_ok = bool(user_record and user_record.feishu_webhook_url)

        result = {
            "daily_loss": {
                "today_date": str(daily.date), "realized_pnl": round(daily.realized_pnl, 2),
                "limit_usdt": daily.limit, "exceeded": daily.exceeded,
            },
            "stop_loss_alerts": [
                {"strategy_id": c.strategy_id, "strategy_name": c.strategy_name,
                 "max_loss_usdt": c.max_loss_usdt,
                 "current_unrealized_pnl": round(c.current_unrealized_pnl, 2),
                 "triggered": c.triggered}
                for c in stop_checks
            ],
            "can_open_new_positions": not daily.exceeded,
            "has_alerts": has_alerts,
            "notify_channels": {
                "email": {"configured": email_ok, "address": user_record.email if user_record else None},
                "feishu": {"configured": feishu_ok, "url": (user_record.feishu_webhook_url or "")[:30] + "..." if user_record and user_record.feishu_webhook_url else None},
            },
            "checked_at": datetime.utcnow().isoformat(),
        }
        db.close()
        return result
    except Exception:
        try:
            db.close()
        except Exception:
            pass
        return _empty()


def _empty():
    return {
        "daily_loss": {"today_date": str(datetime.utcnow().date()), "realized_pnl": 0, "limit_usdt": 0, "exceeded": False},
        "stop_loss_alerts": [],
        "can_open_new_positions": True,
        "has_alerts": False,
        "notify_channels": {"email": {"configured": False, "address": None}, "feishu": {"configured": False, "url": None}},
        "checked_at": datetime.utcnow().isoformat(),
    }
