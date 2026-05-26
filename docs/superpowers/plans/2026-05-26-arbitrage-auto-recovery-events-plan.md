# Arbitrage Auto Recovery Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `arb.recovery.*` runtime events so arbitrage auto-recovery actions are observable end-to-end without changing the existing recovery policy.

**Architecture:** Reuse the existing `RuntimeEvent`, `AlertRouter`, and arbitrage worker patterns introduced in `B1-5A` and `B1-5B`. Emit the new recovery events only after `TaskRepository` has committed the recovery transition inside `ArbitrageExecutionTaskConsumer._apply_auto_recovery()`, then extend `app/runtime/alerting.py` so `retry_scheduled` and `cooldown_started` stay logger-only while `exhausted` escalates to Feishu.

**Tech Stack:** Python 3.10, `asyncio`, `pytest`, dataclass-based runtime events, existing runtime workers in `app/runtime/live_workers.py`, and alert routing in `app/runtime/alerting.py`.

---

### Task 1: Add Recovery Event Builders And Worker Emission

**Files:**
- Modify: `app/runtime/live_workers.py`
- Test: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

Update `tests/test_live_workers.py` in two places.

First, make sure `FakeTaskRepository` returns task-like objects from the recovery helpers so the worker can build post-commit event payloads:

```python
def mark_auto_recovery_retry(self, task_uuid: str, *, failure_reason: str):
    task = next(task for task in self.executable_tasks if task.task_uuid == task_uuid)
    task.retry_count = int(getattr(task, "retry_count", 0) or 0) + 1
    task.failure_reason = failure_reason
    task.auto_recovery_status = "RETRY_PENDING"
    task.cooldown_until = None
    self.retry_marked.append(
        {"task_uuid": task_uuid, "failure_reason": failure_reason}
    )
    return task


def mark_auto_recovery_cooldown(
    self,
    task_uuid: str,
    *,
    failure_reason: str,
    cooldown_until,
):
    task = next(task for task in self.executable_tasks if task.task_uuid == task_uuid)
    task.failure_reason = failure_reason
    task.auto_recovery_status = "COOLDOWN"
    task.cooldown_until = cooldown_until
    self.cooldowns.append(
        {
            "task_uuid": task_uuid,
            "failure_reason": failure_reason,
            "cooldown_until": cooldown_until,
        }
    )
    return task


def mark_auto_recovery_exhausted(self, task_uuid: str, *, failure_reason: str):
    task = next(task for task in self.executable_tasks if task.task_uuid == task_uuid)
    task.failure_reason = failure_reason
    task.auto_recovery_status = "EXHAUSTED"
    task.cooldown_until = None
    self.exhausted.append(
        {"task_uuid": task_uuid, "failure_reason": failure_reason}
    )
    return task
```

Then add these focused tests near the existing `B1-5B` auto recovery coverage:

```python
@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_emits_retry_scheduled_event_after_retry_transition():
    repository = FakeTaskRepository(task_uuid="arb-close-retry-evt")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-close-retry-evt",
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
    event = _find_event(router.events, "arb.recovery.retry_scheduled")
    assert event.level == "INFO"
    assert event.service == "arb_executor"
    assert event.region == "node-a"
    assert event.payload["task_uuid"] == "arb-close-retry-evt"
    assert event.payload["failure_reason"] == "execution_failed_non_repairable"
    assert event.payload["retry_count"] == 1
    assert event.payload["max_retry_count"] == 2
    assert event.payload["auto_recovery_status"] == "RETRY_PENDING"
    assert event.payload["next_action"] == "RETRY_PENDING"


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_emits_cooldown_started_event_after_cooldown_transition():
    repository = FakeTaskRepository(task_uuid="arb-close-cooldown-evt")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-close-cooldown-evt",
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
    event = _find_event(router.events, "arb.recovery.cooldown_started")
    assert event.level == "INFO"
    assert event.payload["task_uuid"] == "arb-close-cooldown-evt"
    assert event.payload["failure_reason"] == "execution_failed_non_repairable"
    assert event.payload["retry_count"] == 2
    assert event.payload["max_retry_count"] == 2
    assert event.payload["auto_recovery_status"] == "COOLDOWN"
    assert event.payload["next_action"] == "COOLDOWN"
    assert event.payload["cooldown_until"] is not None


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_emits_exhausted_event_after_exhausted_transition():
    repository = FakeTaskRepository(task_uuid="arb-close-exhausted-evt")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-close-exhausted-evt",
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
    event = _find_event(router.events, "arb.recovery.exhausted")
    assert event.level == "ERROR"
    assert event.payload["task_uuid"] == "arb-close-exhausted-evt"
    assert event.payload["failure_reason"] == "execution_failed_non_repairable"
    assert event.payload["retry_count"] == 2
    assert event.payload["max_retry_count"] == 2
    assert event.payload["auto_recovery_status"] == "EXHAUSTED"
    assert event.payload["next_action"] == "EXHAUSTED"


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_emits_retry_scheduled_event_for_failed_repair():
    repository = FakeTaskRepository(task_uuid="arb-open-repair-retry-evt")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-open-repair-retry-evt",
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
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert processed == 1
    event = _find_event(router.events, "arb.recovery.retry_scheduled")
    assert event.payload["task_uuid"] == "arb-open-repair-retry-evt"
    assert event.payload["failure_reason"] == "repair_failed_manual_required"
    assert event.payload["auto_recovery_status"] == "RETRY_PENDING"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "retry_scheduled_event_after_retry_transition or cooldown_started_event_after_cooldown_transition or exhausted_event_after_exhausted_transition or retry_scheduled_event_for_failed_repair"
```

Expected:

```text
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_emits_retry_scheduled_event_after_retry_transition
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_emits_cooldown_started_event_after_cooldown_transition
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_emits_exhausted_event_after_exhausted_transition
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_emits_retry_scheduled_event_for_failed_repair
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/live_workers.py` in two parts.

First, add three event builders near the existing `arb.executor.*` / `arb.repair.*` helpers:

```python
def _build_arb_recovery_retry_scheduled_event(
    *,
    region: str,
    task,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.recovery.retry_scheduled",
        level="INFO",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage recovery retry scheduled",
        payload={
            "task_uuid": str(task.task_uuid),
            "user_id": str(task.user_id),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "spot_exchange": str(task.spot_exchange),
            "derivative_exchange": str(task.derivative_exchange),
            "failure_reason": getattr(task, "failure_reason", None),
            "retry_count": int(getattr(task, "retry_count", 0) or 0),
            "max_retry_count": int(getattr(task, "max_retry_count", 0) or 0),
            "auto_recovery_status": str(getattr(task, "auto_recovery_status", "NONE") or "NONE"),
            "next_action": "RETRY_PENDING",
        },
    )


def _build_arb_recovery_cooldown_started_event(
    *,
    region: str,
    task,
) -> RuntimeEvent:
    cooldown_until = getattr(task, "cooldown_until", None)
    return RuntimeEvent(
        event_type="arb.recovery.cooldown_started",
        level="INFO",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage recovery cooldown started",
        payload={
            "task_uuid": str(task.task_uuid),
            "user_id": str(task.user_id),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "spot_exchange": str(task.spot_exchange),
            "derivative_exchange": str(task.derivative_exchange),
            "failure_reason": getattr(task, "failure_reason", None),
            "retry_count": int(getattr(task, "retry_count", 0) or 0),
            "max_retry_count": int(getattr(task, "max_retry_count", 0) or 0),
            "auto_recovery_status": str(getattr(task, "auto_recovery_status", "NONE") or "NONE"),
            "cooldown_until": (
                cooldown_until.isoformat() if cooldown_until is not None else None
            ),
            "next_action": "COOLDOWN",
        },
    )


def _build_arb_recovery_exhausted_event(
    *,
    region: str,
    task,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage recovery exhausted",
        payload={
            "task_uuid": str(task.task_uuid),
            "user_id": str(task.user_id),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "spot_exchange": str(task.spot_exchange),
            "derivative_exchange": str(task.derivative_exchange),
            "failure_reason": getattr(task, "failure_reason", None),
            "retry_count": int(getattr(task, "retry_count", 0) or 0),
            "max_retry_count": int(getattr(task, "max_retry_count", 0) or 0),
            "auto_recovery_status": str(getattr(task, "auto_recovery_status", "NONE") or "NONE"),
            "next_action": "EXHAUSTED",
        },
    )
```

