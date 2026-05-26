# Executor OPEN_PARTIAL Alert Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `executor.task.failed` alerts for repairable `OPEN_PARTIAL` results while preserving `executor.execution_result`, `executor.repair_planned`, and all real failure alerts.

**Architecture:** Keep the change isolated to the executor consumer event-emission branch in `app/runtime/live_workers.py`. First add focused red tests that prove repairable `OPEN_PARTIAL` must not emit `executor.task.failed` but true failures still must; then implement the minimal `should_emit_failed_event` guard, run focused regression plus nearby coverage, and only then consider a light remote verification if needed before push.

**Tech Stack:** Python 3.10+, pytest, asyncio, Redis-style worker consumers, existing runtime event router and fake test doubles

---

## File Structure

- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - Add a minimal boolean guard so repairable `OPEN_PARTIAL` results skip `executor.task.failed`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - Add focused tests proving repairable `OPEN_PARTIAL` no longer emits the failed event
  - Add a counter-example test proving non-repair failure still emits the failed event

## Task 1: Lock The Alert Semantics With Failing Tests

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Write the failing regression for repairable `OPEN_PARTIAL`**

Add this test near the existing `executor.execution_result` / `executor.repair_planned` coverage in `tests/test_live_workers.py`:

```python
@pytest.mark.asyncio
async def test_executor_does_not_emit_failed_event_for_repairable_open_partial_result():
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
    assert _find_event(router.events, "executor.execution_result").payload[
        "execution_status"
    ] == "OPEN_PARTIAL"
    assert _find_event(router.events, "executor.repair_planned").payload[
        "repair_action"
    ] == "AUTO_HEDGE_REPAIRING"
    assert all(event.event_type != "executor.task.failed" for event in router.events)
```

- [ ] **Step 2: Write the failing counter-example for true failure**

Add this second test immediately after the previous one:

```python
@pytest.mark.asyncio
async def test_executor_still_emits_failed_event_for_non_repair_failure_result():
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
            "execution_status": "FAILED",
            "filled_exchanges": [],
            "failed_exchanges": ["okx", "gate"],
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
    failed_event = _find_event(router.events, "executor.task.failed")
    assert failed_event.message == "executor task failed"
    assert failed_event.payload["error"] == "FAILED"
```

- [ ] **Step 3: Run the focused tests and watch the first one fail**

Run:

```bash
pytest tests/test_live_workers.py -k "repairable_open_partial_result or non_repair_failure_result" -q
```

Expected:

- the repairable `OPEN_PARTIAL` test FAILS because `executor.task.failed` is still emitted
- the non-repair failure test may already PASS

- [ ] **Step 4: Verify nearby existing behavior remains represented**

Run:

```bash
pytest tests/test_live_workers.py -k "rich_open_partial_result or repair_planned_event_for_open_partial_result" -q
```

Expected: existing nearby tests PASS, confirming we are editing the right area without changing their assumptions yet.

- [ ] **Step 5: Commit the red tests**

```bash
git add tests/test_live_workers.py
git commit -m "test: cover executor open partial alert semantics"
```

Expected: the commit may fail if you intentionally keep the tree red. If so, skip the commit and record explicitly that the branch is intentionally red until Task 2 turns it green.

## Task 2: Implement The Minimal Failed-Event Guard

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Add the minimal guard in the executor consumer**

Update the `execution_status is not None` branch in `app/runtime/live_workers.py` so it computes:

```python
should_emit_failed_event = lifecycle_status == "FAILED"
if (
    execution_status == "OPEN_PARTIAL"
    and failed_exchanges
    and repair_plan.action != "NONE"
):
    should_emit_failed_event = False
```

And replace:

```python
if lifecycle_status == "FAILED":
```

with:

```python
if should_emit_failed_event:
```

Keep everything else in that branch unchanged.

- [ ] **Step 2: Run the focused tests and turn them green**

Run:

```bash
pytest tests/test_live_workers.py -k "repairable_open_partial_result or non_repair_failure_result" -q
```

Expected:

```text
2 passed
```

- [ ] **Step 3: Run nearby regression coverage**

Run:

```bash
pytest tests/test_live_workers.py -k "rich_open_partial_result or repair_planned_event_for_open_partial_result or publishes_repair_task_for_open_partial_result or marks_task_failed_when_dispatch_raises" -q
```

Expected: all selected tests PASS.

- [ ] **Step 4: Run a syntax sanity check**

Run:

```bash
python -m py_compile app/runtime/live_workers.py tests/test_live_workers.py
```

Expected: PASS with no output.

- [ ] **Step 5: Commit the green fix**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "fix: suppress executor failed event for repairable partials"
```

Expected: one commit containing the minimal guard plus focused regression coverage.

## Task 3: Final Verification And Optional Remote Spot Check

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Run the broader targeted suite**

Run:

```bash
pytest tests/test_live_workers.py tests/test_worker_service.py tests/test_repair_execution_service.py tests/test_task_repository.py -q
```

Expected: PASS with no new regressions in nearby runtime/repair behavior.

- [ ] **Step 2: Check the working tree and recent commits**

Run:

```bash
git status -sb
git log -5 --oneline
```

Expected:

- working tree is clean
- latest commit includes `fix: suppress executor failed event for repairable partials`

- [ ] **Step 3: Prepare the remote verification note**

Record this exact handoff note:

```text
Remote restart of furun-spot-executor.service is recommended after push to confirm Feishu no longer receives executor.task.failed / OPEN_PARTIAL.
No new remote canary is required because this fix only changes executor event emission for already-covered OPEN_PARTIAL behavior.
```

- [ ] **Step 4: Summarize push-readiness**

Record these facts in the implementation handoff:

```text
- repairable OPEN_PARTIAL no longer emits executor.task.failed
- executor.execution_result still emits OPEN_PARTIAL details
- executor.repair_planned still emits repair intent
- true non-repair failure still emits executor.task.failed
- live_workers.py is the only production code file changed
```
