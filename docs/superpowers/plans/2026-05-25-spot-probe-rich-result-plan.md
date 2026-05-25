# Spot Probe Rich Result Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `SpotArbitrageTaskResult` with richer leg-level execution details so the real probe path reports stable leg statuses, failure stages, and leg error codes without regressing executor summary writeback.

**Architecture:** Keep the work on the existing `SpotArbitrageProbeService` path instead of unifying all execution models. First add failing probe tests for leg-level success and failure stages, then implement the minimal richer result fields and state transitions inside `run_task()`, and finally verify that `RedisExecutionTaskConsumer` still consumes the richer result without regressing execution-summary persistence.

**Tech Stack:** Python 3.10+, pytest, pytest-asyncio, Redis Streams worker flow, existing spot arbitrage runtime service, current executor summary writeback path

---

## File Structure

- Modify: `d:\old\FuRunSystemV4\app\runtime\spot_arbitrage_probe.py`
  - Extend `SpotArbitrageTaskResult` with richer leg-level fields
  - Track per-leg status, per-leg error code/detail, and `failed_stage` through `run_task()`
- Modify: `d:\old\FuRunSystemV4\tests\test_spot_arbitrage_probe.py`
  - Add direct probe tests for `create_sell`, `cancel_*`, and `fetch_final_*` richer-result paths
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - Add compatibility tests proving richer result fields do not break executor task summary writeback

## Task 1: Add Failing Probe Tests For Richer Result Fields

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_spot_arbitrage_probe.py`

- [ ] **Step 1: Write the failing richer-result tests**

Add these fake clients near the existing `FakeClient` helpers:

```python
class CancelFailClient(FakeClient):
    async def cancel_order(self, order_id, symbol):
        raise RuntimeError("cancel order failed")


class FinalFetchFailClient(FakeClient):
    async def __init__(self, exchange, bid, ask, fail_on_create=False):
        super().__init__(exchange, bid, ask, fail_on_create=fail_on_create)
        self.fetch_count = 0

    async def fetch_order(self, order_id, symbol):
        self.fetch_count += 1
        if self.fetch_count >= 2:
            raise RuntimeError("final fetch failed")
        return await super().fetch_order(order_id, symbol)


class CancelFailFactory(FakeFactory):
    def create_session(self, exchange, env_mode, proxies, credentials):
        config = self.client_configs[exchange]
        client = CancelFailClient(
            exchange,
            config["bid"],
            config["ask"],
            fail_on_create=config["fail_on_create"],
        )
        self.created_clients[exchange].append(client)
        self.create_session_calls.append(exchange)
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=client,
        )


class FinalFetchFailFactory(FakeFactory):
    def create_session(self, exchange, env_mode, proxies, credentials):
        config = self.client_configs[exchange]
        client = FinalFetchFailClient(
            exchange,
            config["bid"],
            config["ask"],
            fail_on_create=config["fail_on_create"],
        )
        self.created_clients[exchange].append(client)
        self.create_session_calls.append(exchange)
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=client,
        )
```

Add these tests:

```python
@pytest.mark.asyncio
async def test_spot_arbitrage_probe_records_leg_statuses_for_full_success():
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
    assert result.buy_leg_status == "final_fetched"
    assert result.sell_leg_status == "final_fetched"
    assert result.failed_stage is None
    assert result.buy_leg_error_code is None
    assert result.sell_leg_error_code is None


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_records_create_sell_failure_details():
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

    assert result.execution_status == "OPEN_PARTIAL"
    assert result.buy_leg_status == "created"
    assert result.sell_leg_status == "create_failed"
    assert result.failed_stage == "create_sell"
    assert result.sell_leg_error_code == "sell_create_failed"
    assert "create order failed" in result.sell_leg_error_detail


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_records_cancel_failure_details():
    service = SpotArbitrageProbeService(session_factory=CancelFailFactory())
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.execution_status == "OPEN_PARTIAL"
    assert result.failed_stage in {"cancel_buy", "cancel_sell"}
    assert result.buy_leg_status in {"cancel_failed", "cancelled", "final_fetched"}
    assert result.sell_leg_status in {"cancel_failed", "cancelled", "final_fetched"}
    assert {result.buy_leg_error_code, result.sell_leg_error_code} & {
        "buy_cancel_failed",
        "sell_cancel_failed",
    }


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_records_final_fetch_failure_details():
    service = SpotArbitrageProbeService(session_factory=FinalFetchFailFactory())
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.execution_status == "OPEN_PARTIAL"
    assert result.failed_stage in {"fetch_final_buy", "fetch_final_sell"}
    assert result.buy_leg_status in {"final_fetch_failed", "final_fetched"}
    assert result.sell_leg_status in {"final_fetch_failed", "final_fetched"}
    assert {result.buy_leg_error_code, result.sell_leg_error_code} & {
        "buy_final_fetch_failed",
        "sell_final_fetch_failed",
    }
