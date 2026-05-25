# Executor Execution Result Event Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `executor.execution_result` runtime event so executor logs expose real execution status, exchange summary, and richer leg-level probe details without expanding task-table persistence.

**Architecture:** Keep the change inside the existing `RedisExecutionTaskConsumer` path. First write failing worker tests that lock the new event contract for rich results, summary-only results, and preflight non-emission; then add one focused event builder plus one dispatch hook after `dispatcher.dispatch(...)`; finally run focused regressions to confirm execution-summary persistence and existing processed/failed events still behave the same.

**Tech Stack:** Python 3.10+, pytest, pytest-asyncio, Redis Streams worker runtime, `RuntimeEvent`, existing executor summary writeback flow

---

## File Structure

- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - Add a focused `executor.execution_result` event builder
  - Emit the event only when `dispatcher.dispatch(...)` returns a result with `execution_status`
  - Keep existing execution-summary writeback and processed/failed events intact
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - Add red/green tests for richer-result event emission, summary-only compatibility, and preflight non-emission
- Reuse without changes: `d:\old\FuRunSystemV4\app\runtime\runtime_events.py`
  - Keep the current `RuntimeEvent` dataclass as the event envelope
- Reuse without changes: `d:\old\FuRunSystemV4\app\runtime\alerting.py`
  - Keep existing `INFO` routing behavior so the new event stays log-only

## Task 1: Add Failing Worker Tests For `executor.execution_result`

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Add a focused assertion helper for the new event**

Place this helper near the existing `FakeEventRouter` and event-related tests:

```python
def _find_event(events, event_type: str):
    return next(event for event in events if event.event_type == event_type)
```

- [ ] **Step 2: Write the failing rich-result success event test**

Add this test:

```python
@pytest.mark.asyncio
async def test_executor_emits_execution_result_event_for_rich_open_hedged_result():
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
            "ok": True,
            "execution_status": "OPEN_HEDGED",
            "filled_exchanges": ["okx", "gate"],
            "failed_exchanges": [],
            "buy_leg_status": "final_fetched",
            "sell_leg_status": "final_fetched",
            "buy_leg_error_code": None,
            "sell_leg_error_code": None,
            "buy_leg_error_detail": None,
            "sell_leg_error_detail": None,
            "failed_stage": None,
        },
    )()
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    event = _find_event(router.events, "executor.execution_result")
    assert event.service == "executor"
    assert event.region == "node-a"
    assert event.symbol == "BTC/USDT"
    assert event.exchange == "okx"
    assert event.exchanges == ["okx", "gate"]
    assert event.payload == {
        "task_uuid": "task-1",
        "user_id": "42",
        "source_message_id": "src-1",
        "buy_exchange": "okx",
        "sell_exchange": "gate",
        "execution_status": "OPEN_HEDGED",
        "filled_exchanges": ["okx", "gate"],
        "failed_exchanges": [],
        "buy_leg_status": "final_fetched",
        "sell_leg_status": "final_fetched",
        "buy_leg_error_code": None,
        "sell_leg_error_code": None,
        "buy_leg_error_detail": None,
        "sell_leg_error_detail": None,
        "failed_stage": None,
    }
```

- [ ] **Step 3: Write the failing rich-result partial-failure event test**

Add this test:

```python
@pytest.mark.asyncio
async def test_executor_emits_execution_result_event_for_rich_open_partial_result():
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
            "buy_leg_status": "created",
            "sell_leg_status": "create_failed",
            "buy_leg_error_code": None,
            "sell_leg_error_code": "sell_create_failed",
            "buy_leg_error_detail": None,
            "sell_leg_error_detail": "create order failed",
            "failed_stage": "create_sell",
        },
    )()
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    event = _find_event(router.events, "executor.execution_result")
    assert event.payload["execution_status"] == "OPEN_PARTIAL"
    assert event.payload["filled_exchanges"] == ["okx"]
    assert event.payload["failed_exchanges"] == ["gate"]
    assert event.payload["buy_leg_status"] == "created"
    assert event.payload["sell_leg_status"] == "create_failed"
    assert event.payload["sell_leg_error_code"] == "sell_create_failed"
    assert event.payload["sell_leg_error_detail"] == "create order failed"
    assert event.payload["failed_stage"] == "create_sell"
```

- [ ] **Step 4: Write the failing summary-only compatibility test**

Add this test:

```python
@pytest.mark.asyncio
async def test_executor_emits_execution_result_event_for_summary_only_result():
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
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    event = _find_event(router.events, "executor.execution_result")
    assert event.payload["execution_status"] == "OPEN_HEDGED"
    assert event.payload["filled_exchanges"] == ["okx", "gate"]
    assert event.payload["failed_exchanges"] == []
    assert event.payload["buy_leg_status"] is None
    assert event.payload["sell_leg_status"] is None
    assert event.payload["buy_leg_error_code"] is None
    assert event.payload["sell_leg_error_code"] is None
    assert event.payload["failed_stage"] is None
```

- [ ] **Step 5: Write the failing preflight non-emission test**

Add this test:

```python
@pytest.mark.asyncio
async def test_executor_preflight_failure_does_not_emit_execution_result_event():
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
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(FakeSpotService()),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=FakeTaskRepository(task_uuid="task-1"),
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object()},
        max_iterations=1,
    )

    assert processed == 0
    assert all(
        event.event_type != "executor.execution_result" for event in router.events
    )
```

- [ ] **Step 6: Run the targeted worker tests to verify they fail**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "execution_result_event"
```

Expected: FAIL because `RedisExecutionTaskConsumer` does not yet emit `executor.execution_result`.

- [ ] **Step 7: Commit the red test slice only if you intentionally checkpoint red**

```bash
git add tests/test_live_workers.py
git commit -m "test: add executor execution result event regressions"
```

Expected: skip this commit if you do not keep failing-test checkpoints in this repo.

## Task 2: Implement `executor.execution_result` In `RedisExecutionTaskConsumer`

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`

- [ ] **Step 1: Add a focused event builder method**

Inside `RedisExecutionTaskConsumer`, add this method after `__init__` and before `run()`:

```python
    def _build_execution_result_event(self, *, payload: dict, result: object) -> RuntimeEvent:
        buy_exchange = (
            str(payload["buy_exchange"])
            if payload.get("buy_exchange") is not None
            else None
        )
        sell_exchange = (
            str(payload["sell_exchange"])
            if payload.get("sell_exchange") is not None
            else None
        )
        exchanges = [exchange for exchange in (buy_exchange, sell_exchange) if exchange]
        return RuntimeEvent(
            event_type="executor.execution_result",
            level="INFO",
            service="executor",
            region=self.region,
            symbol=str(payload["symbol"]) if payload.get("symbol") is not None else None,
            exchange=buy_exchange,
            exchanges=exchanges or None,
            message="executor execution result recorded",
            payload={
                "task_uuid": (
                    str(payload["task_uuid"])
                    if payload.get("task_uuid") is not None
                    else None
                ),
                "user_id": (
                    str(payload["user_id"])
                    if payload.get("user_id") is not None
                    else None
                ),
                "source_message_id": (
                    str(payload["source_message_id"])
                    if payload.get("source_message_id") is not None
                    else None
                ),
                "buy_exchange": buy_exchange,
                "sell_exchange": sell_exchange,
                "execution_status": getattr(result, "execution_status", None),
                "filled_exchanges": list(getattr(result, "filled_exchanges", []) or []),
                "failed_exchanges": list(getattr(result, "failed_exchanges", []) or []),
                "buy_leg_status": getattr(result, "buy_leg_status", None),
                "sell_leg_status": getattr(result, "sell_leg_status", None),
                "buy_leg_error_code": getattr(result, "buy_leg_error_code", None),
                "sell_leg_error_code": getattr(result, "sell_leg_error_code", None),
                "buy_leg_error_detail": getattr(result, "buy_leg_error_detail", None),
                "sell_leg_error_detail": getattr(result, "sell_leg_error_detail", None),
                "failed_stage": getattr(result, "failed_stage", None),
            },
        )
```

- [ ] **Step 2: Emit the event immediately after dispatch returns a real execution result**

In `run()`, right after:

```python
result = await self.dispatcher.dispatch(
    effective_payload,
    execution_accounts_by_exchange=execution_accounts_by_exchange,
    credentials_by_exchange=dispatch_credentials_by_exchange,
    proxies_by_exchange=proxies_by_exchange,
)
execution_status = getattr(result, "execution_status", None)
```

insert:

```python
if self.event_router is not None and execution_status is not None:
    await self.event_router.dispatch(
        self._build_execution_result_event(
            payload=effective_payload,
            result=result,
        )
    )
```

Do not move or remove the existing execution-summary writeback block below it.

- [ ] **Step 3: Keep the processed/failed and summary writeback flow unchanged**

After the insertion, make sure this existing pattern still remains:

```python
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
```

No database schema change belongs in this task.

- [ ] **Step 4: Run the targeted event tests to verify they pass**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "execution_result_event"
```

Expected: PASS. The new event should emit for rich and summary-only results, and stay absent on preflight failure.

- [ ] **Step 5: Commit the implementation slice**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: emit executor execution result events"
```

## Task 3: Run Focused Regression And Syntax Checks

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Re-run the existing executor summary regression slice**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "execution_result_open_hedged or execution_result_open_partial or preflight_failure_does_not_write_execution_summary"
```

Expected: PASS. The new event must not regress execution-summary persistence or preflight non-write semantics.

- [ ] **Step 2: Run the broader worker regression file**

Run:

```bash
python -m pytest -q tests/test_live_workers.py
```

Expected: PASS. Existing dispatcher, control-rule, account-truth, and executor event tests stay green.

- [ ] **Step 3: Run syntax checks on the touched modules**

Run:

```bash
python -m py_compile app/runtime/live_workers.py tests/test_live_workers.py
```

Expected: PASS with no output.

- [ ] **Step 4: Check the working tree before handoff**

Run:

```bash
git status --short
```

Expected: show only the intended `live_workers.py` and `test_live_workers.py` changes before the final cleanup commit, or show a clean tree if previous tasks already committed everything.

- [ ] **Step 5: If Step 1-4 exposed a real follow-up fix, commit it**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "test: finalize executor execution result event regressions"
```

Expected: skip this commit if no follow-up fix was needed.
