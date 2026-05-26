# Arbitrage Recovery Policy Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade arbitrage auto recovery from one-size-fits-all handling to classified failure recovery with mapped actions, tiered cooldown windows, and task-level exhausted alert deduplication.

**Architecture:** Keep `ArbitrageExecutionTaskConsumer` as the single executor/repair failure entry point, but split the policy internals into a failure classifier and a recovery decision helper inside `app/runtime/live_workers.py`. Preserve `TaskRepository` as the state truth layer, reuse the existing `arb.recovery.*` event chain from `B1-5C`, and only tighten `AlertRouter` dedupe so `arb.recovery.exhausted` dedupes by `task_uuid` instead of the generic `symbol + exchange` key.

**Tech Stack:** Python 3.10, `asyncio`, `pytest`, dataclass-based runtime events, runtime workers in `app/runtime/live_workers.py`, alert routing in `app/runtime/alerting.py`, repository transitions already present in `app/db/task_repository.py`.

---

### Task 1: Add Failure Classification Helpers

**Files:**
- Modify: `app/runtime/live_workers.py`
- Test: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

Add these focused tests to `tests/test_live_workers.py` near the existing `B1-5B/B1-5C` arbitrage recovery coverage:

```python
def test_classify_arbitrage_failure_returns_transient_network_for_timeout_text():
    result = _classify_arbitrage_failure(
        execution_status="FAILED",
        failure_reason="connection timeout while placing order",
        repair_result=None,
    )

    assert result == "TRANSIENT_NETWORK"


def test_classify_arbitrage_failure_returns_temporary_route_for_route_resolution_text():
    result = _classify_arbitrage_failure(
        execution_status="FAILED",
        failure_reason="missing execution account for exchange=okx",
        repair_result=None,
    )

    assert result == "TEMPORARY_ROUTE"


def test_classify_arbitrage_failure_returns_exchange_rejected_for_reduce_only_text():
    result = _classify_arbitrage_failure(
        execution_status="FAILED",
        failure_reason="order rejected because reduce-only is required",
        repair_result=None,
    )

    assert result == "EXCHANGE_REJECTED"


def test_classify_arbitrage_failure_returns_repair_failed_for_failed_repair_result():
    repair_result = type(
        "RepairResult",
        (),
        {
            "ok": False,
            "status": "MANUAL_REQUIRED",
            "reason": "repair order failed",
        },
    )()

    result = _classify_arbitrage_failure(
        execution_status="OPEN_PARTIAL",
        failure_reason="repair_failed_manual_required",
        repair_result=repair_result,
    )

    assert result == "REPAIR_FAILED"


def test_classify_arbitrage_failure_returns_unknown_hard_failure_when_no_rule_matches():
    result = _classify_arbitrage_failure(
        execution_status="FAILED",
        failure_reason="unclassified fatal state",
        repair_result=None,
    )

    assert result == "UNKNOWN_HARD_FAILURE"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "classify_arbitrage_failure_returns_transient_network or classify_arbitrage_failure_returns_temporary_route or classify_arbitrage_failure_returns_exchange_rejected or classify_arbitrage_failure_returns_repair_failed or classify_arbitrage_failure_returns_unknown_hard_failure"
```

Expected:

```text
FAIL tests/test_live_workers.py::test_classify_arbitrage_failure_returns_transient_network_for_timeout_text
FAIL tests/test_live_workers.py::test_classify_arbitrage_failure_returns_temporary_route_for_route_resolution_text
FAIL tests/test_live_workers.py::test_classify_arbitrage_failure_returns_exchange_rejected_for_reduce_only_text
FAIL tests/test_live_workers.py::test_classify_arbitrage_failure_returns_repair_failed_for_failed_repair_result
FAIL tests/test_live_workers.py::test_classify_arbitrage_failure_returns_unknown_hard_failure_when_no_rule_matches
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/live_workers.py` by adding one focused classifier helper above `ArbitrageExecutionTaskConsumer`:

