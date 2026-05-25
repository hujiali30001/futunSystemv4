# Executor Worker Trade Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the executor worker’s default probe-based execution path with a runtime trade execution service that drives `TradeExecutor` while preserving execution summary persistence and `executor.execution_result` compatibility.

**Architecture:** Add one focused runtime adapter service that turns executor payload plus account truth into `ExecutionTask` and adapter mappings for `TradeExecutor`. Keep `RedisOpportunityDispatcher` as the existing call shell, wire the new service only into `build_executor_worker()`, and verify that `RedisExecutionTaskConsumer` still writes execution summaries and emits execution-result events from the new minimal result shape.

**Tech Stack:** Python 3.10+, pytest, pytest-asyncio, Redis Streams executor runtime, `TradeExecutor`, `ExecutionTask`, `ExchangeAdapter`, existing worker assembly and event pipeline

---

## File Structure

- Create: `d:\old\FuRunSystemV4\app\runtime\trade_execution_service.py`
  - Add `RuntimeTradeExecutionService`
  - Build `ExecutionTask` and `ExchangeAdapter` mappings from runtime payload and account truth
  - Return a minimal executor-compatible result object
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
  - Add a dedicated `trade_execution_service` field to `WorkerApplication`
  - Switch only `build_executor_worker()` from probe to the new service
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`
  - Lock the executor worker wiring change without regressing scanner/consumer probe usage
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - Verify execution summary persistence and `executor.execution_result` compatibility with the new minimal result object
- Create: `d:\old\FuRunSystemV4\tests\test_trade_execution_service.py`
  - Add focused tests for `OPEN_HEDGED` and `OPEN_PARTIAL`

## Task 1: Add Failing Tests For The Runtime Trade Execution Service

**Files:**
- Create: `d:\old\FuRunSystemV4\tests\test_trade_execution_service.py`

- [ ] **Step 1: Write the first failing success-path test**

Create this file with the first test:

```python
import pytest

from app.runtime.trade_execution_service import RuntimeTradeExecutionService


class FakeAdapter:
    def __init__(self, exchange: str, *, bid: float, ask: float, fail_create: bool = False):
        self.exchange = exchange
        self.bid = bid
        self.ask = ask
        self.fail_create = fail_create
        self.created_requests = []
        self.closed = False

    async def fetch_ticker(self, symbol: str) -> dict[str, float | str]:
        return {"symbol": symbol, "bid": self.bid, "ask": self.ask, "last": (self.bid + self.ask) / 2}

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        _ = symbol
        return round(amount, 6)

    def price_to_precision(self, symbol: str, price: float) -> float:
        _ = symbol
        return round(price, 6)

    async def create_order(self, order_request):
        self.created_requests.append(order_request)
        if self.fail_create:
            raise RuntimeError(f"{self.exchange} create failed")
        return {"id": f"{self.exchange}-1"}

    async def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, exchange: str, *, bid: float, ask: float, fail_create: bool = False):
        self.exchange = exchange
        self.bid = bid
        self.ask = ask
        self.fail_create = fail_create
        self.mark_ready_calls = 0
        self.closed = False
        self.markets = {
            "BTC/USDT": {
                "limits": {"amount": {"min": 0.001}},
            }
        }

    async def mark_ready(self) -> None:
        self.mark_ready_calls += 1


class FakeSessionFactory:
    def __init__(self, configs: dict[str, dict[str, float | bool]]) -> None:
        self.configs = configs
        self.sessions = {}

    def create_session(self, *, exchange: str, env_mode: str, proxies: dict, credentials: object):
        _ = env_mode, proxies, credentials
        config = self.configs[exchange]
        session = FakeSession(
            exchange,
            bid=float(config["bid"]),
            ask=float(config["ask"]),
            fail_create=bool(config.get("fail_create", False)),
        )
        self.sessions[exchange] = session
        return session


