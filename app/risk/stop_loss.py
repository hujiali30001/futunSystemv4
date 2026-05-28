from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from models import ArbitrageTask, StrategyConfig, PositionSnapshot


@dataclass(slots=True)
class StopLossCheck:
    strategy_id: int
    strategy_name: str
    max_loss_usdt: float
    current_unrealized_pnl: float
    triggered: bool


class StopLossChecker:

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_open_positions_with_stop_loss(
        self, user_id: int
    ) -> list[dict]:
        tasks = (
            self.db.query(ArbitrageTask)
            .filter(
                ArbitrageTask.user_id == user_id,
                ArbitrageTask.status.in_(["RUNNING", "OPEN_HEDGED", "OPEN_PARTIAL"]),
                ArbitrageTask.strategy_config_id.isnot(None),
            )
            .all()
        )

        strategy_ids = {t.strategy_config_id for t in tasks if t.strategy_config_id}
        strategies = {
            s.id: s
            for s in self.db.query(StrategyConfig)
            .filter(StrategyConfig.id.in_(strategy_ids), StrategyConfig.max_loss_usdt.isnot(None))
            .all()
        }

        results = []
        for task in tasks:
            strategy = strategies.get(task.strategy_config_id)
            if strategy is None:
                continue
            snapshots = (
                self.db.query(PositionSnapshot)
                .filter(
                    PositionSnapshot.task_uuid == task.task_uuid,
                    PositionSnapshot.snapshot_type == "open",
                )
                .order_by(PositionSnapshot.id.desc())
                .limit(1)
                .all()
            )
            unrealized = sum(float(s.unrealized_pnl or 0) for s in snapshots)
            results.append({
                "task": task,
                "strategy": strategy,
                "unrealized_pnl": unrealized,
            })
        return results

    def check(self, user_id: int) -> list[StopLossCheck]:
        results = []
        positions = self.get_open_positions_with_stop_loss(user_id)
        for pos in positions:
            strategy = pos["strategy"]
            triggered = pos["unrealized_pnl"] <= -strategy.max_loss_usdt
            results.append(StopLossCheck(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                max_loss_usdt=strategy.max_loss_usdt,
                current_unrealized_pnl=pos["unrealized_pnl"],
                triggered=triggered,
            ))
        return results
