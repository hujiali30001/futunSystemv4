# Repair Worker Minimal Auto Hedge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first minimal repair execution loop by publishing repair tasks from executor partial results, consuming them in a dedicated repair worker, attempting one auto-hedge order on the failed leg, emitting `repair.task.finished`, and updating task summary status.

**Architecture:** Keep the change minimal and incremental. First add a small Redis repair-task publishing path next to the existing `executor.repair_planned` event so there is a real consumable input; then add a focused `RuntimeRepairExecutionService` plus `RedisRepairTaskConsumer` in the existing runtime worker style; finally wire the new `repair` role into worker config/factory/app and verify repository/task-summary updates with focused regressions.

**Tech Stack:** Python 3.10+, asyncio, pytest, Redis Streams, SQLAlchemy repository pattern, existing `ExchangeAdapter` / session factory runtime services, current worker runtime modules

---

## File Structure

- Create: `d:\old\FuRunSystemV4\app\runtime\repair_execution_service.py`
  - Define `RuntimeRepairResult`
  - Implement `RuntimeRepairExecutionService.run_task(...)` for one failed-leg market repair order
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
  - Add `RepairTaskPublisher`
  - Add `build_repair_task_payload(...)`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - Extend `RedisExecutionTaskConsumer` to publish repair tasks after `executor.repair_planned`
  - Add `RedisRepairTaskConsumer`
  - Add `repair.task.finished` event builder
- Modify: `d:\old\FuRunSystemV4\app\db\task_repository.py`
  - Add a focused repository method for repair result writeback
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_config.py`
  - Add `repair` worker role and resolved repair stream key
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
  - Add repair service field
  - Add `build_repair_worker(...)`
  - Add `repair` role branch in `WorkerApp.run()` and CLI args
- Create: `d:\old\FuRunSystemV4\tests\test_repair_execution_service.py`
  - Lock the minimal repair service success/failure contract
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - Add publish + repair-consumer red/green tests
- Modify: `d:\old\FuRunSystemV4\tests\test_task_repository.py`
  - Add repair result writeback tests
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`
  - Add repair worker wiring and `WorkerApp` role tests

## Task 1: Publish Repair Tasks From Executor Partial Results

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Write the failing executor publish test**

Add this test near the existing `executor.repair_planned` tests:

```python
@pytest.mark.asyncio
async def test_executor_publishes_repair_task_for_open_partial_result():
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
                            "source_message_id": "src-1",
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
        repair_task_publisher=RepairTaskPublisher(redis_client),
        block_ms=1,
        event_router=FakeEventRouter(),
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert redis_client.xadds[-1][0] == "stream:repair_tasks:node-a"
    assert redis_client.xadds[-1][1] == {
        "task_uuid": "task-1",
        "user_id": "42",
        "symbol": "BTC/USDT",
        "buy_exchange": "okx",
        "sell_exchange": "gate",
        "execution_status": "OPEN_PARTIAL",
        "failed_exchanges": "gate",
        "repair_action": "AUTO_HEDGE_REPAIRING",
        "repair_reason": "one_leg_failed",
        "target_exchanges": "gate",
        "target_quote_amount": "40.0",
    }
```

- [ ] **Step 2: Run the targeted test to verify red**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "publishes_repair_task_for_open_partial_result"
```

Expected: FAIL because `RepairTaskPublisher`, `build_repair_task_payload(...)`, or `repair_task_publisher` wiring does not exist yet.

- [ ] **Step 3: Add the minimal repair-task publisher and payload builder**

Update `app/runtime/redis_flow.py` with this code after `NodeExecutionTaskPublisher`:

```python
class RepairTaskPublisher:
    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    async def publish(self, *, node_id: str, task_payload: dict[str, str]) -> str:
        return await self.redis_client.xadd(
            f"stream:repair_tasks:{node_id}",
            task_payload,
        )