Then extend `_apply_auto_recovery()` so it emits the recovery event after the repository transition returns the updated task:

```python
async def _apply_auto_recovery(self, *, task, failure_reason: str) -> None:
    decision = _decide_arbitrage_auto_recovery(
        task=task,
        failure_reason=failure_reason,
        cooldown_seconds=self.auto_recovery_cooldown_seconds,
    )
    updated_task = None
    if decision.action == "RETRY_PENDING":
        updated_task = self.task_repository.mark_auto_recovery_retry(
            str(task.task_uuid),
            failure_reason=decision.failure_reason,
        )
        if self.event_router is not None and updated_task is not None:
            await self.event_router.dispatch(
                _build_arb_recovery_retry_scheduled_event(
                    region=self.region,
                    task=updated_task,
                )
            )
        return
    if decision.action == "COOLDOWN":
        updated_task = self.task_repository.mark_auto_recovery_cooldown(
            str(task.task_uuid),
            failure_reason=decision.failure_reason,
            cooldown_until=decision.cooldown_until,
        )
        if self.event_router is not None and updated_task is not None:
            await self.event_router.dispatch(
                _build_arb_recovery_cooldown_started_event(
                    region=self.region,
                    task=updated_task,
                )
            )
        return
    updated_task = self.task_repository.mark_auto_recovery_exhausted(
        str(task.task_uuid),
        failure_reason=decision.failure_reason,
    )
    if self.event_router is not None and updated_task is not None:
        await self.event_router.dispatch(
            _build_arb_recovery_exhausted_event(
                region=self.region,
                task=updated_task,
            )
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "retry_scheduled_event_after_retry_transition or cooldown_started_event_after_cooldown_transition or exhausted_event_after_exhausted_transition or retry_scheduled_event_for_failed_repair"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat(runtime): add arbitrage auto recovery events"
```

### Task 2: Add Alert Titles, Feishu Rendering, And Routing Expectations

**Files:**
- Modify: `app/runtime/alerting.py`
- Test: `tests/test_alerting.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_alerting.py`:

```python
def test_feishu_notifier_renders_arbitrage_recovery_exhausted_message():
    captured = {}

    def fake_urlopen(request, timeout=5):
        captured["body"] = request.data
        return FakeHttpResponse()

    notifier = FeishuNotifier(
        webhook_url="https://example.test/hook",
        urlopen=fake_urlopen,
    )
    event = RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        message="arbitrage recovery exhausted",
        symbol="BTC/USDT",
        payload={
            "task_uuid": "arb-close-exhausted-evt",
            "task_type": "close",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "failure_reason": "execution_failed_non_repairable",
            "retry_count": 2,
            "max_retry_count": 2,
            "auto_recovery_status": "EXHAUSTED",
            "next_action": "EXHAUSTED",
        },
    )

    notifier.send_sync(event)
    body = json.loads(captured["body"].decode("utf-8"))

    assert "套利自动恢复已耗尽" in body["content"]["text"]
    assert "交易对：BTC/USDT" in body["content"]["text"]
    assert "任务类型：close" in body["content"]["text"]
    assert "现货交易所：binance" in body["content"]["text"]
    assert "衍生品交易所：okx" in body["content"]["text"]
    assert "恢复状态：EXHAUSTED" in body["content"]["text"]
    assert "重试次数：2/2" in body["content"]["text"]
    assert "原因：execution_failed_non_repairable" in body["content"]["text"]


@pytest.mark.asyncio
async def test_alert_router_does_not_send_feishu_for_info_recovery_events():
    router = build_router()
    event = RuntimeEvent(
        event_type="arb.recovery.cooldown_started",
        level="INFO",
        service="arb_executor",
        message="arbitrage recovery cooldown started",
        symbol="BTC/USDT",
        payload={
            "task_uuid": "arb-close-cooldown-evt",
            "task_type": "close",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "failure_reason": "execution_failed_non_repairable",
            "retry_count": 2,
            "max_retry_count": 2,
            "auto_recovery_status": "COOLDOWN",
            "cooldown_until": "2026-05-26T10:00:00",
            "next_action": "COOLDOWN",
        },
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert len(router.feishu_notifier.events) == 0
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
async def test_alert_router_sends_feishu_for_error_recovery_exhausted_event():
    router = build_router()
    event = RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        message="arbitrage recovery exhausted",
        symbol="BTC/USDT",
        payload={
            "task_uuid": "arb-close-exhausted-evt",
            "task_type": "close",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "failure_reason": "execution_failed_non_repairable",
            "retry_count": 2,
            "max_retry_count": 2,
            "auto_recovery_status": "EXHAUSTED",
            "next_action": "EXHAUSTED",
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
python -m pytest tests/test_alerting.py -q -k "recovery_exhausted_message or info_recovery_events or error_recovery_exhausted_event"
```

