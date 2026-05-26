# Arbitrage Runtime Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `arb.*` runtime events and alert templates so the arbitrage dispatcher, executor, and repair paths are observable without changing business behavior.

**Architecture:** Reuse the existing `RuntimeEvent`, `AlertRouter`, and `StructuredEventLogger` primitives rather than adding a parallel observability stack. Extend `app/runtime/live_workers.py` with arbitrage-specific event builders and emissions, then extend `app/runtime/alerting.py` so `arb.*` failures surface to Feishu while informational events stay in structured logs only.

**Tech Stack:** Python 3.10, `asyncio`, `pytest`, dataclass-based runtime events, existing runtime workers in `app/runtime/live_workers.py`, and alert routing in `app/runtime/alerting.py`.

---

### Task 1: Add Arbitrage Dispatcher Runtime Events

**Files:**
- Modify: `app/runtime/live_workers.py`
- Test: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_live_workers.py` near the existing arbitrage dispatcher coverage:

```python
@pytest.mark.asyncio
async def test_arbitrage_dispatcher_emits_user_discovered_and_task_created_events():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:opportunities",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "spot_exchange": "binance",
                            "derivative_exchange": "okx",
                            "opportunity_type": "OPEN",
                            "open_spread_bps": "25.0",
                            "close_spread_bps": "14.0",
                            "funding_rate": "0.0005",
                            "annualized_bps": "55.0",
                            "redis_member": "binance:okx:BTC/USDT:OPEN:1",
                            "timestamp": "1.0",
                            "source_message_id": "1-0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeArbitrageDispatchRepository(task_uuid="arb-open-1")
    repository.generated_task_uuids = ["arb-open-1"]
    router = FakeEventRouter()
    dispatcher = RedisArbitrageTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_repository=repository,
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0, open_spread_bps_threshold=20.0)]
        ),
        stream_key="stream:opportunities",
        block_ms=0,
        event_router=router,
        region="node-a",
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    discovered = _find_event(router.events, "arb.dispatcher.user_discovered")
    created = _find_event(router.events, "arb.dispatcher.task_created")
    assert discovered.service == "arb_dispatcher"
    assert discovered.region == "node-a"
    assert discovered.payload["user_id"] == "42"
    assert discovered.payload["source_message_id"] == "1-0"
    assert created.payload["task_uuid"] == "arb-open-1"
    assert created.payload["strategy_config_id"] == "11"
    assert created.payload["worker_node_id"] == "node-a"


@pytest.mark.asyncio
async def test_arbitrage_dispatcher_emits_task_skipped_event_for_missing_account_coverage():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:opportunities",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "spot_exchange": "binance",
                            "derivative_exchange": "okx",
                            "opportunity_type": "OPEN",
                            "open_spread_bps": "25.0",
                            "close_spread_bps": "14.0",
                            "funding_rate": "0.0005",
                            "annualized_bps": "55.0",
                            "redis_member": "binance:okx:BTC/USDT:OPEN:1",
                            "timestamp": "1.0",
                            "source_message_id": "1-0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeArbitrageDispatchRepository(task_uuid="arb-open-1")
    router = FakeEventRouter()
    dispatcher = RedisArbitrageTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_repository=repository,
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0, open_spread_bps_threshold=20.0)]
        ),
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(exchange="binance")]}
        ),
        stream_key="stream:opportunities",
        block_ms=0,
        event_router=router,
        region="node-a",
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    skipped = _find_event(router.events, "arb.dispatcher.task_skipped")
    assert skipped.payload["user_id"] == "42"
    assert skipped.payload["symbol"] == "BTC/USDT"
    assert skipped.payload["skip_reason"] == "account_coverage_missing"
    assert repository.created == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "arbitrage_dispatcher_emits_user_discovered or arbitrage_dispatcher_emits_task_skipped"
```

Expected:

```text
FAIL tests/test_live_workers.py::test_arbitrage_dispatcher_emits_user_discovered_and_task_created_events
FAIL tests/test_live_workers.py::test_arbitrage_dispatcher_emits_task_skipped_event_for_missing_account_coverage
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/live_workers.py` in three places.

First, add arbitrage dispatcher event builders near the existing executor/repair event helpers:

```python
def _build_arb_dispatcher_user_discovered_event(
    *,
    region: str,
    payload: dict[str, object],
    user_id: str,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.dispatcher.user_discovered",
        level="INFO",
        service="arb_dispatcher",
        region=region,
        symbol=str(payload["symbol"]) if payload.get("symbol") is not None else None,
        exchange=str(payload["spot_exchange"]) if payload.get("spot_exchange") is not None else None,
        exchanges=[
            str(payload["spot_exchange"]),
            str(payload["derivative_exchange"]),
        ],
        message="arbitrage dispatcher user discovered",
        payload={
            "user_id": str(user_id),
            "symbol": str(payload["symbol"]),
            "opportunity_type": str(payload["opportunity_type"]),
            "spot_exchange": str(payload["spot_exchange"]),
            "derivative_exchange": str(payload["derivative_exchange"]),
            "source_message_id": str(payload["source_message_id"]),
        },
    )