def build_repair_task_payload(
    payload: dict[str, object],
    *,
    execution_status: str,
    failed_exchanges: list[str],
    repair_action: str,
    repair_reason: str,
    target_exchanges: list[str],
) -> dict[str, str]:
    return {
        "task_uuid": str(payload["task_uuid"]),
        "user_id": str(payload["user_id"]),
        "symbol": str(payload["symbol"]),
        "buy_exchange": str(payload["buy_exchange"]),
        "sell_exchange": str(payload["sell_exchange"]),
        "execution_status": execution_status,
        "failed_exchanges": ",".join(failed_exchanges),
        "repair_action": repair_action,
        "repair_reason": repair_reason,
        "target_exchanges": ",".join(target_exchanges),
        "target_quote_amount": str(payload.get("target_quote_amount", "15.0")),
    }
```

Then update `app/runtime/live_workers.py` imports:

```python
from app.runtime.redis_flow import (
    build_node_execution_task_payload,
    build_repair_task_payload,
)
```

And extend `RedisExecutionTaskConsumer.__init__` plus the `OPEN_PARTIAL` branch:

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
        repair_task_publisher=None,
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
        self.repair_task_publisher = repair_task_publisher
        self.env_mode = env_mode
```

```python
                            target_exchanges = list(failed_exchanges)
                            if (
                                self.repair_task_publisher is not None
                                and execution_status == "OPEN_PARTIAL"
                                and failed_exchanges
                                and repair_plan.action != "NONE"
                            ):
                                await self.repair_task_publisher.publish(
                                    node_id=self.region,
                                    task_payload=build_repair_task_payload(
                                        effective_payload,
                                        execution_status=execution_status,
                                        failed_exchanges=failed_exchanges,
                                        repair_action=repair_plan.action,
                                        repair_reason=repair_plan.reason,
                                        target_exchanges=target_exchanges,
                                    ),
                                )
```
```

- [ ] **Step 4: Re-run the targeted test to verify green**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "publishes_repair_task_for_open_partial_result"
```

Expected: PASS.

- [ ] **Step 5: Run the nearby `repair_planned` regression slice**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "repair_planned or publishes_repair_task_for_open_partial_result"
```

Expected: PASS and existing `executor.repair_planned` assertions remain green.

- [ ] **Step 6: Commit the publish slice**

```bash
git add app/runtime/redis_flow.py app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: publish repair tasks for partial execution results"
```

## Task 2: Add The Minimal Repair Execution Service

**Files:**
- Create: `d:\old\FuRunSystemV4\app\runtime\repair_execution_service.py`
- Create: `d:\old\FuRunSystemV4\tests\test_repair_execution_service.py`

- [ ] **Step 1: Write the failing repair service tests**

Create `tests/test_repair_execution_service.py`:

```python
import pytest

from app.runtime.repair_execution_service import (
    RuntimeRepairExecutionService,
    RuntimeRepairResult,
)


class FakeClient:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.markets = {"BTC/USDT": {"limits": {"amount": {"min": 0.0001}}}}
        self.orders = []

    async def fetch_ticker(self, symbol: str) -> dict:
        return {"symbol": symbol, "bid": 100.0, "ask": 101.0, "last": 100.5}

    async def create_order(self, symbol, order_type, side, amount, price, params):
        if self.should_fail:
            raise RuntimeError("repair order failed")
        self.orders.append((symbol, order_type, side, amount, price, params))
        return {"id": "repair-1", "symbol": symbol, "status": "closed"}

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        _ = symbol
        return amount


class FakeSession:
    def __init__(self, client) -> None:
        self.client = client
        self.markets = client.markets

    async def mark_ready(self) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, client) -> None:
        self.client = client

    def create_session(self, *, exchange, env_mode, proxies, credentials):
        _ = exchange, env_mode, proxies, credentials
        return FakeSession(self.client)


