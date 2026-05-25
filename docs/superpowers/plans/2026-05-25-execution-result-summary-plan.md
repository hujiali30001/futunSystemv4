# Execution Result Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist structured execution summaries onto `arbitrage_tasks` so each task records lifecycle status, execution result status, successful exchanges, failed exchanges, and repair guidance.

**Architecture:** Keep the implementation on the current runtime path instead of introducing a new attempt table. First extend `SpotArbitrageTaskResult` so the real execution service can distinguish `OPEN_HEDGED` from `OPEN_PARTIAL`; then extend `ArbitrageTask` plus `TaskRepository` with execution-summary fields and a dedicated write method; finally wire `RedisExecutionTaskConsumer` to persist those summaries only when a real execution result exists, leaving preflight and dispatch-front failures unchanged.

**Tech Stack:** Python 3.10+, SQLAlchemy ORM, pytest, pytest-asyncio, Redis Streams worker flow, existing spot arbitrage runtime service, task repository

---

## File Structure

- Modify: `d:\old\FuRunSystemV4\app\runtime\spot_arbitrage_probe.py`
  - Extend `SpotArbitrageTaskResult` with `execution_status`, `filled_exchanges`, and `failed_exchanges`
- Modify: `d:\old\FuRunSystemV4\tests\test_spot_arbitrage_probe.py`
  - Add runtime-result tests for `OPEN_HEDGED` and `OPEN_PARTIAL`
- Modify: `d:\old\FuRunSystemV4\models.py`
  - Add task-level execution summary columns
- Modify: `d:\old\FuRunSystemV4\app\db\task_repository.py`
  - Add `mark_execution_result(...)`
- Modify: `d:\old\FuRunSystemV4\tests\test_models.py`
  - Assert new task columns exist
- Modify: `d:\old\FuRunSystemV4\tests\test_task_repository.py`
  - Add summary persistence coverage
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - Persist summary writebacks from executor results
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
  - Inject `RiskManager` into executor worker construction
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - Add executor summary writeback regressions
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`
  - Cover executor worker `RiskManager` wiring
- Modify: `d:\old\FuRunSystemV4\tests\test_trading_engine.py`
  - Add explicit `OPEN_HEDGED` repair-plan regression

## Task 1: Extend The Runtime Execution Result Shape

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_spot_arbitrage_probe.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\spot_arbitrage_probe.py`

- [ ] **Step 1: Write the failing runtime-result tests**

```python
@pytest.mark.asyncio
async def test_spot_arbitrage_probe_returns_open_hedged_summary_when_both_legs_finish():
    service = SpotArbitrageProbeService(session_factory=FakeFactory())
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.execution_status == "OPEN_HEDGED"
    assert result.filled_exchanges == ["bitget", "gate"]
    assert result.failed_exchanges == []


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_returns_open_partial_summary_when_second_leg_create_fails():
    factory = FakeFactory()
    factory.client_configs["gate"]["fail_on_create"] = True
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.ok is False
    assert result.execution_status == "OPEN_PARTIAL"
    assert result.filled_exchanges == ["bitget"]
    assert result.failed_exchanges == ["gate"]
```

- [ ] **Step 2: Run the targeted runtime-result tests to verify they fail**

Run: `pytest -q tests/test_spot_arbitrage_probe.py -k "open_hedged_summary or open_partial_summary"`

Expected: FAIL because `SpotArbitrageTaskResult` does not yet expose `execution_status`, `filled_exchanges`, or `failed_exchanges`.

- [ ] **Step 3: Write the minimal structured runtime result implementation**

Add these fields to `SpotArbitrageTaskResult`:

```python
@dataclass(slots=True)
class SpotArbitrageTaskResult:
    ok: bool
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_order_id: str | None
    sell_order_id: str | None
    buy_final_status: str | None
    sell_final_status: str | None
    message: str
    execution_status: str | None = None
    filled_exchanges: list[str] | None = None
    failed_exchanges: list[str] | None = None
```