@pytest.mark.asyncio
async def test_runtime_trade_execution_service_returns_open_hedged_for_two_successful_legs(monkeypatch):
    adapters = {}

    def build_adapter(session):
        adapter = FakeAdapter(
            session.exchange,
            bid=session.bid,
            ask=session.ask,
            fail_create=session.fail_create,
        )
        adapters[session.exchange] = adapter
        return adapter

    monkeypatch.setattr(
        "app.runtime.trade_execution_service.ExchangeAdapter",
        build_adapter,
    )
    service = RuntimeTradeExecutionService(
        session_factory=FakeSessionFactory(
            {
                "okx": {"bid": 100.0, "ask": 101.0},
                "gate": {"bid": 103.0, "ask": 104.0},
            }
        )
    )

    result = await service.run_task(
        exchanges=["okx", "gate"],
        credentials_by_exchange={"okx": object(), "gate": object()},
        execution_accounts_by_exchange={"okx": object(), "gate": object()},
        symbol="BTC/USDT",
        target_quote_amount=40.0,
        env_mode="testnet",
        proxies_by_exchange={"okx": {}, "gate": {}},
    )

    assert result.ok is True
    assert result.execution_status == "OPEN_HEDGED"
    assert result.filled_exchanges == ["okx", "gate"]
    assert result.failed_exchanges == []
    assert adapters["okx"].closed is True
    assert adapters["gate"].closed is True
```

- [ ] **Step 2: Run the first test to verify it fails**

Run:

```bash
python -m pytest -q tests/test_trade_execution_service.py::test_runtime_trade_execution_service_returns_open_hedged_for_two_successful_legs
```

Expected: FAIL because `app.runtime.trade_execution_service` does not exist yet.

- [ ] **Step 3: Add the second failing partial-failure test**

Append this test:

```python
@pytest.mark.asyncio
async def test_runtime_trade_execution_service_returns_open_partial_when_one_leg_fails(monkeypatch):
    def build_adapter(session):
        return FakeAdapter(
            session.exchange,
            bid=session.bid,
            ask=session.ask,
            fail_create=session.fail_create,
        )

    monkeypatch.setattr(
        "app.runtime.trade_execution_service.ExchangeAdapter",
        build_adapter,
    )
    service = RuntimeTradeExecutionService(
        session_factory=FakeSessionFactory(
            {
                "okx": {"bid": 100.0, "ask": 101.0},
                "gate": {"bid": 103.0, "ask": 104.0, "fail_create": True},
            }
        )
    )

    result = await service.run_task(
        exchanges=["okx", "gate"],
        credentials_by_exchange={"okx": object(), "gate": object()},
        execution_accounts_by_exchange={"okx": object(), "gate": object()},
        symbol="BTC/USDT",
        target_quote_amount=40.0,
        env_mode="testnet",
        proxies_by_exchange={"okx": {}, "gate": {}},
    )

    assert result.ok is False
    assert result.execution_status == "OPEN_PARTIAL"
    assert result.filled_exchanges == ["okx"]
    assert result.failed_exchanges == ["gate"]
```

- [ ] **Step 4: Run both tests to verify they fail for the expected reason**

Run:

```bash
python -m pytest -q tests/test_trade_execution_service.py
```

Expected: FAIL because the runtime trade execution service module is still missing.

## Task 2: Implement The Runtime Trade Execution Service And Wire It Into The Executor Worker

**Files:**
- Create: `d:\old\FuRunSystemV4\app\runtime\trade_execution_service.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Create the runtime trade execution service**

Create `app/runtime/trade_execution_service.py` with this minimal implementation:

```python
from __future__ import annotations

from dataclasses import dataclass

from app.exchanges.adapters import ExchangeAdapter
from app.exchanges.session_manager import ExchangeClientFactory, ExchangeCredentials
from app.trading.executor import TradeExecutor
from app.trading.tasks import ExecutionLeg, ExecutionTask


@dataclass(slots=True)
class RuntimeExecutionResult:
    ok: bool
    execution_status: str | None
    filled_exchanges: list[str]
    failed_exchanges: list[str]


class RuntimeTradeExecutionService:
    def __init__(self, session_factory: ExchangeClientFactory | None = None) -> None:
        self.session_factory = session_factory or ExchangeClientFactory()

    async def run_task(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict[str, ExchangeCredentials],
        execution_accounts_by_exchange: dict | None = None,
        symbol: str,
        target_quote_amount: float = 15.0,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> RuntimeExecutionResult:
        _ = execution_accounts_by_exchange
        unique_exchanges = list(dict.fromkeys(exchanges))
        sessions = {}
        adapters = {}
        try:
            for exchange in unique_exchanges:
                session = self.session_factory.create_session(
                    exchange=exchange,
                    env_mode=env_mode,
                    proxies=(proxies_by_exchange or {}).get(exchange, {}),
                    credentials=credentials_by_exchange[exchange],
                )
                await session.mark_ready()
                sessions[exchange] = session
                adapters[exchange] = ExchangeAdapter(session)

            tickers = {
                exchange: await adapters[exchange].fetch_ticker(symbol)
                for exchange in unique_exchanges
            }
            buy_exchange = min(unique_exchanges, key=lambda name: tickers[name]["ask"])
            sell_exchange = max(unique_exchanges, key=lambda name: tickers[name]["bid"])

            buy_amount = adapters[buy_exchange].amount_to_precision(
                symbol,
                self._build_safe_amount(
                    sessions[buy_exchange].markets[symbol],
                    tickers[buy_exchange],
                    target_quote_amount=target_quote_amount,
                ),
            )
            sell_amount = adapters[sell_exchange].amount_to_precision(
                symbol,
                self._build_safe_amount(
                    sessions[sell_exchange].markets[symbol],
                    tickers[sell_exchange],
                    target_quote_amount=target_quote_amount,
                ),
            )
            buy_price = adapters[buy_exchange].price_to_precision(
                symbol,
                float(tickers[buy_exchange]["bid"]) * 0.95,
            )
            sell_price = adapters[sell_exchange].price_to_precision(
                symbol,
                float(tickers[sell_exchange]["ask"]) * 1.05,
            )

            task = ExecutionTask(
                task_id=f"{buy_exchange}:{sell_exchange}:{symbol}",
                symbol=symbol,
                open_legs=[
                    ExecutionLeg(
                        exchange=buy_exchange,
                        side="buy",
                        order_type="limit",
                        amount=float(buy_amount),
                        price=float(buy_price),
                    ),
                    ExecutionLeg(
                        exchange=sell_exchange,
                        side="sell",
                        order_type="limit",
                        amount=float(sell_amount),
                        price=float(sell_price),
                    ),
                ],
            )
            executor = TradeExecutor(adapter_factory=adapters)
            result = await executor.execute_open(task)
            return RuntimeExecutionResult(
                ok=result.status == "OPEN_HEDGED",
                execution_status=result.status,
                filled_exchanges=list(result.filled_exchanges),
                failed_exchanges=list(result.failed_exchanges),
            )
        finally:
            for adapter in adapters.values():
                await adapter.close()

    @staticmethod
    def _build_safe_amount(
        market: dict,
        ticker: dict,
        *,
        target_quote_amount: float,
    ) -> float:
        min_amount = market.get("limits", {}).get("amount", {}).get("min") or 0.0001
        reference_price = ticker.get("bid") or ticker.get("last") or ticker.get("ask") or 1.0
        requested_amount = float(target_quote_amount) / float(reference_price)
        return max(float(min_amount), requested_amount)
```

- [ ] **Step 2: Re-run the focused runtime service tests to verify they pass**

Run:

```bash
python -m pytest -q tests/test_trade_execution_service.py
```

Expected: PASS.

- [ ] **Step 3: Add the worker wiring regression test**

In `tests/test_worker_service.py`, add this test:

```python
def test_build_executor_worker_uses_trade_execution_service():
    settings = WorkerSettings(
        redis_url="redis://localhost:6379/0",
        worker_role="executor",
        executor_node_id="node-a",
    )
    app = WorkerApplication(
        settings=settings,
        event_router=FakeEventRouter(),
    )

    worker = app.build_executor_worker(redis_client=object())

    assert worker.consumer.dispatcher.spot_service is app.trade_execution_service
    assert worker.consumer.dispatcher.spot_service is not app.spot_service
```

