# Executor Preflight / Account Truth / Repair Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the current `dispatcher -> executor -> repair` chain so executor uses dispatcher-selected exchanges deterministically, account-truth/preflight boundaries become stricter, and single-shot repair results become more explicit and easier to close out.

**Architecture:** Keep the work narrowly scoped to the existing runtime layer. First lock executor execution semantics with red tests and make `RuntimeTradeExecutionService` follow payload-selected `buy_exchange / sell_exchange`; next strengthen executor preflight and account-truth consistency checks with stable failure codes; finally tighten repair result semantics and regression coverage so the minimal repair path is more production-ready without expanding into a full repair strategy engine.

**Tech Stack:** Python 3.10+, pytest, asyncio, Redis-style workers, runtime services in `app/runtime`, existing fake worker test doubles

---

## File Structure

- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
  - Make dispatcher pass explicit `buy_exchange` / `sell_exchange` into the runtime trade execution service
- Modify: `d:\old\FuRunSystemV4\app\runtime\trade_execution_service.py`
  - Stop re-selecting buy/sell exchanges from ticker spreads and execute strictly on the dispatcher-selected edges
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - Strengthen executor preflight and account-truth consistency checks while preserving current event flow
- Modify: `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
  - Tighten stable failure boundaries only if needed to support new preflight/account-truth assertions
- Modify: `d:\old\FuRunSystemV4\app\runtime\repair_execution_service.py`
  - Make repair result semantics clearer without expanding the repair strategy scope
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - Add focused worker-chain regressions for deterministic executor routing, stricter preflight/account-truth checks, and repair closeout semantics
- Modify: `d:\old\FuRunSystemV4\tests\test_repair_execution_service.py`
  - Add focused unit coverage for tightened repair result semantics

## Task 1: Make Executor Obey Dispatcher-Selected Exchanges

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\trade_execution_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Write the failing worker-chain regression**

Add this test near the existing executor-focused tests in `tests/test_live_workers.py`:

```python
@pytest.mark.asyncio
async def test_executor_runtime_trade_service_keeps_payload_selected_exchange_order():
    class RecordingTradeService:
        def __init__(self) -> None:
            self.calls = []

        async def run_task(
            self,
            *,
            buy_exchange,
            sell_exchange,
            exchanges,
            credentials_by_exchange,
            execution_accounts_by_exchange=None,
            symbol,
            target_quote_amount=15.0,
            env_mode="testnet",
            proxies_by_exchange=None,
        ):
            self.calls.append(
                {
                    "buy_exchange": buy_exchange,
                    "sell_exchange": sell_exchange,
                    "exchanges": list(exchanges),
                    "symbol": symbol,
                    "target_quote_amount": target_quote_amount,
                }
            )
            return type(
                "ExecutionSummary",
                (),
                {
                    "ok": True,
                    "execution_status": "OPEN_HEDGED",
                    "filled_exchanges": [buy_exchange, sell_exchange],
                    "failed_exchanges": [],
                },
            )()

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
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = RecordingTradeService()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"bitget": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert service.calls == [
        {
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
            "exchanges": ["bitget", "gate"],
            "symbol": "BTC/USDT",
            "target_quote_amount": 40.0,
        }
    ]
```

- [ ] **Step 2: Write the failing direct unit test for the trade execution service**

Add this test into `tests/test_live_workers.py` near other runtime-service-like helpers, or move it into a dedicated nearby test file if there is already a pattern:

```python
@pytest.mark.asyncio
async def test_runtime_trade_execution_service_uses_payload_selected_exchanges_without_reselecting():
    class FakeClient:
        def __init__(self, exchange: str) -> None:
            self.exchange = exchange
            self.markets = {"BTC/USDT": {"limits": {"amount": {"min": 0.0001}}}}

        async def mark_ready(self) -> None:
            return None

    class FakeAdapter:
        def __init__(self, session) -> None:
            self.session = session

        async def fetch_ticker(self, symbol: str) -> dict:
            if self.session.exchange == "bitget":
                return {"symbol": symbol, "bid": 100.0, "ask": 101.0, "last": 100.5}
            return {"symbol": symbol, "bid": 200.0, "ask": 201.0, "last": 200.5}

        def amount_to_precision(self, symbol: str, amount: float) -> float:
            return amount

        def price_to_precision(self, symbol: str, price: float) -> float:
            return price

        async def close(self) -> None:
            return None

    class FakeSessionFactory:
        def create_session(self, *, exchange, env_mode, proxies, credentials):
            _ = env_mode, proxies, credentials
            return FakeClient(exchange)

    captured = {}

    class RecordingExecutor:
        def __init__(self, adapter_factory) -> None:
            captured["adapter_factory_keys"] = list(adapter_factory.keys())

        async def execute_open(self, task):
            captured["open_legs"] = [
                (leg.exchange, leg.side, leg.amount, leg.price) for leg in task.open_legs
            ]
            return type(
                "Result",
                (),
                {
                    "status": "OPEN_HEDGED",
                    "filled_exchanges": ["bitget", "gate"],
                    "failed_exchanges": [],
                },
            )()

    service = RuntimeTradeExecutionService(session_factory=FakeSessionFactory())
