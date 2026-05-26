# Arbitrage Automatic Failure Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add task-level automatic retry and cooldown recovery so arbitrage execution and repair failures continue through a bounded system-driven recovery path instead of stopping after alerts.

**Architecture:** Extend `ArbitrageTask` with minimal auto-recovery metadata and keep the main lifecycle statuses (`CREATED / DISPATCHED / RUNNING / SUCCEEDED / FAILED`) intact. Implement the recovery truth in `TaskRepository`, then route both non-repairable executor failures and repair failures through one shared decision path inside `ArbitrageExecutionTaskConsumer`, while teaching `claim_next_executable_task()` to skip cooldown tasks until their window expires.

**Tech Stack:** Python 3.10, SQLAlchemy ORM/Core, `pytest`, existing runtime workers in `app/runtime/live_workers.py`, repository logic in `app/db/task_repository.py`, models in `models.py`.

---

### Task 1: Add Auto-Recovery Task Fields And Claim Eligibility

**Files:**
- Modify: `models.py`
- Modify: `app/db/task_repository.py`
- Test: `tests/test_task_repository.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_task_repository.py`:

```python
from datetime import datetime, timedelta


def test_task_repository_sets_default_auto_recovery_fields_on_create():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    task = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-open-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )

    assert task.retry_count == 0
    assert task.max_retry_count == 2
    assert task.cooldown_until is None
    assert task.failure_reason is None
    assert task.auto_recovery_status == "NONE"


def test_claim_next_executable_task_skips_cooldown_tasks_until_due():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-cooldown",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_auto_recovery_cooldown(
        "arb-cooldown",
        failure_reason="temporary_route_failure",
        cooldown_until=datetime.utcnow() + timedelta(minutes=5),
    )

    claimed = repository.claim_next_executable_task(
        worker_node_id="node-a",
        env_mode="testnet",
    )

    assert claimed is None


def test_claim_next_executable_task_can_claim_due_retry_pending_task():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-retry",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_auto_recovery_retry(
        "arb-retry",
        failure_reason="temporary_route_failure",
    )

    claimed = repository.claim_next_executable_task(
        worker_node_id="node-a",
        env_mode="testnet",
    )

    assert claimed is not None
    assert claimed.task_uuid == "arb-retry"
    assert claimed.status == "RUNNING"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_task_repository.py -q -k "default_auto_recovery_fields or skips_cooldown_tasks or due_retry_pending_task"
```

Expected:

```text
FAIL tests/test_task_repository.py::test_task_repository_sets_default_auto_recovery_fields_on_create
FAIL tests/test_task_repository.py::test_claim_next_executable_task_skips_cooldown_tasks_until_due
FAIL tests/test_task_repository.py::test_claim_next_executable_task_can_claim_due_retry_pending_task
```

- [ ] **Step 3: Write the minimal implementation**

Update `models.py` to extend `ArbitrageTask`:

```python
class ArbitrageTask(TimestampMixin, Base):
    __tablename__ = "arbitrage_tasks"
    ...
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retry_count: Mapped[int] = mapped_column(Integer, default=2)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auto_recovery_status: Mapped[str] = mapped_column(String(32), default="NONE")
```

Then update `app/db/task_repository.py` in three places.

First, let `list_executable_tasks()` and `claim_next_executable_task()` understand retry/cooldown eligibility:

```python
from datetime import datetime
from sqlalchemy import and_, desc, or_, select, update


def list_executable_tasks(self, *, env_mode: str, limit: int = 100) -> list[ArbitrageTask]:
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
```

```python
def claim_next_executable_task(self, *, worker_node_id: str, env_mode: str) -> ArbitrageTask | None:
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
    ...
```

Second, reset recovery metadata on success:

```python
def mark_execution_result(...):
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
    ...
```

```python
def mark_repair_result(...):
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
    task.auto_recovery_status = "NONE" if lifecycle_status == "SUCCEEDED" else task.auto_recovery_status
    task.finished_at = datetime.utcnow()
    ...
```