@pytest.mark.asyncio
async def test_runtime_repair_execution_service_returns_repaired_for_successful_order():
    service = RuntimeRepairExecutionService(
        session_factory=FakeSessionFactory(FakeClient())
    )

    result = await service.run_task(
        task_uuid="task-1",
        symbol="BTC/USDT",
        buy_exchange="okx",
        sell_exchange="gate",
        target_exchanges=["gate"],
        credentials_by_exchange={"gate": object()},
        target_quote_amount=40.0,
        env_mode="testnet",
    )

    assert result == RuntimeRepairResult(
        ok=True,
        status="REPAIRED",
        task_uuid="task-1",
        target_exchanges=["gate"],
        repaired_exchanges=["gate"],
        remaining_failed_exchanges=[],
        reason=None,
    )


@pytest.mark.asyncio
async def test_runtime_repair_execution_service_returns_manual_required_when_order_fails():
    service = RuntimeRepairExecutionService(
        session_factory=FakeSessionFactory(FakeClient(should_fail=True))
    )

    result = await service.run_task(
        task_uuid="task-1",
        symbol="BTC/USDT",
        buy_exchange="okx",
        sell_exchange="gate",
        target_exchanges=["gate"],
        credentials_by_exchange={"gate": object()},
        target_quote_amount=40.0,
        env_mode="testnet",
    )

    assert result == RuntimeRepairResult(
        ok=False,
        status="MANUAL_REQUIRED",
        task_uuid="task-1",
        target_exchanges=["gate"],
        repaired_exchanges=[],
        remaining_failed_exchanges=["gate"],
        reason="repair order failed",
    )
```

- [ ] **Step 2: Run the new tests to verify red**

Run:

```bash
python -m pytest -q tests/test_repair_execution_service.py
```

Expected: FAIL with `ModuleNotFoundError` because `app.runtime.repair_execution_service` does not exist yet.

- [ ] **Step 3: Implement the minimal repair execution service**

Create `app/runtime/repair_execution_service.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.exchanges.adapters import ExchangeAdapter, OrderRequest
from app.exchanges.session_manager import ExchangeClientFactory, ExchangeCredentials


@dataclass(slots=True)
class RuntimeRepairResult:
    ok: bool
    status: str
    task_uuid: str
    target_exchanges: list[str]
    repaired_exchanges: list[str]
    remaining_failed_exchanges: list[str]
    reason: str | None = None