```python
def _classify_arbitrage_failure(
    *,
    execution_status: str,
    failure_reason: str,
    repair_result: Any | None = None,
) -> str:
    if repair_result is not None and not getattr(repair_result, "ok", False):
        return "REPAIR_FAILED"

    normalized = (failure_reason or "").lower()
    transient_network_keywords = (
        "timeout",
        "connection",
        "network",
        "reset",
        "temporarily unavailable",
    )
    temporary_route_keywords = (
        "route",
        "missing execution account",
        "dispatcher region",
    )
    exchange_rejected_keywords = (
        "reject",
        "invalid",
        "insufficient",
        "reduce-only",
        "order not accepted",
    )

    if any(keyword in normalized for keyword in transient_network_keywords):
        return "TRANSIENT_NETWORK"
    if any(keyword in normalized for keyword in temporary_route_keywords):
        return "TEMPORARY_ROUTE"
    if any(keyword in normalized for keyword in exchange_rejected_keywords):
        return "EXCHANGE_REJECTED"
    return "UNKNOWN_HARD_FAILURE"
```

Do not change any recovery decision logic in this task.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "classify_arbitrage_failure_returns_transient_network or classify_arbitrage_failure_returns_temporary_route or classify_arbitrage_failure_returns_exchange_rejected or classify_arbitrage_failure_returns_repair_failed or classify_arbitrage_failure_returns_unknown_hard_failure"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat(runtime): add arbitrage failure classification"
```

### Task 2: Add Classified Recovery Decisions And Tiered Cooldown

**Files:**
- Modify: `app/runtime/live_workers.py`
- Test: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_live_workers.py`:

```python
def test_decide_arbitrage_recovery_returns_retry_pending_for_transient_network():
    task = type(
        "Task",
        (),
        {"retry_count": 0, "max_retry_count": 2, "auto_recovery_status": "NONE"},
    )()

    decision = _decide_arbitrage_recovery(
        task=task,
        failure_category="TRANSIENT_NETWORK",
        failure_reason="connection timeout while placing order",
        now=datetime(2026, 5, 26, 10, 0, 0),
    )

    assert decision.action == "RETRY_PENDING"
    assert decision.failure_reason == "TRANSIENT_NETWORK"
    assert decision.cooldown_until is None


def test_decide_arbitrage_recovery_returns_cooldown_for_exchange_rejected_with_scaled_window():
    task = type(
        "Task",
        (),
        {"retry_count": 1, "max_retry_count": 3, "auto_recovery_status": "NONE"},
    )()

    base_time = datetime(2026, 5, 26, 10, 0, 0)
    decision = _decide_arbitrage_recovery(
        task=task,
        failure_category="EXCHANGE_REJECTED",
        failure_reason="order rejected because reduce-only is required",
        now=base_time,
    )

    assert decision.action == "COOLDOWN"
    assert decision.failure_reason == "EXCHANGE_REJECTED"
    assert decision.cooldown_until == base_time + timedelta(seconds=600)


def test_decide_arbitrage_recovery_returns_exhausted_for_unknown_hard_failure():
    task = type(
        "Task",
        (),
        {"retry_count": 0, "max_retry_count": 2, "auto_recovery_status": "NONE"},
    )()

    decision = _decide_arbitrage_recovery(
        task=task,
        failure_category="UNKNOWN_HARD_FAILURE",
        failure_reason="unclassified fatal state",
        now=datetime(2026, 5, 26, 10, 0, 0),
    )

    assert decision.action == "EXHAUSTED"
    assert decision.failure_reason == "UNKNOWN_HARD_FAILURE"


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_routes_timeout_failure_to_retry_pending():
    repository = FakeTaskRepository(task_uuid="arb-close-net-1")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-close-net-1",
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
                    "reason": "connection timeout while placing order",
                },
            )()
        ),
        repair_service=FakeRepairExecutionService(result=None),
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(account_id=11, exchange="binance"), FakeExchangeAccount(account_id=12, exchange="okx")]}
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
        {"task_uuid": "arb-close-net-1", "failure_reason": "TRANSIENT_NETWORK"}
    ]
    assert repository.cooldowns == []
    assert repository.exhausted == []


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_routes_exchange_rejected_to_cooldown():
    repository = FakeTaskRepository(task_uuid="arb-close-reject-1")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-close-reject-1",
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
                    "reason": "order rejected because reduce-only is required",
                },
            )()
        ),
        repair_service=FakeRepairExecutionService(result=None),
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(account_id=11, exchange="binance"), FakeExchangeAccount(account_id=12, exchange="okx")]}
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
    assert repository.cooldowns[0]["task_uuid"] == "arb-close-reject-1"
    assert repository.cooldowns[0]["failure_reason"] == "EXCHANGE_REJECTED"


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_routes_unknown_hard_failure_to_exhausted():
    repository = FakeTaskRepository(task_uuid="arb-close-hard-1")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-close-hard-1",
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
                    "reason": "unclassified fatal state",
                },
            )()
        ),
        repair_service=FakeRepairExecutionService(result=None),
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(account_id=11, exchange="binance"), FakeExchangeAccount(account_id=12, exchange="okx")]}
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
        {"task_uuid": "arb-close-hard-1", "failure_reason": "UNKNOWN_HARD_FAILURE"}
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "decide_arbitrage_recovery_returns_retry_pending or decide_arbitrage_recovery_returns_cooldown_for_exchange_rejected or decide_arbitrage_recovery_returns_exhausted_for_unknown_hard_failure or routes_timeout_failure_to_retry_pending or routes_exchange_rejected_to_cooldown or routes_unknown_hard_failure_to_exhausted"
```