At the top of `SpotArbitrageProbeService.run_task()`, initialize execution-summary state:

```python
sessions = {}
adapters = {}
unique_exchanges = list(dict.fromkeys(exchanges))
filled_exchanges: list[str] = []
failed_exchanges: list[str] = []
buy_exchange = ""
sell_exchange = ""
buy_order = None
sell_order = None
```

Replace the two order-creation lines with:

```python
buy_order = await adapters[buy_exchange].create_order(buy_request)
filled_exchanges.append(buy_exchange)
sell_order = await adapters[sell_exchange].create_order(sell_request)
filled_exchanges.append(sell_exchange)
```

Replace the success return with:

```python
return SpotArbitrageTaskResult(
    ok=True,
    symbol=symbol,
    buy_exchange=buy_exchange,
    sell_exchange=sell_exchange,
    buy_order_id=buy_order.get("id"),
    sell_order_id=sell_order.get("id"),
    buy_final_status=buy_final.get("status"),
    sell_final_status=sell_final.get("status"),
    message="spot_arbitrage_task_ok",
    execution_status="OPEN_HEDGED",
    filled_exchanges=filled_exchanges,
    failed_exchanges=[],
)
```

Replace the exception return with:

```python
if buy_exchange and buy_order is not None and buy_exchange not in filled_exchanges:
    filled_exchanges.append(buy_exchange)
if sell_exchange and sell_order is None and sell_exchange not in failed_exchanges:
    failed_exchanges.append(sell_exchange)

return SpotArbitrageTaskResult(
    ok=False,
    symbol=symbol,
    buy_exchange=buy_exchange,
    sell_exchange=sell_exchange,
    buy_order_id=None if buy_order is None else buy_order.get("id"),
    sell_order_id=None if sell_order is None else sell_order.get("id"),
    buy_final_status=None,
    sell_final_status=None,
    message=str(exc),
    execution_status="OPEN_PARTIAL" if filled_exchanges and failed_exchanges else None,
    filled_exchanges=filled_exchanges,
    failed_exchanges=failed_exchanges,
)
```

- [ ] **Step 4: Run the targeted runtime-result tests to verify they pass**

Run: `pytest -q tests/test_spot_arbitrage_probe.py -k "open_hedged_summary or open_partial_summary"`

Expected: PASS with the new runtime-result tests green.

- [ ] **Step 5: Commit the runtime result slice**

```bash
git add tests/test_spot_arbitrage_probe.py app/runtime/spot_arbitrage_probe.py
git commit -m "feat: add structured spot execution summaries"
```

## Task 2: Add Task Summary Fields And Repository Writeback

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_models.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_task_repository.py`
- Modify: `d:\old\FuRunSystemV4\models.py`
- Modify: `d:\old\FuRunSystemV4\app\db\task_repository.py`

- [ ] **Step 1: Write the failing model and repository tests**

```python
def test_strategy_config_and_arbitrage_task_expose_expected_columns():
    strategy_columns = {column.key for column in inspect(StrategyConfig).columns}
    task_columns = {column.key for column in inspect(ArbitrageTask).columns}

    assert {
        "task_uuid",
        "status",
        "status_reason",
        "worker_node_id",
        "execution_status",
        "filled_exchanges_json",
        "failed_exchanges_json",
        "repair_action",
        "repair_reason",
        "dispatched_at",
        "started_at",
        "finished_at",
    } <= task_columns
    assert Base.metadata.tables["strategy_configs"].name == "strategy_configs"