- [ ] **Step 4: Add the executor main-path compatibility test**

In `tests/test_live_workers.py`, add this test:

```python
@pytest.mark.asyncio
async def test_executor_marks_execution_result_with_runtime_trade_execution_service_result():
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
    service = type(
        "RuntimeTradeExecutionServiceResultStub",
        (),
        {
            "run_task": staticmethod(
                lambda **kwargs: None
            ),
        },
    )()
```

Then replace the stub body with an async result object:

```python
    async def _run_task(**kwargs):
        _ = kwargs
        return type(
            "ExecutionSummary",
            (),
            {
                "ok": True,
                "execution_status": "OPEN_HEDGED",
                "filled_exchanges": ["okx", "gate"],
                "failed_exchanges": [],
            },
        )()

    service.run_task = _run_task
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
    assert repository.execution_result_calls[0]["execution_status"] == "OPEN_HEDGED"
    event = _find_event(router.events, "executor.execution_result")
    assert event.payload["execution_status"] == "OPEN_HEDGED"
    assert event.payload["filled_exchanges"] == ["okx", "gate"]
    assert event.payload["failed_exchanges"] == []
    assert event.payload["buy_leg_status"] is None
```

- [ ] **Step 5: Switch the executor worker wiring in `worker_service.py`**

Make these edits:

1. Add the import:

```python
from app.runtime.trade_execution_service import RuntimeTradeExecutionService
```

2. Extend `WorkerApplication` fields:

```python
    trade_execution_service: RuntimeTradeExecutionService = field(
        default_factory=RuntimeTradeExecutionService
    )
```

3. Update `build_executor_worker()`:

```python
    def build_executor_worker(self, *, redis_client: Redis) -> ConsumerWorker:
        dispatcher = RedisOpportunityDispatcher(self.trade_execution_service)
```

Keep `build_consumer_worker()` unchanged so it still uses `self.spot_service`.

- [ ] **Step 6: Run the focused wiring and compatibility tests**

Run:

```bash
python -m pytest -q tests/test_trade_execution_service.py tests/test_worker_service.py tests/test_live_workers.py -k "trade_execution_service or build_executor_worker_uses_trade_execution_service or runtime_trade_execution_service_result"
```

Expected: PASS.

- [ ] **Step 7: Commit the implementation slice**

```bash
git add app/runtime/trade_execution_service.py app/runtime/worker_service.py tests/test_trade_execution_service.py tests/test_worker_service.py tests/test_live_workers.py
git commit -m "feat: wire executor worker to trade executor"
```

## Task 3: Run Focused Regression And Final Checks

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\trade_execution_service.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_trade_execution_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Re-run the executor event and summary regression slice**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "execution_result or write_execution_summary or preflight_failure"
```

Expected: PASS. The new runtime service must not break execution summaries or execution-result events.

- [ ] **Step 2: Re-run the worker assembly regression file**

Run:

```bash
python -m pytest -q tests/test_worker_service.py
```

Expected: PASS. Scanner/consumer probe wiring and executor worker assembly remain green.

- [ ] **Step 3: Re-run the new runtime service file**

Run:

```bash
python -m pytest -q tests/test_trade_execution_service.py
```

Expected: PASS.

- [ ] **Step 4: Run syntax checks on all touched modules**

Run:

```bash
python -m py_compile app/runtime/trade_execution_service.py app/runtime/worker_service.py tests/test_trade_execution_service.py tests/test_worker_service.py tests/test_live_workers.py
```

Expected: PASS with no output.

- [ ] **Step 5: Check the working tree before handoff**

Run:

```bash
git status --short
```

Expected: show only the intended tracked file changes before the final handoff, or a clean tree if the implementation commit already captured everything.

- [ ] **Step 6: If Step 1-5 exposed a real follow-up fix, commit it**

```bash
git add app/runtime/trade_execution_service.py app/runtime/worker_service.py tests/test_trade_execution_service.py tests/test_worker_service.py tests/test_live_workers.py
git commit -m "test: finalize executor worker trade executor wiring"
```

Expected: skip this commit if no follow-up fix was needed.