Expected:

```text
FAIL tests/test_alerting.py::test_feishu_notifier_renders_arbitrage_recovery_exhausted_message
FAIL tests/test_alerting.py::test_alert_router_does_not_send_feishu_for_info_recovery_events
FAIL tests/test_alerting.py::test_alert_router_sends_feishu_for_error_recovery_exhausted_event
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
    "arb.recovery.retry_scheduled": "套利自动重试已安排",
    "arb.recovery.cooldown_started": "套利自动冷却已开始",
    "arb.recovery.exhausted": "套利自动恢复已耗尽",
}
```

Then extend `FeishuNotifier._render_text()` so `arb.recovery.*` gets its own rendering branch before the generic `event.event_type.startswith("arb.")` block:

```python
if event.event_type.startswith("arb.recovery."):
    payload = event.payload or {}
    retry_count = payload.get("retry_count", "-")
    max_retry_count = payload.get("max_retry_count", "-")
    return "\n".join(
        [
            title,
            f"服务：{event.service}",
            f"交易对：{event.symbol or '-'}",
            f"任务类型：{payload.get('task_type', '-')}",
            f"现货交易所：{payload.get('spot_exchange', '-')}",
            f"衍生品交易所：{payload.get('derivative_exchange', '-')}",
            f"恢复状态：{payload.get('auto_recovery_status', '-')}",
            f"下一动作：{payload.get('next_action', '-')}",
            f"重试次数：{retry_count}/{max_retry_count}",
            f"冷却截止：{payload.get('cooldown_until', '-')}",
            f"原因：{payload.get('failure_reason', event.message)}",
        ]
    )
```

Do not change `AlertRouter.dispatch()` branching logic in this task. The existing behavior should already keep `INFO` recovery events logger-only and route `ERROR` recovery events to Feishu.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_alerting.py -q -k "recovery_exhausted_message or info_recovery_events or error_recovery_exhausted_event"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/alerting.py tests/test_alerting.py
git commit -m "feat(alerting): add arbitrage recovery alert templates"
```

### Task 3: Run Focused Recovery-Event Regressions

**Files:**
- Review: `docs/superpowers/specs/2026-05-26-arbitrage-auto-recovery-events-design.md`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_alerting.py`

- [ ] **Step 1: Run the B1-5C focused suite**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_alerting.py -q -k "arb.recovery or recovery_exhausted_message or retry_scheduled_event_after_retry_transition or cooldown_started_event_after_cooldown_transition"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Re-check B1-5B auto recovery coverage**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_task_repository.py -q -k "auto_recovery or cooldown or retry_pending or marks_exhausted_after_cooldown_retry_fails"
```

Expected:

```text
selected B1-5B tests pass unchanged
```

- [ ] **Step 3: Re-check B1-5A arbitrage observability coverage**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_alerting.py -q -k "arb.executor or arb.repair or arbitrage_failure_message or info_arbitrage_events"
```

Expected:

```text
selected B1-5A tests pass unchanged
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
shows the two B1-5C implementation commits on top, followed by the B1-5C spec/plan commits and recent B1-5B commits
```