def test_task_repository_marks_execution_result_with_summary_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    task = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-1",
            user_id=42,
            strategy_config_id=None,
            opportunity_id="opp-1",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="okx",
            derivative_exchange="gate",
            target_notional=100.0,
            expected_spread_bps=120.0,
            expected_funding_bps=0.0,
            idempotency_key="idem-1",
            home_region="main",
        )
    )

    repository.mark_execution_result(
        task.task_uuid,
        lifecycle_status="FAILED",
        execution_status="OPEN_PARTIAL",
        filled_exchanges=["okx"],
        failed_exchanges=["gate"],
        repair_action="AUTO_HEDGE_REPAIRING",
        repair_reason="one_leg_failed",
    )

    refreshed = repository.get_by_task_uuid(task.task_uuid)

    assert refreshed is not None
    assert refreshed.status == "FAILED"
    assert refreshed.status_reason is None
    assert refreshed.execution_status == "OPEN_PARTIAL"
    assert refreshed.filled_exchanges_json == ["okx"]
    assert refreshed.failed_exchanges_json == ["gate"]
    assert refreshed.repair_action == "AUTO_HEDGE_REPAIRING"
    assert refreshed.repair_reason == "one_leg_failed"
    assert refreshed.finished_at is not None
```

- [ ] **Step 2: Run the targeted persistence tests to verify they fail**

Run: `pytest -q tests/test_models.py tests/test_task_repository.py -k "execution_result or expected_columns"`

Expected: FAIL because the task model lacks the new columns and `TaskRepository` does not yet expose `mark_execution_result(...)`.

- [ ] **Step 3: Write the minimal task-summary persistence implementation**

Add these fields to `ArbitrageTask` in `models.py`:

```python
status: Mapped[str] = mapped_column(String(32), default="CREATED")
status_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
execution_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
filled_exchanges_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
failed_exchanges_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
repair_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
repair_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
worker_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

Add this exact method to `TaskRepository`:

```python
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
    task.finished_at = datetime.utcnow()
    self.session.commit()
    self.session.refresh(task)
    return task
```

- [ ] **Step 4: Run the targeted persistence tests to verify they pass**

Run: `pytest -q tests/test_models.py tests/test_task_repository.py -k "execution_result or expected_columns"`

Expected: PASS with the new task columns and repository summary write path covered.

- [ ] **Step 5: Commit the task-summary persistence slice**

```bash
git add tests/test_models.py tests/test_task_repository.py models.py app/db/task_repository.py
git commit -m "feat: persist task execution summaries"
```

## Task 3: Wire Executor Summary Writeback

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_trading_engine.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`

- [ ] **Step 1: Write the failing worker and executor regression tests**

```python
def test_risk_manager_returns_none_plan_for_open_hedged():
    manager = RiskManager()
    result = ExecutionResult(
        status="OPEN_HEDGED",
        filled_exchanges=["okx", "gate"],
        failed_exchanges=[],
    )

    plan = manager.build_repair_plan(result)

    assert plan.action == "NONE"
    assert plan.reason == "fully_hedged"


@pytest.mark.asyncio
async def test_executor_marks_execution_result_open_hedged():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_exec_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "task_uuid": "task-1",
                            "user_id": "42",
                            "symbol": "BTC/USDT",
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    service.result = type(
        "ExecutionSummary",
        (),
        {
            "ok": True,
            "execution_status": "OPEN_HEDGED",
            "filled_exchanges": ["okx", "gate"],
            "failed_exchanges": [],
        },
    )()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert repository.execution_results == [
        (
            "task-1",
            {
                "lifecycle_status": "SUCCEEDED",
                "execution_status": "OPEN_HEDGED",
                "filled_exchanges": ["okx", "gate"],
                "failed_exchanges": [],
                "repair_action": "NONE",
                "repair_reason": "fully_hedged",
            },
        )
    ]


