import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

logger = logging.getLogger("uvicorn")


@dataclass
class _UserAlertState:
    daily_exceeded: bool = False
    stop_triggered: set[int] = field(default_factory=set)


class RiskAutoScanner:

    def __init__(self, db_factory) -> None:
        self._db_factory = db_factory
        self._states: dict[int, _UserAlertState] = {}
        self._scan_interval: int = 60

    async def run(self) -> None:
        while True:
            try:
                await self._scan_once()
            except Exception:
                logger.exception("RiskAutoScanner scan error")
            await asyncio.sleep(self._scan_interval)

    def _scan_once(self) -> None:
        from models import User
        from app.risk.daily_loss import DailyLossTracker
        from app.risk.stop_loss import StopLossChecker
        from app.risk.notifier import UserNotifier

        db: Session = self._db_factory()
        try:
            users = db.query(User).filter(User.status == "active").all()
        finally:
            db.close()

        for user in users:
            state = self._states.get(user.id, _UserAlertState())
            db = self._db_factory()
            try:
                daily = DailyLossTracker(db).check(user.id)
                stop_checks = StopLossChecker(db).check(user.id)
                notifier = UserNotifier(db)
            finally:
                db.close()

            new_daily = daily.exceeded
            if new_daily and not state.daily_exceeded:
                body = [
                    f"日期: {daily.date}",
                    f"今日已实现盈亏: {daily.realized_pnl:.2f} USDT",
                    f"亏损上限: {daily.limit} USDT",
                    f"状态: 已超限，新开仓已禁止",
                ]
                notifier.send_risk_alert(user, "日亏损超限", body)
                logger.warning("RiskAutoScanner: daily_loss exceeded for user=%s", user.id)

            state.daily_exceeded = new_daily

            current_triggered: set[int] = set()
            for c in stop_checks:
                if c.triggered:
                    current_triggered.add(c.strategy_id)
                    if c.strategy_id not in state.stop_triggered:
                        body = [
                            f"策略: {c.strategy_name} (ID={c.strategy_id})",
                            f"止损线: -{c.max_loss_usdt} USDT",
                            f"当前浮亏: {c.current_unrealized_pnl:.2f} USDT",
                            f"状态: 已触发止损，建议立即平仓",
                        ]
                        notifier.send_risk_alert(user, f"策略止损触发 - {c.strategy_name}", body)
                        logger.warning("RiskAutoScanner: stop_loss triggered for user=%s strategy=%s", user.id, c.strategy_name)

            state.stop_triggered = current_triggered
            self._states[user.id] = state


_scanner: RiskAutoScanner | None = None


def get_scanner() -> RiskAutoScanner | None:
    return _scanner


def init_scanner(db_factory) -> RiskAutoScanner:
    global _scanner
    _scanner = RiskAutoScanner(db_factory)
    return _scanner