```

Then monkeypatch within the test:

```python
    monkeypatch.setattr("app.runtime.trade_execution_service.ExchangeAdapter", FakeAdapter)
    monkeypatch.setattr("app.runtime.trade_execution_service.TradeExecutor", RecordingExecutor)

    result = await service.run_task(
        buy_exchange="bitget",
        sell_exchange="gate",
        exchanges=["bitget", "gate"],
        credentials_by_exchange={"bitget": object(), "gate": object()},
        symbol="BTC/USDT",
        target_quote_amount=40.0,
        env_mode="testnet",
    )

    assert result.execution_status == "OPEN_HEDGED"
    assert captured["adapter_factory_keys"] == ["bitget", "gate"]
    assert captured["open_legs"][0][0] == "bitget"
    assert captured["open_legs"][0][1] == "buy"
    assert captured["open_legs"][1][0] == "gate"
    assert captured["open_legs"][1][1] == "sell"
```

- [ ] **Step 3: Run the focused tests and verify red**

Run:

```bash
python -m pytest tests/test_live_workers.py -k "payload_selected_exchange_order or uses_payload_selected_exchanges_without_reselecting" -q
```

Expected:

- at least one test FAILS because `RedisOpportunityDispatcher` does not yet pass explicit `buy_exchange / sell_exchange`, and `RuntimeTradeExecutionService` still reselects edges internally

- [ ] **Step 4: Implement the minimal production changes**

In `app/runtime/redis_flow.py`, update `RedisOpportunityDispatcher.dispatch()` from:

```python
return await self.spot_service.run_task(
    exchanges=exchanges,
    credentials_by_exchange=credentials_by_exchange,
    symbol=payload["symbol"],
    target_quote_amount=target_quote_amount,
    env_mode="testnet",
    proxies_by_exchange=proxies_by_exchange,
)
```

to:

```python
return await self.spot_service.run_task(
    buy_exchange=payload["buy_exchange"],
    sell_exchange=payload["sell_exchange"],
    exchanges=exchanges,
    credentials_by_exchange=credentials_by_exchange,
    execution_accounts_by_exchange=execution_accounts_by_exchange,
    symbol=payload["symbol"],
    target_quote_amount=target_quote_amount,
    env_mode="testnet",
    proxies_by_exchange=proxies_by_exchange,
)
```

In `app/runtime/trade_execution_service.py`, update the method signature:

```python
async def run_task(
    self,
    *,
    buy_exchange: str,
    sell_exchange: str,
    exchanges: list[str],
    credentials_by_exchange: dict[str, ExchangeCredentials],
    execution_accounts_by_exchange: dict[str, Any] | None = None,
    symbol: str,
    target_quote_amount: float = 15.0,
    env_mode: str = "testnet",
    proxies_by_exchange: dict[str, dict[str, str]] | None = None,
) -> RuntimeExecutionResult:
```

Replace the current ticker-based re-selection block:

```python
tickers = {
    exchange: await adapters[exchange].fetch_ticker(symbol)
    for exchange in unique_exchanges
}
buy_exchange = min(unique_exchanges, key=lambda name: tickers[name]["ask"])
sell_exchange = max(unique_exchanges, key=lambda name: tickers[name]["bid"])
```

with:

```python
tickers = {
    exchange: await adapters[exchange].fetch_ticker(symbol)
    for exchange in unique_exchanges
}
if buy_exchange not in unique_exchanges or sell_exchange not in unique_exchanges:
    raise RuntimeError("payload selected exchanges are not available in execution set")