Third, add repository helpers for later tasks:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_task_repository.py -q -k "default_auto_recovery_fields or skips_cooldown_tasks or due_retry_pending_task"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add models.py app/db/task_repository.py tests/test_task_repository.py
git commit -m "feat(task-repository): add arbitrage auto recovery metadata"
```

### Task 2: Add Repository Recovery State Transitions

**Files:**
- Modify: `app/db/task_repository.py`
- Test: `tests/test_task_repository.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_task_repository.py`:

```python
def test_mark_auto_recovery_retry_increments_retry_count_and_requeues_task():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-retry-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_executing("arb-retry-1", worker_node_id="node-a")

    task = repository.mark_auto_recovery_retry(
        "arb-retry-1",
        failure_reason="temporary_route_failure",
    )

    assert task.status == "DISPATCHED"
    assert task.retry_count == 1
    assert task.auto_recovery_status == "RETRY_PENDING"
    assert task.failure_reason == "temporary_route_failure"
    assert task.cooldown_until is None


def test_mark_auto_recovery_cooldown_sets_due_time_without_finishing_task():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-cooldown-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_executing("arb-cooldown-1", worker_node_id="node-a")
    due_at = datetime.utcnow() + timedelta(minutes=3)

    task = repository.mark_auto_recovery_cooldown(
        "arb-cooldown-1",
        failure_reason="retry_limit_reached",
        cooldown_until=due_at,
    )

    assert task.status == "DISPATCHED"
    assert task.auto_recovery_status == "COOLDOWN"
    assert task.failure_reason == "retry_limit_reached"
    assert task.cooldown_until is not None
    assert task.finished_at is None


def test_mark_auto_recovery_exhausted_marks_final_failure():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-exhausted-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_executing("arb-exhausted-1", worker_node_id="node-a")

    task = repository.mark_auto_recovery_exhausted(
        "arb-exhausted-1",
        failure_reason="cooldown_retried_and_failed",
    )

    assert task.status == "FAILED"
    assert task.status_reason == "auto_recovery_exhausted"
    assert task.auto_recovery_status == "EXHAUSTED"
    assert task.failure_reason == "cooldown_retried_and_failed"
    assert task.finished_at is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_task_repository.py -q -k "mark_auto_recovery_retry or mark_auto_recovery_cooldown or mark_auto_recovery_exhausted"
```

Expected:

```text
FAIL tests/test_task_repository.py::test_mark_auto_recovery_retry_increments_retry_count_and_requeues_task
FAIL tests/test_task_repository.py::test_mark_auto_recovery_cooldown_sets_due_time_without_finishing_task
FAIL tests/test_task_repository.py::test_mark_auto_recovery_exhausted_marks_final_failure
```

- [ ] **Step 3: Write the minimal implementation**

If Task 1 already added the repository helpers, keep them and only tighten any fields needed so these tests pass. The final implementations must preserve these exact behaviors:

```python
def mark_auto_recovery_retry(...):
    task.status = "DISPATCHED"
    task.status_reason = None
    task.retry_count += 1
    task.failure_reason = failure_reason
    task.auto_recovery_status = "RETRY_PENDING"
    task.cooldown_until = None
    task.finished_at = None
```

```python
def mark_auto_recovery_cooldown(...):
    task.status = "DISPATCHED"
    task.status_reason = None
    task.failure_reason = failure_reason
    task.auto_recovery_status = "COOLDOWN"
    task.cooldown_until = cooldown_until
    task.finished_at = None
```

```python
def mark_auto_recovery_exhausted(...):
    task.status = "FAILED"
    task.status_reason = "auto_recovery_exhausted"
    task.failure_reason = failure_reason
    task.auto_recovery_status = "EXHAUSTED"
    task.cooldown_until = None
    task.finished_at = datetime.utcnow()
```

Also update `mark_failed()` so unexpected hard failures clear cooldown state and land in a stable terminal result:

```python
def mark_failed(self, task_uuid: str, *, reason: str) -> ArbitrageTask:
    task = self._require_task(task_uuid)
    task.status = "FAILED"
    task.status_reason = reason
    task.failure_reason = reason
    task.auto_recovery_status = "EXHAUSTED"
    task.cooldown_until = None
    task.finished_at = datetime.utcnow()
    ...
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_task_repository.py -q -k "mark_auto_recovery_retry or mark_auto_recovery_cooldown or mark_auto_recovery_exhausted"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/db/task_repository.py tests/test_task_repository.py
git commit -m "feat(task-repository): add arbitrage recovery transitions"
```

### Task 3: Route Executor And Repair Failures Through One Recovery Policy

**Files:**
- Modify: `app/runtime/live_workers.py`
- Test: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

Update `FakeTaskRepository` in `tests/test_live_workers.py` to record the new recovery transition calls:

```python
self.retry_marked = []
self.cooldowns = []
self.exhausted = []