class RuntimeRepairExecutionService:
    def __init__(self, session_factory: ExchangeClientFactory | None = None) -> None:
        self.session_factory = session_factory or ExchangeClientFactory()

    async def run_task(
        self,
        *,
        task_uuid: str,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        target_exchanges: list[str],
        credentials_by_exchange: dict[str, ExchangeCredentials],
        target_quote_amount: float = 15.0,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> RuntimeRepairResult:
        target_exchange = target_exchanges[0]
        side = "buy" if target_exchange == buy_exchange else "sell"
        session = self.session_factory.create_session(
            exchange=target_exchange,
            env_mode=env_mode,
            proxies=(proxies_by_exchange or {}).get(target_exchange, {}),
            credentials=credentials_by_exchange[target_exchange],
        )
        await session.mark_ready()
        adapter = ExchangeAdapter(session)
        try:
            ticker = await adapter.fetch_ticker(symbol)
            reference_price = (
                ticker.get("last") or ticker.get("ask") or ticker.get("bid") or 1.0
            )
            amount = adapter.amount_to_precision(
                symbol,
                max(
                    session.markets[symbol]["limits"]["amount"]["min"],
                    float(target_quote_amount) / float(reference_price),
                ),
            )
            await adapter.create_order(
                OrderRequest(
                    symbol=symbol,
                    side=side,
                    order_type="market",
                    amount=float(amount),
                    price=None,
                )
            )
            return RuntimeRepairResult(
                ok=True,
                status="REPAIRED",
                task_uuid=task_uuid,
                target_exchanges=list(target_exchanges),
                repaired_exchanges=[target_exchange],
                remaining_failed_exchanges=[],
                reason=None,
            )
        except Exception as exc:
            return RuntimeRepairResult(
                ok=False,
                status="MANUAL_REQUIRED",
                task_uuid=task_uuid,
                target_exchanges=list(target_exchanges),
                repaired_exchanges=[],
                remaining_failed_exchanges=[target_exchange],
                reason=str(exc),
            )
        finally:
            await adapter.close()
```

- [ ] **Step 4: Re-run the new tests to verify green**

Run:

```bash
python -m pytest -q tests/test_repair_execution_service.py
```

Expected: PASS.

- [ ] **Step 5: Commit the service slice**

```bash
git add app/runtime/repair_execution_service.py tests/test_repair_execution_service.py
git commit -m "feat: add minimal repair execution service"
```

## Task 3: Add The Repair Consumer, Result Event, And Task Summary Writeback

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\db\task_repository.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_task_repository.py`

- [ ] **Step 1: Write the failing repair-consumer tests**

Add these tests to `tests/test_live_workers.py`:

```python
@pytest.mark.asyncio
async def test_repair_worker_emits_finished_event_and_marks_task_succeeded_for_successful_repair():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:repair_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "task_uuid": "task-1",
                            "user_id": "42",
                            "symbol": "BTC/USDT",
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "execution_status": "OPEN_PARTIAL",
                            "failed_exchanges": "gate",
                            "repair_action": "AUTO_HEDGE_REPAIRING",
                            "repair_reason": "one_leg_failed",
                            "target_exchanges": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    router = FakeEventRouter()
    repair_service = FakeRepairExecutionService(
        result=type(
            "RepairResult",
            (),
            {
                "ok": True,
                "status": "REPAIRED",
                "task_uuid": "task-1",
                "target_exchanges": ["gate"],
                "repaired_exchanges": ["gate"],
                "remaining_failed_exchanges": [],
                "reason": None,
            },
        )()
    )
    consumer = RedisRepairTaskConsumer(
        redis_client=redis_client,
        repair_service=repair_service,
        stream_key="stream:repair_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    event = _find_event(router.events, "repair.task.finished")
    assert event.payload["status"] == "REPAIRED"
    assert repository.repair_results[-1]["lifecycle_status"] == "SUCCEEDED"
    assert repository.repair_results[-1]["execution_status"] == "OPEN_HEDGED"


@pytest.mark.asyncio
async def test_repair_worker_marks_manual_required_when_repair_fails():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:repair_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "task_uuid": "task-1",
                            "user_id": "42",
                            "symbol": "BTC/USDT",
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "execution_status": "OPEN_PARTIAL",
                            "failed_exchanges": "gate",
                            "repair_action": "AUTO_HEDGE_REPAIRING",
                            "repair_reason": "one_leg_failed",
                            "target_exchanges": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    router = FakeEventRouter()
    repair_service = FakeRepairExecutionService(
        result=type(
            "RepairResult",
            (),
            {
                "ok": False,
                "status": "MANUAL_REQUIRED",
                "task_uuid": "task-1",
                "target_exchanges": ["gate"],
                "repaired_exchanges": [],
                "remaining_failed_exchanges": ["gate"],
                "reason": "repair order failed",
            },
        )()
    )
    consumer = RedisRepairTaskConsumer(
        redis_client=redis_client,
        repair_service=repair_service,
        stream_key="stream:repair_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    event = _find_event(router.events, "repair.task.finished")
    assert event.payload["status"] == "MANUAL_REQUIRED"
    assert repository.repair_results[-1]["lifecycle_status"] == "FAILED"
    assert repository.repair_results[-1]["status_reason"] == "manual_required"
```

Also extend `FakeTaskRepository` in the test file with a `repair_results` list and `mark_repair_result(...)`.

- [ ] **Step 2: Run the targeted tests to verify red**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "repair_worker_emits_finished_event or repair_worker_marks_manual_required"
```

Expected: FAIL because `RedisRepairTaskConsumer`, `repair.task.finished`, and repository writeback do not exist yet.

- [ ] **Step 3: Add the repository writeback method**

Update `app/db/task_repository.py`:

```python
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
        task.finished_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(task)
        return task
```

Add a repository test to `tests/test_task_repository.py`:

```python
def test_mark_repair_result_marks_manual_required_summary(session):
    repository = TaskRepository(session)
    task = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-1",
            user_id=1,
            strategy_config_id=None,
            opportunity_id="opp-1",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="okx",
            derivative_exchange="gate",
            target_notional=100.0,
            expected_spread_bps=10.0,
            expected_funding_bps=0.0,
            idempotency_key="idem-1",
            home_region="main",
        )
    )

    repository.mark_repair_result(
        task.task_uuid,
        lifecycle_status="FAILED",
        execution_status="OPEN_PARTIAL",
        filled_exchanges=["okx"],
        failed_exchanges=["gate"],
        repair_action="AUTO_HEDGE_REPAIRING",
        repair_reason="one_leg_failed",
        status_reason="manual_required",
    )

    refreshed = repository.get_by_task_uuid(task.task_uuid)
    assert refreshed is not None
    assert refreshed.status == "FAILED"
    assert refreshed.status_reason == "manual_required"
    assert refreshed.execution_status == "OPEN_PARTIAL"