def _build_arb_dispatcher_task_created_event(
    *,
    region: str,
    payload: dict[str, object],
    user_id: str,
    worker_node_id: str,
    task_record,
    strategy,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.dispatcher.task_created",
        level="INFO",
        service="arb_dispatcher",
        region=region,
        symbol=str(payload["symbol"]),
        exchange=str(payload["spot_exchange"]),
        exchanges=[str(payload["spot_exchange"]), str(payload["derivative_exchange"])],
        message="arbitrage dispatcher task created",
        payload={
            "task_uuid": str(task_record.task_uuid),
            "user_id": str(user_id),
            "strategy_config_id": (
                None if strategy is None else str(getattr(strategy, "id", None))
            ),
            "symbol": str(payload["symbol"]),
            "opportunity_type": str(payload["opportunity_type"]),
            "spot_exchange": str(payload["spot_exchange"]),
            "derivative_exchange": str(payload["derivative_exchange"]),
            "worker_node_id": worker_node_id,
        },
    )


def _build_arb_dispatcher_task_skipped_event(
    *,
    region: str,
    payload: dict[str, object],
    user_id: str,
    skip_reason: str,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.dispatcher.task_skipped",
        level="INFO",
        service="arb_dispatcher",
        region=region,
        symbol=str(payload["symbol"]),
        exchange=str(payload["spot_exchange"]),
        exchanges=[str(payload["spot_exchange"]), str(payload["derivative_exchange"])],
        message="arbitrage dispatcher task skipped",
        payload={
            "user_id": str(user_id),
            "symbol": str(payload["symbol"]),
            "opportunity_type": str(payload["opportunity_type"]),
            "skip_reason": skip_reason,
            "source_message_id": str(payload["source_message_id"]),
        },
    )
```

Then extend `RedisArbitrageTaskDispatcher.__init__()` to accept and store `event_router`:

```python
class RedisArbitrageTaskDispatcher:
    def __init__(
        self,
        *,
        redis_client,
        user_ids: list[str],
        route_resolver,
        task_repository=None,
        strategy_repository=None,
        dispatch_user_repository=None,
        account_repository=None,
        stream_key: str,
        block_ms: int = 1000,
        event_router=None,
        region: str = "default",
        env_mode: str = "testnet",
    ) -> None:
        ...
        self.event_router = event_router