def mark_auto_recovery_retry(self, task_uuid: str, *, failure_reason: str):
    self.retry_marked.append(
        {"task_uuid": task_uuid, "failure_reason": failure_reason}
    )
    return None

def mark_auto_recovery_cooldown(
    self,
    task_uuid: str,
    *,
    failure_reason: str,
    cooldown_until,
):
    self.cooldowns.append(
        {
            "task_uuid": task_uuid,
            "failure_reason": failure_reason,
            "cooldown_until": cooldown_until,
        }
    )
    return None

def mark_auto_recovery_exhausted(self, task_uuid: str, *, failure_reason: str):
    self.exhausted.append(
        {"task_uuid": task_uuid, "failure_reason": failure_reason}
    )
    return None
```

Then add these tests:

```python
@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_marks_retry_pending_for_first_non_repairable_failure():
    repository = FakeTaskRepository(task_uuid="arb-close-1")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-close-1",
            "user_id": 42,
            "task_type": "close",
            "symbol": "BTC/USDT",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "target_notional": 100.0,
            "retry_count": 0,
            "max_retry_count": 2,
            "auto_recovery_status": "NONE",
        },
    )()
    repository.executable_tasks = [task]
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=repository,
        execution_adapter=ArbitrageExecutionAdapterStub(
            result=type(
                "ExecutionSummary",
                (),
                {
                    "ok": False,
                    "execution_status": "FAILED",
                    "filled_exchanges": [],
                    "failed_exchanges": ["binance", "okx"],
                },
            )()
        ),
        repair_service=FakeRepairExecutionService(result=None),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(account_id=11, exchange="binance"),
                    FakeExchangeAccount(account_id=12, exchange="okx"),
                ]
            }
        ),
        worker_node_id="node-a",
        env_mode="testnet",
    )

    processed = await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert processed == 1
    assert repository.retry_marked == [
        {
            "task_uuid": "arb-close-1",
            "failure_reason": "execution_failed_non_repairable",
        }
    ]
    assert repository.cooldowns == []
    assert repository.exhausted == []
    assert repository.execution_results == []


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_marks_cooldown_when_retry_limit_is_reached():
    repository = FakeTaskRepository(task_uuid="arb-close-2")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-close-2",
            "user_id": 42,
            "task_type": "close",
            "symbol": "BTC/USDT",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "target_notional": 100.0,
            "retry_count": 2,
            "max_retry_count": 2,
            "auto_recovery_status": "NONE",
        },
    )()
    repository.executable_tasks = [task]
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=repository,
        execution_adapter=ArbitrageExecutionAdapterStub(
            result=type(
                "ExecutionSummary",
                (),
                {
                    "ok": False,
                    "execution_status": "FAILED",
                    "filled_exchanges": [],
                    "failed_exchanges": ["binance", "okx"],
                },
            )()
        ),
        repair_service=FakeRepairExecutionService(result=None),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(account_id=11, exchange="binance"),
                    FakeExchangeAccount(account_id=12, exchange="okx"),
                ]
            }
        ),
        worker_node_id="node-a",
        env_mode="testnet",
    )

    processed = await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert processed == 1
    assert repository.retry_marked == []
    assert len(repository.cooldowns) == 1
    assert repository.cooldowns[0]["task_uuid"] == "arb-close-2"
    assert repository.cooldowns[0]["failure_reason"] == "execution_failed_non_repairable"
    assert repository.exhausted == []


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_marks_exhausted_after_cooldown_retry_fails():
    repository = FakeTaskRepository(task_uuid="arb-close-3")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-close-3",
            "user_id": 42,
            "task_type": "close",
            "symbol": "BTC/USDT",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "target_notional": 100.0,
            "retry_count": 2,
            "max_retry_count": 2,
            "auto_recovery_status": "COOLDOWN",
        },
    )()
    repository.executable_tasks = [task]
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=repository,
        execution_adapter=ArbitrageExecutionAdapterStub(
            result=type(
                "ExecutionSummary",
                (),
                {
                    "ok": False,
                    "execution_status": "FAILED",
                    "filled_exchanges": [],
                    "failed_exchanges": ["binance", "okx"],
                },
            )()
        ),
        repair_service=FakeRepairExecutionService(result=None),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(account_id=11, exchange="binance"),
                    FakeExchangeAccount(account_id=12, exchange="okx"),
                ]
            }
        ),
        worker_node_id="node-a",
        env_mode="testnet",
    )

    processed = await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert processed == 1
    assert repository.retry_marked == []
    assert repository.cooldowns == []
    assert repository.exhausted == [
        {
            "task_uuid": "arb-close-3",
            "failure_reason": "execution_failed_non_repairable",
        }
    ]


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_routes_failed_repair_into_same_recovery_policy():
    repository = FakeTaskRepository(task_uuid="arb-open-4")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-open-4",
            "user_id": 42,
            "task_type": "open",
            "symbol": "BTC/USDT",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "target_notional": 100.0,
            "retry_count": 0,
            "max_retry_count": 2,
            "auto_recovery_status": "NONE",
        },
    )()
    repository.executable_tasks = [task]
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=repository,
        execution_adapter=ArbitrageExecutionAdapterStub(
            result=type(
                "ExecutionSummary",
                (),
                {
                    "ok": False,
                    "execution_status": "OPEN_PARTIAL",
                    "filled_exchanges": ["binance"],
                    "failed_exchanges": ["okx"],
                },
            )()
        ),
        repair_service=FakeRepairExecutionService(
            result=type(
                "RepairResult",
                (),
                {
                    "ok": False,
                    "status": "MANUAL_REQUIRED",
                    "target_exchanges": ["okx"],
                    "repaired_exchanges": [],
                    "remaining_failed_exchanges": ["okx"],
                    "reason": "repair order failed",
                },
            )()
        ),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(account_id=11, exchange="binance"),
                    FakeExchangeAccount(account_id=12, exchange="okx"),
                ]
            }
        ),
        worker_node_id="node-a",
        env_mode="testnet",
    )

    processed = await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert processed == 1
    assert repository.retry_marked == [
        {
            "task_uuid": "arb-open-4",
            "failure_reason": "repair_failed_manual_required",
        }
    ]
    assert repository.repair_results == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "retry_pending_for_first_non_repairable_failure or marks_cooldown_when_retry_limit_is_reached or marks_exhausted_after_cooldown_retry_fails or routes_failed_repair_into_same_recovery_policy"