```

- [ ] **Step 4: Implement the repair consumer and finished event**

Add this focused builder to `app/runtime/live_workers.py`:

```python
def _build_repair_finished_event(
    *,
    region: str,
    payload: dict[str, object],
    result: Any,
) -> RuntimeEvent:
    buy_exchange = str(payload["buy_exchange"]) if payload.get("buy_exchange") else None
    sell_exchange = str(payload["sell_exchange"]) if payload.get("sell_exchange") else None
    exchanges = [exchange for exchange in (buy_exchange, sell_exchange) if exchange]
    level = "INFO" if getattr(result, "ok", False) else "ERROR"
    return RuntimeEvent(
        event_type="repair.task.finished",
        level=level,
        service="repair",
        region=region,
        symbol=str(payload["symbol"]) if payload.get("symbol") is not None else None,
        exchange=buy_exchange,
        exchanges=exchanges,
        message="repair task finished",
        payload={
            "task_uuid": str(payload["task_uuid"]) if payload.get("task_uuid") else None,
            "repair_action": str(payload.get("repair_action")) if payload.get("repair_action") else None,
            "repair_reason": str(payload.get("repair_reason")) if payload.get("repair_reason") else None,
            "target_exchanges": list(getattr(result, "target_exchanges", []) or []),
            "repaired_exchanges": list(getattr(result, "repaired_exchanges", []) or []),
            "remaining_failed_exchanges": list(getattr(result, "remaining_failed_exchanges", []) or []),
            "status": getattr(result, "status", None),
            "reason": getattr(result, "reason", None),
        },
    )
