from dataclasses import dataclass
from datetime import datetime, date

from sqlalchemy.orm import Session


@dataclass(slots=True)
class DailyLossStatus:
    date: date
    realized_pnl: float
    realized_pnl_minus_fees: float
    limit: float | None
    exceeded: bool
    blocked_until_utc: datetime | None = None


class DailyLossTracker:

    def __init__(self, db: Session) -> None:
        self.db = db

    def compute_today_realized_pnl(
        self,
        user_id: int,
    ) -> float:
        from models import PositionSnapshot

        rows = (
            self.db.query(PositionSnapshot)
            .filter(
                PositionSnapshot.user_id == user_id,
                PositionSnapshot.snapshot_type.in_(["close", "partial_close"]),
                PositionSnapshot.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
            )
            .all()
        )
        return sum(float(r.realized_pnl or 0) for r in rows)

    def get_limit(self, user_id: int) -> float | None:
        from models import RiskLimitRule

        rule = (
            self.db.query(RiskLimitRule)
            .filter(
                RiskLimitRule.scope_type == "user",
                RiskLimitRule.scope_id == str(user_id),
                RiskLimitRule.limit_type == "daily_loss",
                RiskLimitRule.enabled.is_(True),
            )
            .order_by(RiskLimitRule.priority.desc())
            .first()
        )
        if rule is None:
            rule = (
                self.db.query(RiskLimitRule)
                .filter(
                    RiskLimitRule.scope_type == "platform",
                    RiskLimitRule.limit_type == "daily_loss",
                    RiskLimitRule.enabled.is_(True),
                )
                .first()
            )
        return float(rule.limit_value) if rule else None

    def check(self, user_id: int) -> DailyLossStatus:
        today = date.today()
        realized = self.compute_today_realized_pnl(user_id)
        limit = self.get_limit(user_id)
        exceeded = limit is not None and realized <= -limit
        return DailyLossStatus(date=today, realized_pnl=realized, realized_pnl_minus_fees=realized, limit=limit, exceeded=exceeded)