```

Expected:

```text
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_marks_retry_pending_for_first_non_repairable_failure
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_marks_cooldown_when_retry_limit_is_reached
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_marks_exhausted_after_cooldown_retry_fails
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_routes_failed_repair_into_same_recovery_policy
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/live_workers.py` by adding a tiny recovery policy object and one shared handler.

First, add these helpers near `ArbitrageExecutionTaskConsumer`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(slots=True)
class ArbitrageAutoRecoveryDecision:
    action: str
    failure_reason: str
    cooldown_until: datetime | None = None


def _decide_arbitrage_auto_recovery(
    *,
    task,
    failure_reason: str,
    cooldown_seconds: int,
) -> ArbitrageAutoRecoveryDecision:
    retry_count = int(getattr(task, "retry_count", 0) or 0)
    max_retry_count = int(getattr(task, "max_retry_count", 2) or 2)
    auto_recovery_status = str(getattr(task, "auto_recovery_status", "NONE") or "NONE")

    if retry_count < max_retry_count:
        return ArbitrageAutoRecoveryDecision(
            action="RETRY_PENDING",
            failure_reason=failure_reason,
        )
    if auto_recovery_status != "COOLDOWN":
        return ArbitrageAutoRecoveryDecision(
            action="COOLDOWN",
            failure_reason=failure_reason,
            cooldown_until=datetime.utcnow() + timedelta(seconds=cooldown_seconds),
        )
    return ArbitrageAutoRecoveryDecision(
        action="EXHAUSTED",
        failure_reason=failure_reason,
    )
```