@pytest.mark.asyncio
async def test_executor_marks_execution_result_open_partial_with_repair_plan():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_exec_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "task_uuid": "task-1",
                            "user_id": "42",
                            "symbol": "BTC/USDT",
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    service.result = type(
        "ExecutionSummary",
        (),
        {
            "ok": False,
            "execution_status": "OPEN_PARTIAL",
            "filled_exchanges": ["okx"],
            "failed_exchanges": ["gate"],
        },
    )()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert repository.execution_results == [
        (
            "task-1",
            {
                "lifecycle_status": "FAILED",
                "execution_status": "OPEN_PARTIAL",
                "filled_exchanges": ["okx"],
                "failed_exchanges": ["gate"],
                "repair_action": "AUTO_HEDGE_REPAIRING",
                "repair_reason": "one_leg_failed",
            },
        )
    ]


@pytest.mark.asyncio
async def test_executor_preflight_failure_does_not_write_execution_summary():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_exec_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "task_uuid": "task-1",
                            "user_id": "42",
                            "symbol": "BTC/USDT",
                            "buy_exchange": "okx",
                            "sell_exchange": "okx",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(FakeSpotService()),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object()},
        max_iterations=1,
    )

    assert processed == 0
    assert repository.execution_results == []
    assert repository.failed == [("task-1", "executor_preflight_same_exchange")]
```

- [ ] **Step 2: Run the targeted worker regressions to verify they fail**

Run: `pytest -q tests/test_trading_engine.py tests/test_worker_service.py tests/test_live_workers.py -k "open_hedged or open_partial or execution_summary or fully_hedged"`

Expected: FAIL because the fake repository does not yet capture execution summary writes, the consumer still calls `mark_succeeded()` / `mark_failed()` directly after dispatch, and the worker factory does not yet inject a `RiskManager`.

- [ ] **Step 3: Write the minimal executor summary writeback implementation**

Extend `FakeSpotService` in `tests/test_live_workers.py` to support injected results:

```python
class FakeSpotService:
    def __init__(self):
        self.calls = []
        self.result = {"ok": True}

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return self.result
```

Extend `FakeTaskRepository` in `tests/test_live_workers.py` with summary capture:

```python
class FakeTaskRepository:
    def __init__(self, *, task_uuid: str):
        self.task_uuid = task_uuid
        self.generated_task_uuids = [task_uuid]
        self.created = []
        self.dispatched = []
        self.executing = []
        self.succeeded = []
        self.failed = []
        self.blocked = []
        self.execution_results = []

    def mark_execution_result(self, task_uuid: str, **kwargs):
        self.execution_results.append((task_uuid, kwargs))
        return None
```

Add the exact import in `worker_service.py`:

```python
from app.trading.risk_manager import RiskManager
```

Add the new dependency to `RedisExecutionTaskConsumer.__init__()`:

```python
def __init__(
    self,
    *,
    control_guard=None,
    task_repository=None,
    account_repository=None,
    account_truth_resolver=None,
    preflight_validator=None,
    risk_manager=None,
    env_mode: str = "testnet",
    **kwargs,
) -> None:
    super().__init__(**kwargs)
    self.control_guard = control_guard
    self.task_repository = task_repository
    self.account_repository = account_repository
    self.account_truth_resolver = account_truth_resolver
    self.preflight_validator = preflight_validator or ExecutorPreflightValidator()
    self.risk_manager = risk_manager or RiskManager()
    self.env_mode = env_mode
