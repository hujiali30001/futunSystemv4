from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.api.deps import get_db, get_current_user
from app.risk.daily_loss import DailyLossTracker
from app.risk.stop_loss import StopLossChecker
from app.risk.notifier import UserNotifier
from models import User

router = APIRouter()
_migrated = False


def _ensure_migration(db: Session) -> None:
    global _migrated
    if _migrated:
        return
    try:
        db.execute(text("ALTER TABLE strategy_configs ADD COLUMN IF NOT EXISTS max_loss_usdt FLOAT"))
        db.commit()
        _migrated = True
    except Exception:
        db.rollback()


@router.get("/status")
def risk_status(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_migration(db)
    user_id = user["user_id"]
    user_record = db.query(User).filter(User.id == user_id).first()

    daily_tracker = DailyLossTracker(db)
    daily = daily_tracker.check(user_id)

    stop_checker = StopLossChecker(db)
    stop_checks = stop_checker.check(user_id)

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
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_migration(db)
    user_id = user["user_id"]
    user_record = db.query(User).filter(User.id == user_id).first()
    if user_record is None:
        return {"ok": False, "message": "user not found"}

    daily_tracker = DailyLossTracker(db)
    daily = daily_tracker.check(user_id)

    stop_checker = StopLossChecker(db)
    stop_checks = stop_checker.check(user_id)

    notifier = UserNotifier(db)
    results = []
    if daily.exceeded:
        body_lines = [
            f"日期: {daily.date}",
            f"今日已实现盈亏: {daily.realized_pnl:.2f} USDT",
            f"亏损上限: {daily.limit} USDT",
            f"状态: 已超限，新开仓已禁止",
        ]
        r = notifier.send_risk_alert(user_record, "日亏损超限", body_lines)
        r["channel"] = "daily_loss"
        results.append(r)

    for c in stop_checks:
        if c.triggered:
            body_lines = [
                f"策略: {c.strategy_name} (ID={c.strategy_id})",
                f"止损线: -{c.max_loss_usdt} USDT",
                f"当前浮亏: {c.current_unrealized_pnl:.2f} USDT",
                f"状态: 已触发止损，建议立即平仓",
            ]
            r = notifier.send_risk_alert(user_record, f"策略止损触发 - {c.strategy_name}", body_lines)
            r["channel"] = f"stop_loss:{c.strategy_id}"
            results.append(r)

    return {"ok": True, "sent": len(results), "results": results}