```

- [ ] **Step 2: Run the richer-result probe tests to verify they fail**

Run: `python -m pytest -q tests/test_spot_arbitrage_probe.py -k "leg_statuses_for_full_success or create_sell_failure_details or cancel_failure_details or final_fetch_failure_details"`

Expected: FAIL because `SpotArbitrageTaskResult` does not yet expose `buy_leg_status`, `sell_leg_status`, `buy_leg_error_code`, `sell_leg_error_code`, `buy_leg_error_detail`, `sell_leg_error_detail`, or `failed_stage`.

- [ ] **Step 3: Commit the failing-test slice only after verifying red**

```bash
git add tests/test_spot_arbitrage_probe.py
git commit -m "test: add spot probe rich result regressions"
```

Expected: only do this commit if your workflow explicitly commits red tests; otherwise skip this commit and continue directly to Task 2.

## Task 2: Implement Richer Result Fields In SpotArbitrageProbeService

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\spot_arbitrage_probe.py`

- [ ] **Step 1: Add richer fields to `SpotArbitrageTaskResult`**

Add these fields to the dataclass:

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
    buy_leg_status: str | None = None
    sell_leg_status: str | None = None
    buy_leg_error_code: str | None = None
    sell_leg_error_code: str | None = None
    buy_leg_error_detail: str | None = None
    sell_leg_error_detail: str | None = None
    failed_stage: str | None = None
```

- [ ] **Step 2: Initialize and update leg-level state in `run_task()`**

At the top of `run_task()`, initialize richer-result tracking variables:

```python
buy_leg_status = "not_started"
sell_leg_status = "not_started"
buy_leg_error_code = None
sell_leg_error_code = None
buy_leg_error_detail = None
sell_leg_error_detail = None
failed_stage = None
```

Before each stage, update state exactly like this:

```python
buy_leg_status = "create_submitted"
buy_order = await adapters[buy_exchange].create_order(buy_request)
buy_leg_status = "created"
filled_exchanges.append(buy_exchange)

sell_leg_status = "create_submitted"
sell_order = await adapters[sell_exchange].create_order(sell_request)
sell_leg_status = "created"
filled_exchanges.append(sell_exchange)

buy_leg_status = "cancel_submitted"
await adapters[buy_exchange].cancel_order(buy_order["id"], symbol)
buy_leg_status = "cancelled"

sell_leg_status = "cancel_submitted"
await adapters[sell_exchange].cancel_order(sell_order["id"], symbol)
sell_leg_status = "cancelled"

buy_final = await adapters[buy_exchange].fetch_order(buy_order["id"], symbol)
buy_leg_status = "final_fetched"
sell_final = await adapters[sell_exchange].fetch_order(sell_order["id"], symbol)
sell_leg_status = "final_fetched"
```

- [ ] **Step 3: Add stage-specific exception mapping**

Wrap each stage that can fail with minimal exception mapping:

```python
try:
    sell_leg_status = "create_submitted"
    sell_order = await adapters[sell_exchange].create_order(sell_request)
    sell_leg_status = "created"
    filled_exchanges.append(sell_exchange)
except Exception as exc:
    sell_leg_status = "create_failed"
    sell_leg_error_code = "sell_create_failed"
    sell_leg_error_detail = str(exc)
    failed_stage = "create_sell"
    failed_exchanges.append(sell_exchange)
    raise
```

Use the same pattern for:

```python
buy_cancel_failed -> failed_stage = "cancel_buy"
sell_cancel_failed -> failed_stage = "cancel_sell"
buy_final_fetch_failed -> failed_stage = "fetch_final_buy"
sell_final_fetch_failed -> failed_stage = "fetch_final_sell"
```

- [ ] **Step 4: Return richer result on both success and failure**

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
    buy_leg_status=buy_leg_status,
    sell_leg_status=sell_leg_status,
    buy_leg_error_code=buy_leg_error_code,
    sell_leg_error_code=sell_leg_error_code,
    buy_leg_error_detail=buy_leg_error_detail,
    sell_leg_error_detail=sell_leg_error_detail,
    failed_stage=failed_stage,
)
```

Replace the exception return with:

```python
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
    buy_leg_status=buy_leg_status,
    sell_leg_status=sell_leg_status,
    buy_leg_error_code=buy_leg_error_code,
    sell_leg_error_code=sell_leg_error_code,
    buy_leg_error_detail=buy_leg_error_detail,
    sell_leg_error_detail=sell_leg_error_detail,
    failed_stage=failed_stage,
)
```