Expected:

```text
FAIL tests/test_live_workers.py::test_decide_arbitrage_recovery_returns_retry_pending_for_transient_network
FAIL tests/test_live_workers.py::test_decide_arbitrage_recovery_returns_cooldown_for_exchange_rejected_with_scaled_window
FAIL tests/test_live_workers.py::test_decide_arbitrage_recovery_returns_exhausted_for_unknown_hard_failure
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_routes_timeout_failure_to_retry_pending
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_routes_exchange_rejected_to_cooldown
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_routes_unknown_hard_failure_to_exhausted
```

- [ ] **Step 3: Write the minimal implementation**

Replace the current coarse recovery decision helper in `app/runtime/live_workers.py` with a classified decision helper:

```python
@dataclass(slots=True)
class ArbitrageAutoRecoveryDecision:
    action: str
    failure_reason: str
    cooldown_until: datetime | None = None


def _cooldown_seconds_for_failure_category(*, failure_category: str, retry_count: int) -> int:
    base_windows = {
        "TRANSIENT_NETWORK": 0,
        "TEMPORARY_ROUTE": 60,
        "EXCHANGE_REJECTED": 300,
        "REPAIR_FAILED": 180,
        "UNKNOWN_HARD_FAILURE": 0,
    }
    base_seconds = base_windows.get(failure_category, 0)
    if base_seconds <= 0:
        return 0
    if retry_count <= 0:
        multiplier = 1
    elif retry_count == 1:
        multiplier = 2
    else:
        multiplier = 3
    return base_seconds * multiplier


def _decide_arbitrage_recovery(
    *,
    task,
    failure_category: str,
    failure_reason: str,
    now: datetime | None = None,
) -> ArbitrageAutoRecoveryDecision:
    retry_count = int(getattr(task, "retry_count", 0) or 0)
    auto_recovery_status = str(getattr(task, "auto_recovery_status", "NONE") or "NONE")
    current_time = now or datetime.utcnow()

    if failure_category == "TRANSIENT_NETWORK":
        return ArbitrageAutoRecoveryDecision(
            action="RETRY_PENDING",
            failure_reason=failure_category,
        )
    if failure_category in {"TEMPORARY_ROUTE", "EXCHANGE_REJECTED"}:
        cooldown_seconds = _cooldown_seconds_for_failure_category(
            failure_category=failure_category,
            retry_count=retry_count,
        )
        return ArbitrageAutoRecoveryDecision(
            action="COOLDOWN",
            failure_reason=failure_category,
            cooldown_until=current_time + timedelta(seconds=cooldown_seconds),
        )
    if failure_category == "REPAIR_FAILED":
        if auto_recovery_status == "COOLDOWN":
            return ArbitrageAutoRecoveryDecision(
                action="EXHAUSTED",
                failure_reason=failure_category,
            )
        cooldown_seconds = _cooldown_seconds_for_failure_category(
            failure_category=failure_category,
            retry_count=retry_count,
        )
        return ArbitrageAutoRecoveryDecision(
            action="COOLDOWN",
            failure_reason=failure_category,
            cooldown_until=current_time + timedelta(seconds=cooldown_seconds),
        )
    return ArbitrageAutoRecoveryDecision(
        action="EXHAUSTED",
        failure_reason="UNKNOWN_HARD_FAILURE",
    )
```

