from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import ArbitrageTask


@dataclass(slots=True)
class ArbitrageTaskCreate:
    task_uuid: str
    user_id: int
    strategy_config_id: int | None
    opportunity_id: str
    env_mode: str
    task_type: str
    symbol: str
    spot_exchange: str
    derivative_exchange: str
    target_notional: float
    expected_spread_bps: float
    expected_funding_bps: float
    idempotency_key: str
    home_region: str
    buy_account_id: int | None = None
    sell_account_id: int | None = None


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_task(self, data: ArbitrageTaskCreate) -> ArbitrageTask:
        task = ArbitrageTask(**asdict(data))
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def get_by_task_uuid(self, task_uuid: str) -> ArbitrageTask | None:
        return self.session.scalar(
            select(ArbitrageTask).where(ArbitrageTask.task_uuid == task_uuid)
        )

    def mark_dispatched(self, task_uuid: str, *, worker_node_id: str) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = "DISPATCHED"
        task.worker_node_id = worker_node_id
        task.dispatched_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(task)
        return task

    def mark_executing(self, task_uuid: str, *, worker_node_id: str) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = "EXECUTING"
        task.worker_node_id = worker_node_id
        task.started_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(task)
        return task

    def mark_succeeded(self, task_uuid: str) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = "SUCCEEDED"
        task.finished_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(task)
        return task

    def mark_failed(self, task_uuid: str, *, reason: str) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = "FAILED"
        task.status_reason = reason
        task.finished_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(task)
        return task

    def mark_blocked(self, task_uuid: str, *, reason: str) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = "BLOCKED"
        task.status_reason = reason
        task.finished_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(task)
        return task

    def _require_task(self, task_uuid: str) -> ArbitrageTask:
        task = self.get_by_task_uuid(task_uuid)
        if task is None:
            raise LookupError(f"task not found: {task_uuid}")
        return task