- [ ] **Step 5: Run the richer-result probe tests to verify they pass**

Run: `python -m pytest -q tests/test_spot_arbitrage_probe.py -k "leg_statuses_for_full_success or create_sell_failure_details or cancel_failure_details or final_fetch_failure_details"`

Expected: PASS with all new richer-result tests green.

- [ ] **Step 6: Run the existing probe regression slice**

Run: `python -m pytest -q tests/test_spot_arbitrage_probe.py`

Expected: PASS. Existing session cleanup, duplicate exchange reuse, and order sizing tests should stay green.

- [ ] **Step 7: Commit the richer-result implementation**

```bash
git add app/runtime/spot_arbitrage_probe.py tests/test_spot_arbitrage_probe.py
git commit -m "feat: enrich spot probe execution results"
```

## Task 3: Verify Executor Compatibility With Richer Result

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Write the failing compatibility tests**

Add these async tests:

```python
@pytest.mark.asyncio
async def test_executor_marks_execution_result_open_hedged_with_rich_probe_fields():
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
            "buy_leg_status": "final_fetched",
            "sell_leg_status": "final_fetched",
            "buy_leg_error_code": None,
            "sell_leg_error_code": None,
            "failed_stage": None,
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
    assert repository.execution_results[0][1]["execution_status"] == "OPEN_HEDGED"


@pytest.mark.asyncio
async def test_executor_marks_execution_result_open_partial_with_rich_probe_fields():
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
            "buy_leg_status": "created",
            "sell_leg_status": "create_failed",
            "buy_leg_error_code": None,
            "sell_leg_error_code": "sell_create_failed",
            "failed_stage": "create_sell",
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
    assert repository.execution_results[0][1]["execution_status"] == "OPEN_PARTIAL"
    assert repository.execution_results[0][1]["repair_action"] == "AUTO_HEDGE_REPAIRING"
```

- [ ] **Step 2: Run the targeted live-worker compatibility tests to verify they fail or prove compatibility**

Run: `python -m pytest -q tests/test_live_workers.py -k "rich_probe_fields"`

Expected: either FAIL because the fake test scaffolding needs to accept richer result objects, or PASS immediately and prove no executor changes are required for compatibility.

If the tests pass immediately, do not modify production code in `live_workers.py`. Keep the work limited to the compatibility proof and continue to Step 4.

- [ ] **Step 3: If needed, make the minimal compatibility fix**

Only if Step 2 fails, make the smallest change necessary in `tests/test_live_workers.py` or `app/runtime/live_workers.py` so executor still reads:

```python
execution_status = getattr(result, "execution_status", None)
filled_exchanges = list(getattr(result, "filled_exchanges", []) or [])
failed_exchanges = list(getattr(result, "failed_exchanges", []) or [])
```

Do not add new task-summary fields in this task. The compatibility proof only needs executor to keep consuming the existing summary subset.

- [ ] **Step 4: Run the targeted live-worker compatibility tests to verify they pass**

Run: `python -m pytest -q tests/test_live_workers.py -k "rich_probe_fields or execution_result_open_hedged or execution_result_open_partial or preflight_failure_does_not_write_execution_summary"`

Expected: PASS. Richer probe fields should not break executor summary writeback or preflight non-write behavior.

- [ ] **Step 5: Commit the compatibility slice if there were real changes**

```bash
git add tests/test_live_workers.py app/runtime/live_workers.py
git commit -m "test: verify executor compatibility with rich probe results"
```

Expected: only run this commit if Task 3 introduced real changes.

## Task 4: Run Focused Regression And Syntax Checks

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\spot_arbitrage_probe.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_spot_arbitrage_probe.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Run the focused regression slice**

Run: `python -m pytest -q tests/test_spot_arbitrage_probe.py tests/test_live_workers.py`

Expected: PASS. This validates richer probe results plus executor compatibility together.

- [ ] **Step 2: Run syntax checks on touched modules**

Run: `python -m py_compile app/runtime/spot_arbitrage_probe.py tests/test_spot_arbitrage_probe.py tests/test_live_workers.py`

Expected: PASS with no output.

- [ ] **Step 3: Check the working tree before handoff**

Run: `git status --short`

Expected: show only the intended implementation/test changes before the final cleanup commit, or show a clean tree if previous tasks already committed everything.

- [ ] **Step 4: If Task 4 required cleanup edits, commit them**

```bash
git add app/runtime/spot_arbitrage_probe.py tests/test_spot_arbitrage_probe.py tests/test_live_workers.py
git commit -m "test: finalize spot probe rich result regressions"
```

Expected: only run this commit if the focused regression exposed a real mismatch that required a follow-up fix.
