# Executor Repair Planned Event Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `executor.repair_planned` runtime event so `OPEN_PARTIAL` execution results produce a structured repair-planning object without introducing a repair worker or new persistence tables.

**Architecture:** Keep the change inside the existing `RedisExecutionTaskConsumer` path. First write failing worker tests that lock the `executor.repair_planned` contract for `OPEN_PARTIAL`, `OPEN_HEDGED`, and preflight behavior; then add one focused event builder plus one dispatch hook that reuses the existing `repair_plan` computed from `RiskManager`; finally run focused regressions to confirm task summary persistence and `executor.execution_result` behavior stay intact.

**Tech Stack:** Python 3.10+, pytest, pytest-asyncio, Redis Streams worker runtime, `RuntimeEvent`, existing executor summary writeback flow, `RiskManager`

---

## File Structure

- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - Add a focused `executor.repair_planned` event builder
  - Emit the event only for `OPEN_PARTIAL` results with a non-`NONE` repair action
  - Keep execution-summary writeback and `executor.execution_result` behavior intact
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - Add red/green tests for repair-planned emission and non-emission paths
- Reuse without changes: `d:\old\FuRunSystemV4\app\runtime\runtime_events.py`
  - Keep the current `RuntimeEvent` dataclass as the event envelope
- Reuse without changes: `d:\old\FuRunSystemV4\app\trading\risk_manager.py`
  - Keep `RepairPlan` and `RiskManager.build_repair_plan(...)` behavior unchanged

## Task 1: Add Failing Worker Tests For `executor.repair_planned`

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Write the failing `OPEN_PARTIAL` repair event test**

Add this test near the existing executor event tests:

```python
@pytest.mark.asyncio
async def test_executor_emits_repair_planned_event_for_open_partial_result():
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
    event = _find_event(router.events, "executor.repair_planned")
    assert event.service == "executor"
    assert event.region == "node-a"
    assert event.symbol == "BTC/USDT"
    assert event.exchange == "okx"
    assert event.exchanges == ["okx", "gate"]
    assert event.payload == {
        "task_uuid": "task-1",
        "user_id": "42",
        "symbol": "BTC/USDT",
        "buy_exchange": "okx",
        "sell_exchange": "gate",
        "execution_status": "OPEN_PARTIAL",
        "filled_exchanges": ["okx"],
        "failed_exchanges": ["gate"],
        "repair_action": "AUTO_HEDGE_REPAIRING",
        "repair_reason": "one_leg_failed",
        "target_exchanges": ["gate"],
    }
```

- [ ] **Step 2: Write the failing `OPEN_HEDGED` non-emission test**

Add this test:

```python
@pytest.mark.asyncio
async def test_executor_does_not_emit_repair_planned_event_for_open_hedged_result():
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
    assert all(
        event.event_type != "executor.repair_planned" for event in router.events
    )
```

- [ ] **Step 3: Write the failing preflight non-emission test**

Add this test:

```python
@pytest.mark.asyncio
async def test_executor_preflight_failure_does_not_emit_repair_planned_event():
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
        event.event_type != "executor.repair_planned" for event in router.events
    )
```

- [ ] **Step 4: Run the targeted repair-planned tests to verify they fail**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "repair_planned_event"
```

Expected: FAIL because `RedisExecutionTaskConsumer` does not yet emit `executor.repair_planned`.

- [ ] **Step 5: Commit the red test slice only if you intentionally checkpoint red**

```bash
git add tests/test_live_workers.py
git commit -m "test: add executor repair planned event regressions"
```

Expected: skip this commit if you do not keep failing-test checkpoints in this repo.

## Task 2: Implement `executor.repair_planned` In `RedisExecutionTaskConsumer`

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`

- [ ] **Step 1: Add a focused repair-planned event builder**

Inside `RedisExecutionTaskConsumer`, add this method near the existing event builders:

```python
    def _build_repair_planned_event(
        self,
        *,
        payload: dict,
        execution_status: str,
        filled_exchanges: list[str],
        failed_exchanges: list[str],
        repair_plan: RepairPlan,
    ) -> RuntimeEvent:
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
            event_type="executor.repair_planned",
            level="INFO",
            service="executor",
            region=self.region,
            symbol=str(payload["symbol"]) if payload.get("symbol") is not None else None,
            exchange=buy_exchange,
            exchanges=exchanges or None,
            message="executor repair planned",
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
                "symbol": (
                    str(payload["symbol"])
                    if payload.get("symbol") is not None
                    else None
                ),
                "buy_exchange": buy_exchange,
                "sell_exchange": sell_exchange,
                "execution_status": execution_status,
                "filled_exchanges": list(filled_exchanges),
                "failed_exchanges": list(failed_exchanges),
                "repair_action": repair_plan.action,
                "repair_reason": repair_plan.reason,
                "target_exchanges": list(failed_exchanges),
            },
        )
```

- [ ] **Step 2: Emit the repair-planned event in the existing `OPEN_PARTIAL` branch**

In `run()`, after `repair_plan = self.risk_manager.build_repair_plan(...)` and before task summary writeback, insert:

```python
if (
    self.event_router is not None
    and execution_status == "OPEN_PARTIAL"
    and failed_exchanges
    and repair_plan.action != "NONE"
):
    await self.event_router.dispatch(
        self._build_repair_planned_event(
            payload=effective_payload,
            execution_status=execution_status,
            filled_exchanges=filled_exchanges,
            failed_exchanges=failed_exchanges,
            repair_plan=repair_plan,
        )
    )
```

Keep the existing `mark_execution_result(...)` and `executor.execution_result` flow intact below it.

- [ ] **Step 3: Run the targeted repair-planned tests to verify they pass**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "repair_planned_event"
```

Expected: PASS. The new event should emit only for `OPEN_PARTIAL` and stay absent for `OPEN_HEDGED` and preflight failure.

- [ ] **Step 4: Commit the implementation slice**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: emit executor repair planned events"
```

## Task 3: Run Focused Regression And Syntax Checks

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Re-run the executor event and summary regression slice**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "execution_result or write_execution_summary or preflight_failure or repair_planned_event"
```

Expected: PASS. The new repair-planned event must not regress execution-summary persistence or `executor.execution_result`.

- [ ] **Step 2: Re-run the broader worker regression file**

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
git commit -m "test: finalize executor repair planned event regressions"
```

Expected: skip this commit if no follow-up fix was needed.