```

Then add `RedisRepairTaskConsumer` to the same file:

```python
class RedisRepairTaskConsumer(RedisSpotConsumer):
    processed_event_type = "repair.task.processed"
    processed_event_service = "repair"
    processed_event_message = "repair task processed"
    failed_event_type = "repair.task.failed"
    failed_event_service = "repair"
    failed_event_message = "repair task failed"

    def __init__(
        self,
        *,
        repair_service,
        task_repository=None,
        env_mode: str = "testnet",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.repair_service = repair_service
        self.task_repository = task_repository
        self.env_mode = env_mode

    async def run(
        self,
        *,
        credentials_by_exchange: dict | None = None,
        max_iterations: int | None = None,
    ) -> int:
        iteration = 0
        processed = 0
        while max_iterations is None or iteration < max_iterations:
            entries = await self.redis_client.xread(
                {self.stream_key: self.last_id},
                count=1,
                block=self.block_ms,
            )
            for _, messages in entries:
                for message_id, payload in messages:
                    try:
                        target_exchanges = [
                            item for item in str(payload.get("target_exchanges", "")).split(",") if item
                        ]
                        if (
                            str(payload.get("repair_action", "")) != "AUTO_HEDGE_REPAIRING"
                            or str(payload.get("execution_status", "")) != "OPEN_PARTIAL"
                            or not target_exchanges
                        ):
                            self.last_id = message_id
                            processed += 1
                            continue
                        result = await self.repair_service.run_task(
                            task_uuid=str(payload["task_uuid"]),
                            symbol=str(payload["symbol"]),
                            buy_exchange=str(payload["buy_exchange"]),
                            sell_exchange=str(payload["sell_exchange"]),
                            target_exchanges=target_exchanges,
                            credentials_by_exchange=credentials_by_exchange or {},
                            target_quote_amount=float(payload.get("target_quote_amount", "15.0")),
                            env_mode=self.env_mode,
                        )
                        if self.task_repository is not None:
                            if result.ok:
                                self.task_repository.mark_repair_result(
                                    str(payload["task_uuid"]),
                                    lifecycle_status="SUCCEEDED",
                                    execution_status="OPEN_HEDGED",
                                    filled_exchanges=[
                                        str(payload["buy_exchange"]),
                                        str(payload["sell_exchange"]),
                                    ],
                                    failed_exchanges=[],
                                    repair_action=str(payload["repair_action"]),
                                    repair_reason="repair_succeeded",
                                    status_reason=None,
                                )
                            else:
                                self.task_repository.mark_repair_result(
                                    str(payload["task_uuid"]),
                                    lifecycle_status="FAILED",
                                    execution_status="OPEN_PARTIAL",
                                    filled_exchanges=[
                                        exchange
                                        for exchange in (
                                            str(payload["buy_exchange"]),
                                            str(payload["sell_exchange"]),
                                        )
                                        if exchange not in list(result.remaining_failed_exchanges)
                                    ],
                                    failed_exchanges=list(result.remaining_failed_exchanges),
                                    repair_action=str(payload["repair_action"]),
                                    repair_reason=str(payload["repair_reason"]),
                                    status_reason="manual_required",
                                )
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                _build_repair_finished_event(
                                    region=self.region,
                                    payload=payload,
                                    result=result,
                                )
                            )
                            await self.event_router.dispatch(
                                self._build_processed_event(message_id=message_id, payload=payload)
                            )
                        self.last_id = message_id
                        processed += 1
                    except Exception as exc:
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                self._build_failed_event(
                                    message_id=message_id,
                                    payload=payload,
                                    error=exc,
                                )
                            )
            iteration += 1
        return processed
```

- [ ] **Step 5: Re-run the targeted tests to verify green**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "repair_worker_emits_finished_event or repair_worker_marks_manual_required"
python -m pytest -q tests/test_task_repository.py -k "mark_repair_result"
```

Expected: PASS.

- [ ] **Step 6: Commit the repair consumer slice**

```bash
git add app/runtime/live_workers.py app/db/task_repository.py tests/test_live_workers.py tests/test_task_repository.py
git commit -m "feat: add minimal repair worker consumer"
```

## Task 4: Wire The Repair Worker Into Config And App Startup

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_config.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: Write the failing worker wiring tests**

Add these tests to `tests/test_worker_service.py`:

```python
def test_build_repair_worker_uses_repair_execution_service():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="repair",
            worker_region="node-a",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_repair_worker(redis_client=FakeRedis())

    assert worker.consumer.repair_service is factory.repair_execution_service
    assert worker.consumer.stream_key == "stream:repair_tasks:node-a"


@pytest.mark.asyncio
async def test_worker_app_runs_repair_role(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="repair",
            worker_region="node-a",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert len(factory.repair_worker.calls) == 1
```

- [ ] **Step 2: Run the targeted tests to verify red**

Run:

```bash
python -m pytest -q tests/test_worker_service.py -k "build_repair_worker_uses_repair_execution_service or worker_app_runs_repair_role"
```

Expected: FAIL because `repair` role, `build_repair_worker(...)`, and the factory fake support do not exist yet.

- [ ] **Step 3: Implement worker config and startup wiring**

Update `app/runtime/worker_config.py`:

