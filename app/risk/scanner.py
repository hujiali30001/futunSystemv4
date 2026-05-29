import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
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
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="risk-scan")

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                await loop.run_in_executor(self._executor, self._scan_once)
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
                    f"date: {daily.date}",
                    f"realized_pnl: {daily.realized_pnl:.2f} USDT",
                    f"limit: {daily.limit} USDT",
                    f"status: exceeded, new positions blocked",
                ]
                notifier.send_risk_alert(user, "Daily Loss Exceeded", body)
                logger.warning("RiskAutoScanner: daily_loss exceeded for user=%s", user.id)

            state.daily_exceeded = new_daily

            current_triggered: set[int] = set()
            for c in stop_checks:
                if c.triggered:
                    current_triggered.add(c.strategy_id)
                    if c.strategy_id not in state.stop_triggered:
                        body = [
                            f"strategy: {c.strategy_name} (ID={c.strategy_id})",
                            f"stop_loss: -{c.max_loss_usdt} USDT",
                            f"current_unrealized_pnl: {c.current_unrealized_pnl:.2f} USDT",
                            f"status: stop-loss triggered",
                        ]
                        notifier.send_risk_alert(user, f"Stop Loss - {c.strategy_name}", body)
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