```

Finally, emit the events inside `RedisArbitrageTaskDispatcher.run()`:

```python
for user_id in self._resolve_candidate_user_ids():
    node_id = await self.route_resolver.get_user_node(user_id)
    if node_id is None:
        if self.event_router is not None:
            await self.event_router.dispatch(
                _build_arb_dispatcher_task_skipped_event(
                    region=self.region,
                    payload=effective_payload,
                    user_id=user_id,
                    skip_reason="route_unavailable",
                )
            )
        continue
    if self.event_router is not None:
        await self.event_router.dispatch(
            _build_arb_dispatcher_user_discovered_event(
                region=self.region,
                payload=effective_payload,
                user_id=user_id,
            )
        )
    accounts = self._load_user_accounts(user_id=user_id)
    if not self._has_required_account_coverage(payload=effective_payload, accounts=accounts):
        if self.event_router is not None:
            await self.event_router.dispatch(
                _build_arb_dispatcher_task_skipped_event(
                    region=self.region,
                    payload=effective_payload,
                    user_id=user_id,
                    skip_reason="account_coverage_missing",
                )
            )
        continue
    matched_any = False
    for strategy in self._iter_matching_strategies(user_id=user_id, payload=effective_payload):
        matched_any = True
        if str(effective_payload["opportunity_type"]) == "CLOSE":
            closeable = self.task_repository.find_closeable_task(...)
            if closeable is None:
                if self.event_router is not None:
                    await self.event_router.dispatch(
                        _build_arb_dispatcher_task_skipped_event(
                            region=self.region,
                            payload=effective_payload,
                            user_id=user_id,
                            skip_reason="close_context_missing",
                        )
                    )
                continue
        task_record = self._create_arbitrage_task(...)
        if task_record is not None and self.event_router is not None:
            await self.event_router.dispatch(
                _build_arb_dispatcher_task_created_event(
                    region=self.region,
                    payload=effective_payload,
                    user_id=user_id,
                    worker_node_id=node_id,
                    task_record=task_record,
                    strategy=strategy,
                )
            )
    if not matched_any and self.event_router is not None:
        await self.event_router.dispatch(
            _build_arb_dispatcher_task_skipped_event(
                region=self.region,
                payload=effective_payload,
                user_id=user_id,
                skip_reason="threshold_not_matched",
            )
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "arbitrage_dispatcher_emits_user_discovered or arbitrage_dispatcher_emits_task_skipped"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: add arbitrage dispatcher runtime events"
```

### Task 2: Add Arbitrage Executor And Repair Events

**Files:**
- Modify: `app/runtime/live_workers.py`
- Test: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

Add these focused tests to `tests/test_live_workers.py` near the existing `ArbitrageExecutionTaskConsumer` coverage:

```python
@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_emits_execution_result_event_for_success():
    repository = FakeTaskRepository(task_uuid="arb-open-1")
    task = type(
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
    repository.executable_tasks = [task]
    router = FakeEventRouter()
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=repository,
        execution_adapter=ArbitrageExecutionAdapterStub(
            result=type(
                "ExecutionSummary",
                (),
                {
                    "ok": True,
                    "execution_status": "OPEN_HEDGED",
                    "filled_exchanges": ["binance", "okx"],
                    "failed_exchanges": [],
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
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert processed == 1
    event = _find_event(router.events, "arb.executor.execution_result")
    assert event.service == "arb_executor"
    assert event.region == "node-a"
    assert event.payload["task_uuid"] == "arb-open-1"
    assert event.payload["task_type"] == "open"
    assert event.payload["execution_status"] == "OPEN_HEDGED"


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_emits_repair_planned_and_repair_finished_events():
    repository = FakeTaskRepository(task_uuid="arb-open-2")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-open-2",
            "user_id": 42,
            "task_type": "open",
            "symbol": "BTC/USDT",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "target_notional": 100.0,
        },
    )()
    repository.executable_tasks = [task]
    router = FakeEventRouter()
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
                    "ok": True,
                    "status": "REPAIRED",
                    "target_exchanges": ["okx"],
                    "repaired_exchanges": ["okx"],
                    "remaining_failed_exchanges": [],
                    "reason": None,
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
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert processed == 1
    repair_planned = _find_event(router.events, "arb.executor.repair_planned")
    repair_finished = _find_event(router.events, "arb.repair.finished")
    assert repair_planned.payload["repair_action"] == "AUTO_HEDGE_REPAIRING"
    assert repair_finished.level == "INFO"
    assert repair_finished.payload["status"] == "REPAIRED"


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_emits_task_failed_event_for_non_repairable_failure():
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
        },
    )()
    repository.executable_tasks = [task]
    router = FakeEventRouter()
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
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert processed == 1
    failed_event = _find_event(router.events, "arb.executor.task_failed")
    assert failed_event.level == "ERROR"
    assert failed_event.payload["task_uuid"] == "arb-close-1"
    assert failed_event.payload["failed_exchanges"] == ["binance", "okx"]
    assert failed_event.payload["error"] == "FAILED"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "arbitrage_execution_consumer_emits_execution_result_event or arbitrage_execution_consumer_emits_repair_planned or arbitrage_execution_consumer_emits_task_failed"
```

Expected:

```text
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_emits_execution_result_event_for_success
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_emits_repair_planned_and_repair_finished_events
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_emits_task_failed_event_for_non_repairable_failure
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/live_workers.py` to add arbitrage-specific event builders near the spot executor helpers:

```python
def _build_arb_executor_execution_result_event(
    *,
    region: str,
    task,
    result: Any,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.executor.execution_result",
        level="INFO",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage executor execution result",
        payload={
            "task_uuid": str(task.task_uuid),
            "user_id": str(task.user_id),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "spot_exchange": str(task.spot_exchange),
            "derivative_exchange": str(task.derivative_exchange),
            "execution_status": getattr(result, "execution_status", None),
            "filled_exchanges": list(getattr(result, "filled_exchanges", []) or []),
            "failed_exchanges": list(getattr(result, "failed_exchanges", []) or []),
        },
    )


def _build_arb_executor_repair_planned_event(
    *,
    region: str,
    task,
    execution_status: str,
    filled_exchanges: list[str],
    failed_exchanges: list[str],
    repair_plan: RepairPlan,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.executor.repair_planned",
        level="INFO",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage executor repair planned",
        payload={
            "task_uuid": str(task.task_uuid),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "execution_status": execution_status,
            "repair_action": repair_plan.action,
            "repair_reason": repair_plan.reason,
            "target_exchanges": list(failed_exchanges),
        },
    )


def _build_arb_executor_task_failed_event(
    *,
    region: str,
    task,
    result: Any,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.executor.task_failed",
        level="ERROR",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage executor task failed",
        payload={
            "task_uuid": str(task.task_uuid),
            "user_id": str(task.user_id),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "error": str(getattr(result, "execution_status", "FAILED") or "FAILED"),
            "failed_exchanges": list(getattr(result, "failed_exchanges", []) or []),
        },
    )


def _build_arb_repair_finished_event(
    *,
    region: str,
    task,
    result: Any,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.repair.finished",
        level="INFO" if getattr(result, "ok", False) else "ERROR",
        service="arb_repair",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage repair finished",
        payload={
            "task_uuid": str(task.task_uuid),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "status": getattr(result, "status", None),
            "repaired_exchanges": list(getattr(result, "repaired_exchanges", []) or []),
            "remaining_failed_exchanges": list(
                getattr(result, "remaining_failed_exchanges", []) or []
            ),
            "reason": getattr(result, "reason", None),
        },
    )
```

Then extend `ArbitrageExecutionTaskConsumer.__init__()` and `_run_repair()`:

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
    ) -> None:
        ...
        self.event_router = event_router
        self.region = region or worker_node_id
```

```python
async def _run_repair(...):
    ...
    if self.event_router is not None:
        await self.event_router.dispatch(
            _build_arb_executor_repair_planned_event(
                region=self.region,
                task=task,
                execution_status=str(getattr(result, "execution_status", "") or ""),
                filled_exchanges=filled_exchanges,
                failed_exchanges=failed_exchanges,
                repair_plan=repair_plan,
            )
        )
    repair_result = await self.repair_service.run_task(...)
    ...
    if self.event_router is not None:
        await self.event_router.dispatch(
            _build_arb_repair_finished_event(
                region=self.region,
                task=task,
                result=repair_result,
            )
        )
```

Finally, emit arbitrage executor events in `run_once()`:

```python
result = await self.execution_adapter.execute_task(...)
if self.event_router is not None:
    await self.event_router.dispatch(
        _build_arb_executor_execution_result_event(
            region=self.region,
            task=task,
            result=result,
        )
    )
...
if getattr(result, "ok", False):
    self.task_repository.mark_execution_result(...)
    return 1
if execution_status == "OPEN_PARTIAL" and failed_exchanges:
    await self._run_repair(...)
    return 1
if self.event_router is not None:
    await self.event_router.dispatch(
        _build_arb_executor_task_failed_event(
            region=self.region,
            task=task,
            result=result,
        )
    )
self.task_repository.mark_execution_result(...)
return 1
```

Do not change the existing spot `executor.*` and `repair.task.finished` helpers in this task.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "arbitrage_execution_consumer_emits_execution_result_event or arbitrage_execution_consumer_emits_repair_planned or arbitrage_execution_consumer_emits_task_failed"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: add arbitrage executor observability events"
```

### Task 3: Extend Alert Titles, Feishu Text, And Routing Rules

**Files:**
- Modify: `app/runtime/alerting.py`
- Test: `tests/test_alerting.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_alerting.py`:

```python
def test_feishu_notifier_renders_arbitrage_failure_message():
    captured = {}

    def fake_urlopen(request, timeout=5):
        captured["body"] = request.data
        return FakeHttpResponse()

    notifier = FeishuNotifier(
        webhook_url="https://example.test/hook",
        urlopen=fake_urlopen,
    )
    event = RuntimeEvent(
        event_type="arb.executor.task_failed",
        level="ERROR",
        service="arb_executor",
        message="arbitrage executor task failed",
        symbol="BTC/USDT",
        payload={
            "task_uuid": "arb-close-1",
            "task_type": "close",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "failed_exchanges": ["binance", "okx"],
            "error": "FAILED",
        },
    )

    notifier.send_sync(event)
    body = json.loads(captured["body"].decode("utf-8"))

    assert "套利任务失败" in body["content"]["text"]
    assert "交易对：BTC/USDT" in body["content"]["text"]
    assert "任务类型：close" in body["content"]["text"]
    assert "现货交易所：binance" in body["content"]["text"]
    assert "衍生品交易所：okx" in body["content"]["text"]
    assert "原因：FAILED" in body["content"]["text"]


@pytest.mark.asyncio
async def test_alert_router_does_not_send_feishu_for_info_arbitrage_events():
    router = build_router()
    event = RuntimeEvent(
        event_type="arb.dispatcher.task_created",
        level="INFO",
        service="arb_dispatcher",
        message="arbitrage dispatcher task created",
        symbol="BTC/USDT",
        payload={"task_uuid": "arb-open-1"},
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert len(router.feishu_notifier.events) == 0
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
async def test_alert_router_sends_feishu_for_error_arbitrage_repair_event():
    router = build_router()
    event = RuntimeEvent(
        event_type="arb.repair.finished",
        level="ERROR",
        service="arb_repair",
        message="arbitrage repair finished",
        symbol="BTC/USDT",
        payload={
            "task_uuid": "arb-open-2",
            "task_type": "open",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "status": "MANUAL_REQUIRED",
            "remaining_failed_exchanges": ["okx"],
            "reason": "repair order failed",
            "error": "repair order failed",
        },
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert len(router.feishu_notifier.events) == 1
    assert len(router.email_notifier.events) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_alerting.py -q -k "arbitrage_failure_message or info_arbitrage_events or error_arbitrage_repair_event"
```

Expected:

```text
FAIL tests/test_alerting.py::test_feishu_notifier_renders_arbitrage_failure_message
FAIL tests/test_alerting.py::test_alert_router_does_not_send_feishu_for_info_arbitrage_events
FAIL tests/test_alerting.py::test_alert_router_sends_feishu_for_error_arbitrage_repair_event
```

- [ ] **Step 3: Write the minimal implementation**

Update `_event_title_zh()` in `app/runtime/alerting.py`:

```python
mapping = {
    "worker.start_failed": "服务启动失败",
    "worker.started": "服务已启动",
    "worker.stopped": "服务已停止",
    "scanner.iteration.failed": "扫描任务异常",
    "consumer.message.failed": "机会消费异常",
    "opportunity.detected": "检测到套利机会",
    "arb.dispatcher.user_discovered": "套利用户命中",
    "arb.dispatcher.task_created": "套利任务已创建",
    "arb.dispatcher.task_skipped": "套利任务已跳过",
    "arb.executor.execution_result": "套利执行结果",
    "arb.executor.repair_planned": "套利修复已计划",
    "arb.executor.task_failed": "套利任务失败",
    "arb.repair.finished": "套利修复完成",
}
```

Then add an arbitrage-specific Feishu rendering branch in `_render_text()` before the generic `CRITICAL` / fallback cases:

```python
if event.event_type.startswith("arb."):
    payload = event.payload or {}
    failed_exchanges = ",".join(payload.get("failed_exchanges", []) or []) or "-"
    remaining_failed = ",".join(payload.get("remaining_failed_exchanges", []) or []) or "-"
    return "\n".join(
        [
            title,
            f"服务：{event.service}",
            f"交易对：{event.symbol or '-'}",
            f"任务类型：{payload.get('task_type', '-')}",
            f"现货交易所：{payload.get('spot_exchange', '-')}",
            f"衍生品交易所：{payload.get('derivative_exchange', '-')}",
            f"失败交易所：{failed_exchanges}",
            f"剩余失败交易所：{remaining_failed}",
            f"原因：{payload.get('error', payload.get('reason', event.message))}",
        ]
    )
```

Keep the router behavior simple: no new special case is required for `arb.*` `INFO` events because they should follow the existing default logger-only path, while `ERROR` arbitrage events already flow through the existing error-to-Feishu branch. Do not broaden the `INFO` Feishu rule beyond `opportunity.detected`.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_alerting.py -q -k "arbitrage_failure_message or info_arbitrage_events or error_arbitrage_repair_event"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/alerting.py tests/test_alerting.py
git commit -m "feat: add arbitrage alert titles and feishu templates"
```

### Task 4: Run Focused Observability Regressions

**Files:**
- Review: `docs/superpowers/specs/2026-05-26-arbitrage-runtime-observability-design.md`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_alerting.py`

- [ ] **Step 1: Run the arbitrage observability regression suite**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_alerting.py -q -k "arb or arbitrage or executor.execution_result or executor.repair_planned or repair.task.finished"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Re-check that legacy spot alert behavior did not regress**

Run:

```bash
python -m pytest tests/test_alerting.py -q -k "worker.start_failed or opportunity.detected or dedupe"
```

Expected:

```text
selected legacy alerting tests pass unchanged
```

- [ ] **Step 3: Re-check that legacy spot worker events did not regress**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "executor.repair_planned or executor.task.failed or repair.task.finished"
```

Expected:

```text
selected legacy worker event tests pass unchanged
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
shows the three B1-5A implementation commits on top, followed by the B1-5A spec doc and recent B1-4 commits
```