```python
    worker_role: Literal["scanner", "consumer", "dispatcher", "executor", "repair"] = "scanner"
    repair_stream_key: str | None = None
```

```python
    @property
    def resolved_repair_stream_key(self) -> str:
        return self.repair_stream_key or f"stream:repair_tasks:{self.node_id}"
```

Update `app/runtime/worker_service.py` imports and factory fields:

```python
from app.runtime.repair_execution_service import RuntimeRepairExecutionService
```

```python
    repair_execution_service: RuntimeRepairExecutionService = field(
        default_factory=RuntimeRepairExecutionService
    )
```

Add `build_repair_worker(...)`:

```python
    def build_repair_worker(self, *, redis_client: Redis) -> ConsumerWorker:
        task_repository = None
        if self.settings.database_enabled:
            session_factory = build_session_factory(self.settings.database_url)
            session = session_factory()
            task_repository = TaskRepository(session)
        consumer = RedisRepairTaskConsumer(
            redis_client=redis_client,
            repair_service=self.repair_execution_service,
            stream_key=self.settings.resolved_repair_stream_key,
            task_repository=task_repository,
            env_mode=self.settings.env_mode,
            block_ms=self.settings.consumer_block_ms,
            event_router=self.event_router,
            region=self.settings.worker_region,
        )
        return ConsumerWorker(consumer=consumer)
```

Update `WorkerApp.run()` and CLI args:

```python
            if self.settings.worker_role == "repair":
                worker = factory.build_repair_worker(redis_client=redis_client)
                await worker.run(
                    credentials_by_exchange=credentials_by_exchange,
                    stream_key=self.settings.resolved_repair_stream_key,
                )
                return
```

```python
        choices=["scanner", "consumer", "dispatcher", "executor", "repair"],
```

Also extend the `FakeFactory` test helper with a `repair_worker`.

- [ ] **Step 4: Re-run the targeted tests to verify green**

Run:

```bash
python -m pytest -q tests/test_worker_service.py -k "build_repair_worker_uses_repair_execution_service or worker_app_runs_repair_role"
```

Expected: PASS.

- [ ] **Step 5: Run focused cross-file regressions**

Run:

```bash
python -m pytest -q tests/test_repair_execution_service.py tests/test_live_workers.py -k "repair"
python -m pytest -q tests/test_worker_service.py -k "repair or executor"
python -m py_compile app/runtime/repair_execution_service.py app/runtime/redis_flow.py app/runtime/live_workers.py app/runtime/worker_config.py app/runtime/worker_service.py tests/test_repair_execution_service.py tests/test_live_workers.py tests/test_worker_service.py tests/test_task_repository.py
```

Expected: PASS with no syntax errors.

- [ ] **Step 6: Commit the wiring slice**

```bash
git add app/runtime/worker_config.py app/runtime/worker_service.py tests/test_worker_service.py
git commit -m "feat: wire minimal repair worker role"
```

## Task 5: Final Regression And Working Tree Check

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\repair_execution_service.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\db\task_repository.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_config.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_repair_execution_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_task_repository.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: Run the broad focused regression set**

Run:

```bash
python -m pytest -q tests/test_repair_execution_service.py tests/test_live_workers.py tests/test_task_repository.py tests/test_worker_service.py
```

Expected: PASS.

- [ ] **Step 2: Check the working tree**

Run:

```bash
git status --short
git log -4 --oneline
```

Expected: either a clean tree after the planned commits, or only the intended files if a follow-up fix is still pending.

- [ ] **Step 3: If a real follow-up fix was needed, commit it**

```bash
git add app/runtime/repair_execution_service.py app/runtime/redis_flow.py app/runtime/live_workers.py app/db/task_repository.py app/runtime/worker_config.py app/runtime/worker_service.py tests/test_repair_execution_service.py tests/test_live_workers.py tests/test_task_repository.py tests/test_worker_service.py
git commit -m "test: finalize minimal repair worker regressions"
```

Expected: skip this commit if no follow-up fix was needed.
