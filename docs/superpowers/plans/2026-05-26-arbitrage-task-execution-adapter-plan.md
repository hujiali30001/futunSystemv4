# Arbitrage Task Execution Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute `OPEN` and `CLOSE` arbitrage task records through a dedicated adapter layer, write execution results back to `ArbitrageTask`, and keep the existing spot executor path unchanged.

**Architecture:** Add a small `ArbitrageExecutionAdapter` that translates `ArbitrageTask.task_type` into the existing `RuntimeTradeExecutionService` inputs. Run it behind a dedicated arbitrage execution consumer/worker that selects executable arbitrage tasks from the database, marks lifecycle transitions, optionally triggers the existing repair service for repairable single-leg failures, and leaves the current `RedisExecutionTaskConsumer` / spot executor stream untouched.

**Tech Stack:** Python 3.10, `asyncio`, `pytest`, SQLAlchemy task repository, existing runtime execution services in `app/runtime`, Redis-backed worker bootstrap in `app/runtime/worker_service.py`.

---

### Task 1: Add Executable Task Selection And Lifecycle Helpers

**Files:**
- Modify: `app/db/task_repository.py`
- Test: `tests/test_task_repository.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_task_repository.py`:

```python
def test_task_repository_lists_dispatchable_arbitrage_tasks_in_created_and_dispatched_states(session):
    repository = TaskRepository(session)
    created = repository.create_task(
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
    repository.mark_dispatched("arb-open-1", worker_node_id="node-a")
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-close-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="2-0",
            env_mode="testnet",
            task_type="close",
            symbol="ETH/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=80.0,
            expected_spread_bps=10.0,
            expected_funding_bps=1.0,
            idempotency_key="42:2-0:close:11",
            home_region="main",
        )
    )
    repository.mark_succeeded("arb-close-1")

    items = repository.list_executable_tasks(env_mode="testnet", limit=10)

    assert [item.task_uuid for item in items] == ["arb-open-1"]
    assert items[0].status == "DISPATCHED"


def test_mark_executing_sets_running_state_and_started_at(session):
    repository = TaskRepository(session)
    repository.create_task(
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

    task = repository.mark_executing("arb-open-1", worker_node_id="node-a")

    assert task.status == "RUNNING"
    assert task.worker_node_id == "node-a"
    assert task.started_at is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_task_repository.py -q
```

Expected:

```text
FAIL tests/test_task_repository.py::test_task_repository_lists_dispatchable_arbitrage_tasks_in_created_and_dispatched_states
FAIL tests/test_task_repository.py::test_mark_executing_sets_running_state_and_started_at
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/db/task_repository.py`:

```python
from sqlalchemy import desc, select


class TaskRepository:
    ...
    def list_executable_tasks(
        self,
        *,
        env_mode: str,
        limit: int = 100,
    ) -> list[ArbitrageTask]:
        return list(
            self.session.scalars(
                select(ArbitrageTask)
                .where(
                    ArbitrageTask.env_mode == env_mode,
                    ArbitrageTask.status.in_(("CREATED", "DISPATCHED")),
                )
                .order_by(ArbitrageTask.id.asc())
                .limit(limit)
            )
        )

    def mark_executing(self, task_uuid: str, *, worker_node_id: str) -> ArbitrageTask:
        task = self._require_task(task_uuid)
        task.status = "RUNNING"
        task.worker_node_id = worker_node_id
        task.started_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(task)
        return task
```

Keep this task intentionally narrow:

- it only selects executable arbitrage tasks by lifecycle state
- it does not introduce retry counters or locking columns yet

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_task_repository.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/db/task_repository.py tests/test_task_repository.py
git commit -m "feat: add executable arbitrage task selection"
```

### Task 2: Add `ArbitrageExecutionAdapter`

**Files:**
- Create: `app/runtime/arbitrage_execution_adapter.py`
- Test: `tests/test_arbitrage_execution_adapter.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_arbitrage_execution_adapter.py`:

```python
import pytest

from app.runtime.arbitrage_execution_adapter import ArbitrageExecutionAdapter
from app.runtime.trade_execution_service import RuntimeExecutionResult


class TradeExecutionServiceStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class TaskRecord:
    def __init__(self, *, task_type: str):
        self.task_uuid = "task-1"
        self.task_type = task_type
        self.symbol = "BTC/USDT"
        self.spot_exchange = "binance"
        self.derivative_exchange = "okx"
        self.target_notional = 100.0


