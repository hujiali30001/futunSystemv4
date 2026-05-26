from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import desc, or_, select, update
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

    def list_executable_tasks(
        self,
        *,
        env_mode: str,
        limit: int = 100,
    ) -> list[ArbitrageTask]:
        now = datetime.utcnow()
        return list(
            self.session.scalars(
                select(ArbitrageTask)
                .where(
                    ArbitrageTask.env_mode == env_mode,
                    ArbitrageTask.status.in_(("CREATED", "DISPATCHED")),
                    or_(
                        ArbitrageTask.auto_recovery_status != "COOLDOWN",
                        ArbitrageTask.cooldown_until.is_(None),
                        ArbitrageTask.cooldown_until <= now,
                    ),
                )
                .order_by(ArbitrageTask.id.asc())
                .limit(limit)
            )
        )

    def claim_next_executable_task(
        self,
        *,
        worker_node_id: str,
        env_mode: str,
    ) -> ArbitrageTask | None:
        claimed_at = datetime.utcnow()
        candidate_id = (
            select(ArbitrageTask.id)
            .where(
                ArbitrageTask.env_mode == env_mode,
                ArbitrageTask.status.in_(("CREATED", "DISPATCHED")),
                or_(
                    ArbitrageTask.auto_recovery_status != "COOLDOWN",
                    ArbitrageTask.cooldown_until.is_(None),
                    ArbitrageTask.cooldown_until <= claimed_at,
                ),
            )
            .order_by(ArbitrageTask.id.asc())
            .limit(1)
            .scalar_subquery()
        )
        claimed_id = self.session.execute(
            update(ArbitrageTask)
            .where(
                ArbitrageTask.id == candidate_id,
                ArbitrageTask.status.in_(("CREATED", "DISPATCHED")),
            )
            .values(
                status="RUNNING",
                worker_node_id=worker_node_id,
                started_at=claimed_at,
            )
            .returning(ArbitrageTask.id)
        ).scalar_one_or_none()
        if claimed_id is None:
            self.session.rollback()
            return None
        self.session.commit()
        return self.session.get(ArbitrageTask, claimed_id)

    def find_closeable_task(
        self,
        *,
        user_id: int,
        symbol: str,
        spot_exchange: str,
        derivative_exchange: str,
        env_mode: str,
    ) -> ArbitrageTask | None:
        return self.session.scalar(
            select(ArbitrageTask)
            .where(
                ArbitrageTask.user_id == user_id,
                ArbitrageTask.symbol == symbol,
                ArbitrageTask.spot_exchange == spot_exchange,
                ArbitrageTask.derivative_exchange == derivative_exchange,
                ArbitrageTask.env_mode == env_mode,
                ArbitrageTask.task_type == "open",
            )
            .order_by(desc(ArbitrageTask.id))
            .limit(1)
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
        task.status = "RUNNING"
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
        task.failure_reason = reason
        task.auto_recovery_status = "EXHAUSTED"
        task.cooldown_until = None
        task.finished_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(task)
        return task

    def mark_execution_result(
        self,
        task_uuid: str,
        *,
        lifecycle_status: str,
        execution_status: str,
        filled_exchanges: list[str],
        failed_exchanges: list[str],
        repair_action: str,
        repair_reason: str,
    ) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = lifecycle_status
        task.status_reason = None
        task.execution_status = execution_status
        task.filled_exchanges_json = list(filled_exchanges)
        task.failed_exchanges_json = list(failed_exchanges)
        task.repair_action = repair_action
        task.repair_reason = repair_reason
        task.cooldown_until = None
        task.failure_reason = None
        task.auto_recovery_status = "NONE"
        task.finished_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(task)
        return task

    def mark_repair_result(
        self,
        task_uuid: str,
        *,
        lifecycle_status: str,
        execution_status: str,
        filled_exchanges: list[str],
        failed_exchanges: list[str],
        repair_action: str,
        repair_reason: str,
        status_reason: str | None = None,
    ) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = lifecycle_status
        task.status_reason = status_reason
        task.execution_status = execution_status
        task.filled_exchanges_json = list(filled_exchanges)
        task.failed_exchanges_json = list(failed_exchanges)
        task.repair_action = repair_action
        task.repair_reason = repair_reason
        task.cooldown_until = None
        task.failure_reason = None
        if lifecycle_status == "SUCCEEDED":
            task.auto_recovery_status = "NONE"
        task.finished_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(task)
        return task

    def mark_auto_recovery_retry(
        self,
        task_uuid: str,
        *,
        failure_reason: str,
    ) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = "DISPATCHED"
        task.status_reason = None
        task.retry_count += 1
        task.failure_reason = failure_reason
        task.auto_recovery_status = "RETRY_PENDING"
        task.cooldown_until = None
        task.finished_at = None
        self.session.commit()
        self.session.refresh(task)
        return task

    def mark_auto_recovery_cooldown(
        self,
        task_uuid: str,
        *,
        failure_reason: str,
        cooldown_until: datetime,
    ) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = "DISPATCHED"
        task.status_reason = None
        task.failure_reason = failure_reason
        task.auto_recovery_status = "COOLDOWN"
        task.cooldown_until = cooldown_until
        task.finished_at = None
        self.session.commit()
        self.session.refresh(task)
        return task

    def mark_auto_recovery_exhausted(
        self,
        task_uuid: str,
        *,
        failure_reason: str,
    ) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = "FAILED"
        task.status_reason = "auto_recovery_exhausted"
        task.failure_reason = failure_reason
        task.auto_recovery_status = "EXHAUSTED"
        task.cooldown_until = None
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
