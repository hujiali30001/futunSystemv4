from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.risk.daily_loss import DailyLossTracker
from app.risk.stop_loss import StopLossChecker
from app.risk.notifier import UserNotifier
from models import User

router = APIRouter()


def _mk_db():
    from app.api.deps import _session_factory
    return _session_factory()


@router.get("/status")
def risk_status(
    user: dict = Depends(get_current_user),
):
    user_id = user["user_id"]

    try:
        db = _mk_db()
        try:
            user_record = db.query(User).filter(User.id == user_id).first()
            daily = DailyLossTracker(db).check(user_id)
            stop_checks = StopLossChecker(db).check(user_id)
        finally:
            db.close()
    except Exception:
        return {
            "daily_loss": {"today_date": str(datetime.utcnow().date()), "realized_pnl": 0, "limit_usdt": None, "exceeded": False},
            "stop_loss_alerts": [],
            "can_open_new_positions": True,
            "has_alerts": False,
            "notify_channels": {"email": {"configured": False, "address": None}, "feishu": {"configured": False, "url": None}},
            "checked_at": datetime.utcnow().isoformat(),
        }

    has_alerts = daily.exceeded or any(c.triggered for c in stop_checks)
    email_ok = bool(user_record and user_record.email and user_record.smtp_config_json)
    feishu_ok = bool(user_record and user_record.feishu_webhook_url)

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
        "has_alerts": has_alerts,
        "notify_channels": {
            "email": {"configured": email_ok, "address": user_record.email if user_record else None},
            "feishu": {"configured": feishu_ok, "url": (user_record.feishu_webhook_url or "")[:30] + "..." if user_record and user_record.feishu_webhook_url else None},
        },
        "checked_at": datetime.utcnow().isoformat(),
    }


@router.post("/notify")
def risk_notify(
    user: dict = Depends(get_current_user),
):
    user_id = user["user_id"]

    try:
        db = _mk_db()
        try:
            user_record = db.query(User).filter(User.id == user_id).first()
            if user_record is None:
                return {"ok": False, "message": "user not found"}
            daily = DailyLossTracker(db).check(user_id)
            stop_checks = StopLossChecker(db).check(user_id)
            notifier = UserNotifier(db)
        finally:
            db.close()
    except Exception:
        return {"ok": False, "message": "internal error"}

    results = []
    if daily.exceeded:
        body = [
            f"date: {daily.date}",
            f"realized_pnl: {daily.realized_pnl:.2f} USDT",
            f"limit: {daily.limit} USDT",
            f"status: exceeded",
        ]
        r = notifier.send_risk_alert(user_record, "Daily Loss Exceeded", body)
        r["channel"] = "daily_loss"
        results.append(r)

    for c in stop_checks:
        if c.triggered:
            body = [
                f"strategy: {c.strategy_name} (ID={c.strategy_id})",
                f"stop_loss: -{c.max_loss_usdt} USDT",
                f"current: {c.current_unrealized_pnl:.2f} USDT",
                f"status: triggered",
            ]
            r = notifier.send_risk_alert(user_record, f"Stop Loss - {c.strategy_name}", body)
            r["channel"] = f"stop_loss:{c.strategy_id}"
            results.append(r)

    return {"ok": True, "sent": len(results), "results": results}