@pytest.mark.asyncio
async def test_adapter_maps_open_task_to_spot_buy_and_derivative_sell():
    service = TradeExecutionServiceStub(
        RuntimeExecutionResult(
            ok=True,
            execution_status="OPEN_HEDGED",
            filled_exchanges=["binance", "okx"],
            failed_exchanges=[],
        )
    )
    adapter = ArbitrageExecutionAdapter(execution_service=service)

    result = await adapter.execute_task(
        task=TaskRecord(task_type="open"),
        credentials_by_exchange={"binance": object(), "okx": object()},
        execution_accounts_by_exchange={"binance": object(), "okx": object()},
        env_mode="testnet",
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert result.execution_status == "OPEN_HEDGED"
    assert service.calls[0]["buy_exchange"] == "binance"
    assert service.calls[0]["sell_exchange"] == "okx"
    assert service.calls[0]["target_quote_amount"] == 100.0


@pytest.mark.asyncio
async def test_adapter_maps_close_task_to_reverse_direction():
    service = TradeExecutionServiceStub(
        RuntimeExecutionResult(
            ok=True,
            execution_status="CLOSE_HEDGED",
            filled_exchanges=["okx", "binance"],
            failed_exchanges=[],
        )
    )
    adapter = ArbitrageExecutionAdapter(execution_service=service)

    result = await adapter.execute_task(
        task=TaskRecord(task_type="close"),
        credentials_by_exchange={"binance": object(), "okx": object()},
        execution_accounts_by_exchange={"binance": object(), "okx": object()},
        env_mode="testnet",
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert result.execution_status == "CLOSE_HEDGED"
    assert service.calls[0]["buy_exchange"] == "okx"
    assert service.calls[0]["sell_exchange"] == "binance"


@pytest.mark.asyncio
async def test_adapter_rejects_unknown_task_type():
    adapter = ArbitrageExecutionAdapter(
        execution_service=TradeExecutionServiceStub(
            RuntimeExecutionResult(
                ok=True,
                execution_status="OPEN_HEDGED",
                filled_exchanges=[],
                failed_exchanges=[],
            )
        )
    )

    with pytest.raises(ValueError, match="unsupported task_type"):
        await adapter.execute_task(
            task=TaskRecord(task_type="rebalance"),
            credentials_by_exchange={"binance": object(), "okx": object()},
            execution_accounts_by_exchange={"binance": object(), "okx": object()},
            env_mode="testnet",
            proxies_by_exchange={"binance": {}, "okx": {}},
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_arbitrage_execution_adapter.py -q
```

Expected:

```text
ERROR tests/test_arbitrage_execution_adapter.py - ModuleNotFoundError: No module named 'app.runtime.arbitrage_execution_adapter'
```

- [ ] **Step 3: Write the minimal implementation**

Create `app/runtime/arbitrage_execution_adapter.py`:

```python
from dataclasses import dataclass

from app.runtime.trade_execution_service import RuntimeExecutionResult, RuntimeTradeExecutionService


@dataclass(slots=True)
class ArbitrageExecutionAdapter:
    execution_service: RuntimeTradeExecutionService

    async def execute_task(
        self,
        *,
        task,
        credentials_by_exchange: dict,
        execution_accounts_by_exchange: dict,
        env_mode: str,
        proxies_by_exchange: dict | None = None,
    ) -> RuntimeExecutionResult:
        if task.task_type == "open":
            buy_exchange = task.spot_exchange
            sell_exchange = task.derivative_exchange
        elif task.task_type == "close":
            buy_exchange = task.derivative_exchange
            sell_exchange = task.spot_exchange
        else:
            raise ValueError(f"unsupported task_type: {task.task_type}")

        return await self.execution_service.run_task(
            exchanges=[buy_exchange, sell_exchange],
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            credentials_by_exchange=credentials_by_exchange,
            execution_accounts_by_exchange=execution_accounts_by_exchange,
            symbol=task.symbol,
            target_quote_amount=float(task.target_notional),
            env_mode=env_mode,
            proxies_by_exchange=proxies_by_exchange,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_arbitrage_execution_adapter.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/arbitrage_execution_adapter.py tests/test_arbitrage_execution_adapter.py
git commit -m "feat: add arbitrage execution adapter"
```

### Task 3: Add Arbitrage Execution Consumer And Minimal Repair Compatibility

**Files:**
- Modify: `app/runtime/live_workers.py`
- Test: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_live_workers.py`:

```python
class ArbitrageExecutionAdapterStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def execute_task(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class RepairServiceStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_marks_open_task_succeeded_after_adapter_success():
    repository = FakeTaskRepository(task_uuid="arb-open-1")
    repository.tasks_by_uuid["arb-open-1"] = type(
        "Task",
        (),
        {
            "task_uuid": "arb-open-1",
            "user_id": 42,
            "task_type": "open",
            "symbol": "BTC/USDT",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "target_notional": 100.0,
        },
    )()
    repository.executable_tasks = [repository.tasks_by_uuid["arb-open-1"]]
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=repository,
        execution_adapter=ArbitrageExecutionAdapterStub(
            RuntimeExecutionResult(
                ok=True,
                execution_status="OPEN_HEDGED",
                filled_exchanges=["binance", "okx"],
                failed_exchanges=[],
            )
        ),
        repair_service=RepairServiceStub(
            RuntimeRepairResult(
                ok=True,
                status="REPAIRED",
                task_uuid="arb-open-1",
                target_exchanges=[],
                repaired_exchanges=[],
                remaining_failed_exchanges=[],
                reason=None,
            )
        ),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(id=11, exchange="binance"),
                    FakeExchangeAccount(id=12, exchange="okx"),
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
    assert repository.executing == [("arb-open-1", "node-a")]
    assert repository.execution_results[0][0] == "arb-open-1"
    assert repository.execution_results[0][1]["lifecycle_status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_runs_minimal_repair_for_repairable_partial_result():
    repository = FakeTaskRepository(task_uuid="arb-open-1")
    repository.tasks_by_uuid["arb-open-1"] = type(
        "Task",
        (),
        {
            "task_uuid": "arb-open-1",
            "user_id": 42,
            "task_type": "open",
            "symbol": "BTC/USDT",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "target_notional": 100.0,
        },
    )()
    repository.executable_tasks = [repository.tasks_by_uuid["arb-open-1"]]
    repair_service = RepairServiceStub(
        RuntimeRepairResult(
            ok=True,
            status="REPAIRED",
            task_uuid="arb-open-1",
            target_exchanges=["okx"],
            repaired_exchanges=["okx"],
            remaining_failed_exchanges=[],
            reason=None,
        )
    )
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=repository,
        execution_adapter=ArbitrageExecutionAdapterStub(
            RuntimeExecutionResult(
                ok=False,
                execution_status="OPEN_PARTIAL",
                filled_exchanges=["binance"],
                failed_exchanges=["okx"],
            )
        ),
        repair_service=repair_service,
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(id=11, exchange="binance"),
                    FakeExchangeAccount(id=12, exchange="okx"),
                ]
            }
        ),
        worker_node_id="node-a",
        env_mode="testnet",
    )

    await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert repair_service.calls[0]["target_exchanges"] == ["okx"]
    assert repository.repair_results[0][1]["lifecycle_status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_marks_close_task_failed_when_result_is_not_repairable():
    repository = FakeTaskRepository(task_uuid="arb-close-1")
    repository.tasks_by_uuid["arb-close-1"] = type(
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
        },
    )()
    repository.executable_tasks = [repository.tasks_by_uuid["arb-close-1"]]
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=repository,
        execution_adapter=ArbitrageExecutionAdapterStub(
            RuntimeExecutionResult(
                ok=False,
                execution_status="FAILED",
                filled_exchanges=[],
                failed_exchanges=["binance", "okx"],
            )
        ),
        repair_service=RepairServiceStub(
            RuntimeRepairResult(
                ok=False,
                status="MANUAL_REQUIRED",
                task_uuid="arb-close-1",
                target_exchanges=[],
                repaired_exchanges=[],
                remaining_failed_exchanges=[],
                reason="unused",
            )
        ),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(id=11, exchange="binance"),
                    FakeExchangeAccount(id=12, exchange="okx"),
                ]
            }
        ),
        worker_node_id="node-a",
        env_mode="testnet",
    )

    await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert repository.execution_results[0][1]["lifecycle_status"] == "FAILED"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_workers.py -q
```

Expected:

```text
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_marks_open_task_succeeded_after_adapter_success
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_runs_minimal_repair_for_repairable_partial_result
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_marks_close_task_failed_when_result_is_not_repairable
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/live_workers.py`:

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
        env_mode: str,
    ) -> None:
        self.task_repository = task_repository
        self.execution_adapter = execution_adapter
        self.repair_service = repair_service
        self.account_repository = account_repository
        self.worker_node_id = worker_node_id
        self.env_mode = env_mode

    def _build_accounts_by_exchange(self, task) -> dict:
        accounts = self.account_repository.list_enabled_accounts(
            user_id=int(task.user_id),
            env_mode=self.env_mode,
        )
        by_exchange = {str(account.exchange): account for account in accounts}
        return {
            task.spot_exchange: by_exchange[str(task.spot_exchange)],
            task.derivative_exchange: by_exchange[str(task.derivative_exchange)],
        }

    def _is_repairable(self, result) -> bool:
        return result.execution_status == "OPEN_PARTIAL" and bool(result.failed_exchanges)

    async def run_once(
        self,
        *,
        credentials_by_exchange: dict,
        proxies_by_exchange: dict | None = None,
    ) -> int:
        tasks = self.task_repository.list_executable_tasks(
            env_mode=self.env_mode,
            limit=1,
        )
        if not tasks:
            return 0
        task = tasks[0]
        self.task_repository.mark_executing(
            task.task_uuid,
            worker_node_id=self.worker_node_id,
        )
        execution_accounts = self._build_accounts_by_exchange(task)
        result = await self.execution_adapter.execute_task(
            task=task,
            credentials_by_exchange=credentials_by_exchange,
            execution_accounts_by_exchange=execution_accounts,
            env_mode=self.env_mode,
            proxies_by_exchange=proxies_by_exchange,
        )
        if result.ok:
            self.task_repository.mark_execution_result(
                task.task_uuid,
                lifecycle_status="SUCCEEDED",
                execution_status=result.execution_status or "SUCCEEDED",
                filled_exchanges=list(result.filled_exchanges),
                failed_exchanges=list(result.failed_exchanges),
                repair_action="NONE",
                repair_reason="",
            )
            return 1
        if self._is_repairable(result):
            repair_result = await self.repair_service.run_task(
                task_uuid=task.task_uuid,
                symbol=task.symbol,
                buy_exchange=task.spot_exchange,
                sell_exchange=task.derivative_exchange,
                target_exchanges=list(result.failed_exchanges),
                credentials_by_exchange=credentials_by_exchange,
                target_quote_amount=float(task.target_notional),
                env_mode=self.env_mode,
                proxies_by_exchange=proxies_by_exchange,
            )
            self.task_repository.mark_repair_result(
                task.task_uuid,
                lifecycle_status="SUCCEEDED" if repair_result.ok else "FAILED",
                execution_status=repair_result.status,
                filled_exchanges=list(result.filled_exchanges) + list(repair_result.repaired_exchanges),
                failed_exchanges=list(repair_result.remaining_failed_exchanges),
                repair_action="EXECUTED",
                repair_reason=repair_result.reason or "",
                status_reason=None if repair_result.ok else repair_result.reason,
            )
            return 1
        self.task_repository.mark_execution_result(
            task.task_uuid,
            lifecycle_status="FAILED",
            execution_status=result.execution_status or "FAILED",
            filled_exchanges=list(result.filled_exchanges),
            failed_exchanges=list(result.failed_exchanges),
            repair_action="NONE",
            repair_reason="",
        )
        return 1
```

Extend the `FakeTaskRepository` test double in `tests/test_live_workers.py` so these tests can observe:

```python
self.tasks_by_uuid = {}
self.executable_tasks = []
self.executing = []
self.execution_results = []
self.repair_results = []

def list_executable_tasks(self, *, env_mode: str, limit: int = 100):
    self.list_executable_calls.append({"env_mode": env_mode, "limit": limit})
    return list(self.executable_tasks[:limit])

def mark_executing(self, task_uuid: str, *, worker_node_id: str):
    self.executing.append((task_uuid, worker_node_id))
    task = self.tasks_by_uuid[task_uuid]
    task.status = "RUNNING"
    return task
```

Keep this task limited to direct database polling:

- no new Redis execution stream for arbitrage tasks yet
- no new alert-specific behavior yet

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_live_workers.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: add arbitrage execution consumer"
```

### Task 4: Wire A Dedicated `arb_executor` Worker Role

**Files:**
- Modify: `app/runtime/worker_config.py`
- Modify: `app/runtime/worker_service.py`
- Test: `tests/test_worker_service.py`
- Test: `tests/test_worker_config.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_worker_service.py`:

```python
@pytest.mark.asyncio
async def test_worker_app_dispatches_arb_executor_role(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="arb_executor",
            spot_exchanges=["okx", "bitget"],
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert len(factory.arb_executor_worker.calls) == 1


@pytest.mark.asyncio
async def test_default_worker_factory_builds_arb_executor_with_runtime_services():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="arb_executor",
            worker_region="main",
            spot_exchanges=["okx", "bitget"],
            database_enabled=False,
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_arbitrage_executor_worker(redis_client=FakeRedis())

    assert worker.consumer.execution_adapter is not None
    assert worker.consumer.repair_service is factory.repair_execution_service
```

Add this test to `tests/test_worker_config.py`:

```python
def test_worker_settings_accept_arb_executor_role():
    settings = WorkerSettings(worker_role="arb_executor")
    assert settings.worker_role == "arb_executor"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_worker_service.py tests/test_worker_config.py -q
```

Expected:

```text
FAIL tests/test_worker_service.py::test_worker_app_dispatches_arb_executor_role
FAIL tests/test_worker_service.py::test_default_worker_factory_builds_arb_executor_with_runtime_services
FAIL tests/test_worker_config.py::test_worker_settings_accept_arb_executor_role
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/worker_config.py`:

```python
worker_role: Literal[
    "scanner",
    "consumer",
    "dispatcher",
    "arb_dispatcher",
    "executor",
    "arb_executor",
    "repair",
] = "scanner"
```

Update `app/runtime/worker_service.py`:

```python
from app.runtime.arbitrage_execution_adapter import ArbitrageExecutionAdapter
from app.runtime.live_workers import ArbitrageExecutionTaskConsumer


class DefaultWorkerFactory:
    ...
    def build_arbitrage_executor_worker(self, *, redis_client: Redis) -> ConsumerWorker:
        task_repository = None
        account_repository = None
        if self.settings.database_enabled:
            session_factory = build_session_factory(self.settings.database_url)
            session = session_factory()
            task_repository = TaskRepository(session)
            account_repository = AccountRepository(session)
        consumer = ArbitrageExecutionTaskConsumer(
            task_repository=task_repository,
            execution_adapter=ArbitrageExecutionAdapter(self.trade_execution_service),
            repair_service=self.repair_execution_service,
            account_repository=account_repository,
            worker_node_id=self.settings.node_id,
            env_mode=self.settings.env_mode,
        )
        return ConsumerWorker(consumer=consumer)
```

Add the new worker role branch:

```python
if self.settings.worker_role == "arb_executor":
    worker = factory.build_arbitrage_executor_worker(redis_client=redis_client)
    await worker.run(
        credentials_by_exchange=credentials_by_exchange,
        stream_key="db:arbitrage_tasks",
    )
    return
```

Update `parse_args()` choices:

```python
choices=[
    "scanner",
    "consumer",
    "dispatcher",
    "arb_dispatcher",
    "executor",
    "arb_executor",
    "repair",
]
```

If `tests/test_worker_service.py` uses `FakeFactory`, extend it with:

```python
self.arb_executor_worker = FakeWorker()

def build_arbitrage_executor_worker(self, **kwargs):
    return self.arb_executor_worker
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_worker_service.py tests/test_worker_config.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/worker_config.py app/runtime/worker_service.py tests/test_worker_service.py tests/test_worker_config.py
git commit -m "feat: wire arbitrage executor worker"
```

### Task 5: Run Focused Regressions

**Files:**
- Review: `docs/superpowers/specs/2026-05-26-arbitrage-task-execution-adapter-design.md`
- Test: `tests/test_arbitrage_execution_adapter.py`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_worker_service.py`
- Test: `tests/test_worker_config.py`
- Test: `tests/test_task_repository.py`
- Test: `tests/test_trade_execution_service.py`
- Test: `tests/test_repair_execution_service.py`

- [ ] **Step 1: Run the focused regression suite**

Run:

```bash
python -m pytest tests/test_arbitrage_execution_adapter.py tests/test_live_workers.py tests/test_worker_service.py tests/test_worker_config.py tests/test_task_repository.py tests/test_trade_execution_service.py tests/test_repair_execution_service.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Verify old spot executor tests still pass**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "RedisExecutionTaskConsumer or executor or repair_planned"
```

Expected:

```text
selected legacy executor tests pass unchanged
```

- [ ] **Step 3: Check git status**

Run:

```bash
git status --short
```

Expected:

```text
working tree clean
```

- [ ] **Step 4: Inspect recent commits**

Run:

```bash
git log --oneline -n 5
```

Expected:

```text
shows the four B1-3 implementation commits at the top, followed by the spec/plan commits
```