Then extend `ArbitrageExecutionTaskConsumer.__init__()`:

```python
class ArbitrageExecutionTaskConsumer:
    def __init__(
        self,
        *,
        task_repository,
        execution_adapter,
        repair_service,
        account_repository,
        worker_node_id: str,
        env_mode: str = "testnet",
        risk_manager: RiskManager | None = None,
        event_router=None,
        region: str | None = None,
        auto_recovery_cooldown_seconds: int = 300,
    ) -> None:
        ...
        self.auto_recovery_cooldown_seconds = auto_recovery_cooldown_seconds
```

Add one shared transition method:

```python
def _apply_auto_recovery(self, *, task, failure_reason: str) -> None:
    decision = _decide_arbitrage_auto_recovery(
        task=task,
        failure_reason=failure_reason,
        cooldown_seconds=self.auto_recovery_cooldown_seconds,
    )
    if decision.action == "RETRY_PENDING":
        self.task_repository.mark_auto_recovery_retry(
            str(task.task_uuid),
            failure_reason=decision.failure_reason,
        )
        return
    if decision.action == "COOLDOWN":
        self.task_repository.mark_auto_recovery_cooldown(
            str(task.task_uuid),
            failure_reason=decision.failure_reason,
            cooldown_until=decision.cooldown_until,
        )
        return
    self.task_repository.mark_auto_recovery_exhausted(
        str(task.task_uuid),
        failure_reason=decision.failure_reason,
    )
```

Use it in both failure paths:

```python
if execution_status == "OPEN_PARTIAL" and failed_exchanges:
    await self._run_repair(...)
    return 1

if self.event_router is not None:
    await self.event_router.dispatch(...)
self._apply_auto_recovery(
    task=task,
    failure_reason="execution_failed_non_repairable",
)
return 1
```

```python
remaining_failed_exchanges = list(
    getattr(repair_result, "remaining_failed_exchanges", []) or failed_exchanges
)
if getattr(repair_result, "ok", False):
    self.task_repository.mark_repair_result(...)
    ...
    return

if self.event_router is not None:
    await self.event_router.dispatch(
        _build_arb_repair_finished_event(
            region=self.region,
            task=task,
            result=repair_result,
        )
    )
self._apply_auto_recovery(
    task=task,
    failure_reason="repair_failed_manual_required",
)
```

Do not keep the old `status_reason="manual_required"` write on failed arbitrage repair in this task.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "retry_pending_for_first_non_repairable_failure or marks_cooldown_when_retry_limit_is_reached or marks_exhausted_after_cooldown_retry_fails or routes_failed_repair_into_same_recovery_policy"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat(runtime): add arbitrage auto recovery policy"
```

### Task 4: Run Focused Auto-Recovery Regressions

**Files:**
- Review: `docs/superpowers/specs/2026-05-26-arbitrage-automatic-failure-recovery-design.md`
- Test: `tests/test_task_repository.py`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_alerting.py`

- [ ] **Step 1: Run the B1-5B focused suite**

Run:

```bash
python -m pytest tests/test_task_repository.py tests/test_live_workers.py -q -k "auto_recovery or cooldown or retry_pending or arbitrage_execution_consumer"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Re-check B1-4 claim-safety coverage**

Run:

```bash
python -m pytest tests/test_task_repository.py tests/test_live_workers.py -q -k "claim_next_executable_task or no_claimable_task"
```

Expected:

```text
selected claim-safety tests pass unchanged
```

- [ ] **Step 3: Re-check B1-5A arbitrage observability coverage**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_alerting.py -q -k "arb.dispatcher or arb.executor or arb.repair or arbitrage_failure_message"
```

Expected:

```text
selected arbitrage observability tests pass unchanged
```

- [ ] **Step 4: Check git status**

Run:

```bash
git status --short
```

Expected:

```text
working tree clean
```

- [ ] **Step 5: Inspect recent commits**

Run:

```bash
git log --oneline -n 6
```

Expected:

```text
shows the two B1-5B implementation commits on top, followed by the B1-5B spec/plan commits and recent B1-5A commits
```