```

Keep the rest of the quantity/price calculation and `ExecutionTask` construction unchanged.

- [ ] **Step 5: Re-run the focused tests and commit**

Run:

```bash
python -m pytest tests/test_live_workers.py -k "payload_selected_exchange_order or uses_payload_selected_exchanges_without_reselecting" -q
python -m py_compile app/runtime/redis_flow.py app/runtime/trade_execution_service.py tests/test_live_workers.py
git add app/runtime/redis_flow.py app/runtime/trade_execution_service.py tests/test_live_workers.py
git commit -m "fix: keep executor exchange selection deterministic"
```

Expected:

- focused tests PASS
- syntax check PASS
- one commit created for deterministic executor exchange selection

## Task 2: Strengthen Preflight And Account-Truth Consistency

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Write the failing consistency tests**

Add focused tests in `tests/test_live_workers.py` for these cases:

```python
@pytest.mark.asyncio
async def test_executor_preflight_fails_when_resolved_accounts_do_not_match_payload_exchanges():
    ...
    assert repository.failed[-1] == (
        "task-1",
        "executor_preflight_account_exchange_mismatch",
    )
```

```python
@pytest.mark.asyncio
async def test_executor_preflight_fails_when_bound_account_ids_do_not_match_resolved_accounts():
    ...
    assert repository.failed[-1] == (
        "task-1",
        "executor_preflight_account_resolution_failed",
    )
```

```python
@pytest.mark.asyncio
async def test_executor_preflight_fails_when_execution_exchange_order_is_not_buy_then_sell():
    ...
    assert repository.failed[-1] == (
        "task-1",
        "executor_preflight_account_exchange_mismatch",
    )
```

Use a small fake account-truth resolver stub inside the test body to return mismatched exchange keys or mismatched `account_id` values.

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
python -m pytest tests/test_live_workers.py -k "do_not_match_payload_exchanges or do_not_match_resolved_accounts or not_buy_then_sell" -q
```

Expected: FAIL because current preflight is not yet strict enough.

- [ ] **Step 3: Implement the minimal preflight/account-truth guard**

In `app/runtime/live_workers.py`, extend the existing preflight validator area so that when `execution_accounts_by_exchange` is available:

```python
if list(execution_accounts_by_exchange.keys()) != [buy_exchange, sell_exchange]:
    raise ExecutorPreflightError(
        "executor_preflight_account_exchange_mismatch",
        detail="resolved execution accounts do not match payload exchange order",
    )
```

And when payload carries bound account IDs:

```python
if str(payload.get("buy_account_id", "")) and (
    str(execution_accounts_by_exchange[buy_exchange].account_id)
    != str(payload["buy_account_id"])
):
    raise ExecutorPreflightError(
        "executor_preflight_account_resolution_failed",
        detail="resolved buy account does not match payload buy_account_id",
    )
if str(payload.get("sell_account_id", "")) and (
    str(execution_accounts_by_exchange[sell_exchange].account_id)
    != str(payload["sell_account_id"])
):
    raise ExecutorPreflightError(
        "executor_preflight_account_resolution_failed",
        detail="resolved sell account does not match payload sell_account_id",
    )
```

Only touch `executor_account_truth.py` if a stable reason split is required to keep the new tests clean; otherwise leave its logic unchanged.

- [ ] **Step 4: Re-run the focused tests and nearby regression**

Run:

```bash
python -m pytest tests/test_live_workers.py -k "do_not_match_payload_exchanges or do_not_match_resolved_accounts or not_buy_then_sell or account_binding" -q
python -m py_compile app/runtime/live_workers.py app/runtime/executor_account_truth.py tests/test_live_workers.py
```

Expected:

- new focused tests PASS
- existing binding-related tests still PASS
- syntax check PASS

- [ ] **Step 5: Commit the stricter preflight/account-truth guard**

```bash
git add app/runtime/live_workers.py app/runtime/executor_account_truth.py tests/test_live_workers.py
git commit -m "fix: tighten executor account truth preflight checks"
```

Expected: one commit containing the minimal consistency hardening.

## Task 3: Clarify Single-Shot Repair Closeout

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\repair_execution_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_repair_execution_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Write the failing repair-focused tests**

In `tests/test_repair_execution_service.py`, add:

```python
@pytest.mark.asyncio
async def test_runtime_repair_execution_service_keeps_only_non_repaired_targets_in_remaining_failed_exchanges():
    service = RuntimeRepairExecutionService(session_factory=FakeSessionFactory(FakeClient()))

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

    assert result.repaired_exchanges == ["gate"]
    assert result.remaining_failed_exchanges == []
```

And add a failure-path assertion:

```python
@pytest.mark.asyncio
async def test_runtime_repair_execution_service_reports_only_target_exchange_as_remaining_when_repair_fails():
    ...
    assert result.repaired_exchanges == []
    assert result.remaining_failed_exchanges == ["gate"]
    assert result.reason == "repair order failed"
```

In `tests/test_live_workers.py`, add one worker-chain regression:

```python
@pytest.mark.asyncio
async def test_repair_finished_event_keeps_remaining_failed_exchanges_after_failed_repair():
    ...
    event = _find_event(router.events, "repair.task.finished")
    assert event.payload["remaining_failed_exchanges"] == ["gate"]
    assert event.payload["status"] == "MANUAL_REQUIRED"
```

- [ ] **Step 2: Run the focused repair tests and verify red if needed**

Run:

```bash
python -m pytest tests/test_repair_execution_service.py tests/test_live_workers.py -k "remaining_failed_exchanges_after_failed_repair or reports_only_target_exchange_as_remaining or keeps_only_non_repaired_targets" -q
```

Expected:

- if current behavior already matches, note that explicitly and treat this as a specification-lock step
- if any test fails, continue to Step 3 as a true red-green fix

- [ ] **Step 3: Implement the minimal repair closeout tightening**

If tests fail, update `app/runtime/repair_execution_service.py` minimally so the return values remain strictly tied to the targeted failed exchange:

```python
target_exchange = target_exchanges[0]
remaining_failed_exchanges = [target_exchange]
```

and on success:

```python
remaining_failed_exchanges=[]
repaired_exchanges=[target_exchange]
```

On failure keep:

```python
repaired_exchanges=[]
remaining_failed_exchanges=[target_exchange]
reason=str(exc)
```

Do not add retries, loops, or multi-target logic in this task.

- [ ] **Step 4: Run focused repair tests plus adjacent worker regression**

Run:

```bash
python -m pytest tests/test_repair_execution_service.py tests/test_live_workers.py -k "repair_worker or repair_execution_service or remaining_failed_exchanges" -q
python -m py_compile app/runtime/repair_execution_service.py tests/test_repair_execution_service.py tests/test_live_workers.py
```

Expected: PASS with no syntax errors.

- [ ] **Step 5: Commit the repair closeout hardening**

```bash
git add app/runtime/repair_execution_service.py tests/test_repair_execution_service.py tests/test_live_workers.py
git commit -m "fix: clarify repair result closeout semantics"
```

Expected: one commit containing the minimal repair closeout improvement.

## Task 4: Final Regression And Handoff

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\trade_execution_service.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\repair_execution_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_repair_execution_service.py`

- [ ] **Step 1: Run the broader targeted suite**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_worker_service.py tests/test_repair_execution_service.py tests/test_task_repository.py tests/test_executor_account_truth.py -q
```

Expected: PASS with no new regressions around dispatcher/executor/repair/account-truth behavior.

- [ ] **Step 2: Check working tree and recent commits**

Run:

```bash
git status -sb
git log -6 --oneline
```

Expected:

- working tree is clean
- latest commits include:
  - `fix: keep executor exchange selection deterministic`
  - `fix: tighten executor account truth preflight checks`
  - `fix: clarify repair result closeout semantics`

- [ ] **Step 3: Record the remote-readiness note**

Record this exact handoff note:

```text
This hardening pass does not require a new fake canary before push.
After push, the highest-value remote follow-up is a minimal real-path executor/restart validation to confirm deterministic exchange selection and quieter repairable OPEN_PARTIAL behavior under the deployed runtime.
```

- [ ] **Step 4: Summarize push readiness**

Record these facts in the implementation handoff:

```text
- executor now uses dispatcher-selected buy/sell exchanges deterministically
- preflight rejects mismatched execution accounts more strictly
- account-truth/binding consistency is tighter at executor entry
- repair result semantics are clearer without expanding repair scope
- runtime hardening stays scoped to executor/account-truth/repair and does not widen into funding/derivatives work
```