```

Replace the executor success block in `RedisExecutionTaskConsumer.run()` with:

```python
result = await self.dispatcher.dispatch(
    effective_payload,
    execution_accounts_by_exchange=execution_accounts_by_exchange,
    credentials_by_exchange=dispatch_credentials_by_exchange,
    proxies_by_exchange=proxies_by_exchange,
)
execution_status = getattr(result, "execution_status", None)
if (
    task_uuid is not None
    and self.task_repository is not None
    and execution_status is not None
):
    filled_exchanges = list(getattr(result, "filled_exchanges", []) or [])
    failed_exchanges = list(getattr(result, "failed_exchanges", []) or [])
    repair_plan = self.risk_manager.build_repair_plan(
        ExecutionResult(
            status=execution_status,
            filled_exchanges=filled_exchanges,
            failed_exchanges=failed_exchanges,
        )
    )
    lifecycle_status = "SUCCEEDED" if execution_status == "OPEN_HEDGED" else "FAILED"
    self.task_repository.mark_execution_result(
        task_uuid,
        lifecycle_status=lifecycle_status,
        execution_status=execution_status,
        filled_exchanges=filled_exchanges,
        failed_exchanges=failed_exchanges,
        repair_action=repair_plan.action,
        repair_reason=repair_plan.reason,
    )
    self.last_id = message_id
    processed += 1
    if lifecycle_status == "FAILED":
        if self.event_router is not None:
            await self.event_router.dispatch(
                self._build_failed_event(
                    message_id=message_id,
                    payload=payload,
                    error=RuntimeError(execution_status),
                )
            )
        continue
    if self.event_router is not None:
        await self.event_router.dispatch(
            self._build_processed_event(
                message_id=message_id,
                payload=effective_payload,
            )
        )
    continue
```

Inject `RiskManager()` in `DefaultWorkerFactory.build_executor_worker()`:

```python
consumer = RedisExecutionTaskConsumer(
    redis_client=redis_client,
    dispatcher=dispatcher,
    stream_key=self.settings.resolved_executor_stream_key,
    control_guard=control_guard,
    task_repository=task_repository,
    account_repository=account_repository,
    account_truth_resolver=account_truth_resolver,
    risk_manager=RiskManager(),
    env_mode=self.settings.env_mode,
    block_ms=self.settings.consumer_block_ms,
    event_router=self.event_router,
    region=self.settings.worker_region,
)
```

- [ ] **Step 4: Run the targeted worker regressions to verify they pass**

Run: `pytest -q tests/test_trading_engine.py tests/test_worker_service.py tests/test_live_workers.py -k "open_hedged or open_partial or execution_summary or fully_hedged"`

Expected: PASS. `OPEN_HEDGED` should persist a success summary, `OPEN_PARTIAL` should persist a failed summary plus repair guidance, and preflight failures should still skip execution-summary writes.

- [ ] **Step 5: Commit the worker summary slice**

```bash
git add tests/test_trading_engine.py tests/test_worker_service.py tests/test_live_workers.py app/runtime/worker_service.py app/runtime/live_workers.py
git commit -m "feat: write execution summaries to tasks"
```

## Task 4: Run Regression And Finish Validation

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\spot_arbitrage_probe.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\db\task_repository.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_spot_arbitrage_probe.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_task_repository.py`

- [ ] **Step 1: Run the focused regression slice**

Run: `pytest -q tests/test_spot_arbitrage_probe.py tests/test_task_repository.py tests/test_live_workers.py tests/test_worker_service.py tests/test_models.py tests/test_trading_engine.py`

Expected: PASS. This validates runtime result shaping, task persistence, executor writeback, worker wiring, model schema expectations, and repair-plan regressions together.

- [ ] **Step 2: Run a lightweight syntax check on touched modules**

Run: `python -m py_compile app/runtime/spot_arbitrage_probe.py app/runtime/live_workers.py app/runtime/worker_service.py app/db/task_repository.py models.py`

Expected: PASS with no output.

- [ ] **Step 3: Check the working tree before handoff**

Run: `git status --short`

Expected: show only the intended implementation/test changes before the final cleanup commit, or show a clean tree if previous tasks already committed everything.

- [ ] **Step 4: If Task 4 required cleanup edits, commit them**

```bash
git add app/runtime/spot_arbitrage_probe.py app/runtime/live_workers.py app/runtime/worker_service.py app/db/task_repository.py models.py tests/test_spot_arbitrage_probe.py tests/test_task_repository.py tests/test_live_workers.py tests/test_worker_service.py tests/test_models.py tests/test_trading_engine.py
git commit -m "test: finalize execution summary regressions"
```

Expected: only run this commit if the regression pass required a real follow-up fix.