Then update `_apply_auto_recovery()` and its callers so classification happens first:

```python
async def _apply_auto_recovery(
    self,
    *,
    task,
    failure_reason: str,
    repair_result: Any | None = None,
) -> None:
    failure_category = _classify_arbitrage_failure(
        execution_status=str(getattr(task, "execution_status", "") or ""),
        failure_reason=failure_reason,
        repair_result=repair_result,
    )
    decision = _decide_arbitrage_recovery(
        task=task,
        failure_category=failure_category,
        failure_reason=failure_reason,
    )
    ...
```

Use the more specific failure text from execution / repair results:

```python
failure_reason = str(
    getattr(result, "reason", None)
    or getattr(result, "execution_status", "")
    or "execution_failed_non_repairable"
)
await self._apply_auto_recovery(
    task=task,
    failure_reason=failure_reason,
)
```

```python
failure_reason = str(
    getattr(repair_result, "reason", None)
    or getattr(repair_result, "status", "")
    or "repair_failed_manual_required"
)
await self._apply_auto_recovery(
    task=task,
    failure_reason=failure_reason,
    repair_result=repair_result,
)
```

Do not change `TaskRepository` APIs in this task.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "decide_arbitrage_recovery_returns_retry_pending or decide_arbitrage_recovery_returns_cooldown_for_exchange_rejected or decide_arbitrage_recovery_returns_exhausted_for_unknown_hard_failure or routes_timeout_failure_to_retry_pending or routes_exchange_rejected_to_cooldown or routes_unknown_hard_failure_to_exhausted"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat(runtime): upgrade arbitrage recovery policy"
```

### Task 3: Add Repair Escalation, Classified Recovery Events, And Exhausted Dedupe

**Files:**
- Modify: `app/runtime/live_workers.py`
- Modify: `app/runtime/alerting.py`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_alerting.py`

- [ ] **Step 1: Write the failing tests**

Add these tests.

In `tests/test_live_workers.py`:

```python
@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_routes_first_repair_failure_to_cooldown():
    repository = FakeTaskRepository(task_uuid="arb-open-repair-cooldown-1")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-open-repair-cooldown-1",
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
            {"42": [FakeExchangeAccount(account_id=11, exchange="binance"), FakeExchangeAccount(account_id=12, exchange="okx")]}
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
    assert repository.retry_marked == []
    assert len(repository.cooldowns) == 1
    assert repository.cooldowns[0]["failure_reason"] == "REPAIR_FAILED"
    event = _find_event(router.events, "arb.recovery.cooldown_started")
    assert event.payload["failure_reason"] == "REPAIR_FAILED"


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_routes_second_repair_failure_to_exhausted():
    repository = FakeTaskRepository(task_uuid="arb-open-repair-exhausted-1")
    task = type(
        "Task",
        (),
        {
            "task_uuid": "arb-open-repair-exhausted-1",
            "user_id": 42,
            "task_type": "open",
            "symbol": "BTC/USDT",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "target_notional": 100.0,
            "retry_count": 1,
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
            {"42": [FakeExchangeAccount(account_id=11, exchange="binance"), FakeExchangeAccount(account_id=12, exchange="okx")]}
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
    assert repository.cooldowns == []
    assert repository.exhausted == [
        {"task_uuid": "arb-open-repair-exhausted-1", "failure_reason": "REPAIR_FAILED"}
    ]
    event = _find_event(router.events, "arb.recovery.exhausted")
    assert event.payload["failure_reason"] == "REPAIR_FAILED"
```

In `tests/test_alerting.py`:

```python
@pytest.mark.asyncio
async def test_alert_router_dedupes_recovery_exhausted_by_task_uuid():
    timestamps = iter([100.0, 120.0, 121.0])
    router = AlertRouter(
        logger=FakeLogger(),
        feishu_notifier=FakeNotifier(),
        email_notifier=FakeNotifier(),
        alerts_enabled=True,
        feishu_enabled=True,
        email_enabled=True,
        success_spread_bps_threshold=50.0,
        dedupe_window_seconds=60,
        time_provider=lambda: next(timestamps),
    )
    first = RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        message="arbitrage recovery exhausted",
        symbol="BTC/USDT",
        exchange="binance",
        payload={"task_uuid": "arb-exhausted-1", "error": "TRANSIENT_NETWORK"},
    )
    duplicate = RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        message="arbitrage recovery exhausted",
        symbol="BTC/USDT",
        exchange="okx",
        payload={"task_uuid": "arb-exhausted-1", "error": "TRANSIENT_NETWORK"},
    )
    different_task = RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        message="arbitrage recovery exhausted",
        symbol="BTC/USDT",
        exchange="okx",
        payload={"task_uuid": "arb-exhausted-2", "error": "TRANSIENT_NETWORK"},
    )

    await router.dispatch(first)
    await router.dispatch(duplicate)
    await router.dispatch(different_task)

    assert len(router.logger.events) == 3
    assert len(router.feishu_notifier.events) == 2
    assert router.feishu_notifier.events[0].payload["task_uuid"] == "arb-exhausted-1"
    assert router.feishu_notifier.events[1].payload["task_uuid"] == "arb-exhausted-2"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_alerting.py -q -k "first_repair_failure_to_cooldown or second_repair_failure_to_exhausted or dedupes_recovery_exhausted_by_task_uuid"
```

Expected:

```text
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_routes_first_repair_failure_to_cooldown
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_routes_second_repair_failure_to_exhausted
FAIL tests/test_alerting.py::test_alert_router_dedupes_recovery_exhausted_by_task_uuid
```

- [ ] **Step 3: Write the minimal implementation**

In `app/runtime/live_workers.py`, make sure repair failures pass through the new classified recovery path and emit classified recovery events without changing event names:

```python
await self._apply_auto_recovery(
    task=task,
    failure_reason=str(
        getattr(repair_result, "reason", None)
        or getattr(repair_result, "status", "")
        or "repair_failed_manual_required"
    ),
    repair_result=repair_result,
)
```

The already-updated `_classify_arbitrage_failure()` and `_decide_arbitrage_recovery()` from Task 2 should make the first repair failure land in `COOLDOWN` and the second post-cooldown repair failure land in `EXHAUSTED`.

In `app/runtime/alerting.py`, update the dedupe key logic for recovery exhausted events only:

```python
def _should_dedupe(self, event: RuntimeEvent) -> bool:
    if event.event_type == "arb.recovery.exhausted":
        task_uuid = None if event.payload is None else event.payload.get("task_uuid")
        key = f"{event.event_type}:{task_uuid or '-'}"
    else:
        key = f"{event.event_type}:{event.symbol or '-'}:{event.exchange or '-'}"
    now = self.time_provider()
    last_sent = self._dedupe_cache.get(key)
    dedupe_window_seconds = max(self.dedupe_window_seconds, 300)
    if last_sent is not None and now - last_sent < dedupe_window_seconds:
        return True
    self._dedupe_cache[key] = now
    return False
```

Do not broaden Feishu routing beyond the existing `ERROR` rules in this task.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_alerting.py -q -k "first_repair_failure_to_cooldown or second_repair_failure_to_exhausted or dedupes_recovery_exhausted_by_task_uuid"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/live_workers.py app/runtime/alerting.py tests/test_live_workers.py tests/test_alerting.py
git commit -m "feat(runtime): refine arbitrage recovery escalation"
```

### Task 4: Run Focused Policy-Upgrade Regressions

**Files:**
- Review: `docs/superpowers/specs/2026-05-26-arbitrage-recovery-policy-upgrade-design.md`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_alerting.py`
- Test: `tests/test_task_repository.py`

- [ ] **Step 1: Run the B1-5D/E focused suite**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_alerting.py -q -k "classify_arbitrage_failure or decide_arbitrage_recovery or arb.recovery or first_repair_failure_to_cooldown or second_repair_failure_to_exhausted"
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Re-check B1-5B task-level recovery and claim safety**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_task_repository.py -q -k "auto_recovery or cooldown or retry_pending or claim_next_executable_task or no_claimable_task"
```

Expected:

```text
selected B1-5B tests pass unchanged
```

- [ ] **Step 3: Re-check B1-5C recovery events and alert routing**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_alerting.py -q -k "arb.recovery or recovery_exhausted_message or info_recovery_events or error_recovery_exhausted_event"
```

Expected:

```text
selected B1-5C tests pass unchanged
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
git log --oneline -n 8
```

Expected:

```text
shows the three B1-5D/E implementation commits on top, followed by the B1-5D/E spec/plan commits and recent B1-5C commits
```
