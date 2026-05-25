# Executor Preflight Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a unified executor preflight validation layer that rejects invalid execution payloads before real dispatch and persists stable failure reason codes.

**Architecture:** Keep the change local to `app/runtime/live_workers.py` by introducing a small `ExecutorPreflightError` plus `ExecutorPreflightValidator`, then invoke it from `RedisExecutionTaskConsumer` after control-rule evaluation and account-truth resolution. Cover the validator with direct unit tests and cover the consumer integration with targeted async regression tests so the existing task-account-binding success path stays intact.

**Tech Stack:** Python 3.10+, pytest, pytest-asyncio, Redis Streams worker flow, SQLAlchemy-backed task repository, existing executor account truth resolver

---

## File Structure

- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - Add `ExecutorPreflightError`
  - Add `ExecutorPreflightValidator`
  - Wire validator into `RedisExecutionTaskConsumer.__init__()` and `run()`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - Add validator unit tests for all new reason codes
  - Add async executor-consumer integration tests proving failed preflight skips dispatch and persists stable reasons
- Reuse for regression context:
  - `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
  - `d:\old\FuRunSystemV4\tests\test_live_workers.py`

## Task 1: Add Validator Unit Tests And Minimal Validator

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`

- [ ] **Step 1: Write the failing validator tests**

```python
from app.runtime.live_workers import (
    ContinuousSpotScanner,
    ExecutorPreflightError,
    ExecutorPreflightValidator,
    RedisExecutionTaskConsumer,
    RedisNodeTaskDispatcher,
    RedisSpotConsumer,
    _evaluate_account_exchange_coverage,
    _normalize_account_region,
    _parse_market_type_scope,
)


def test_executor_preflight_validator_rejects_missing_required_fields():
    validator = ExecutorPreflightValidator()

    with pytest.raises(ExecutorPreflightError) as exc_info:
        validator.validate(
            payload={
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "okx",
            },
            execution_accounts_by_exchange=None,
        )

    assert exc_info.value.reason == "executor_preflight_invalid_payload"


def test_executor_preflight_validator_rejects_same_exchange():
    validator = ExecutorPreflightValidator()

    with pytest.raises(ExecutorPreflightError) as exc_info:
        validator.validate(
            payload={
                "task_uuid": "task-1",
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "okx",
                "sell_exchange": "okx",
                "target_quote_amount": "40.0",
            },
            execution_accounts_by_exchange=None,
        )

    assert exc_info.value.reason == "executor_preflight_same_exchange"


def test_executor_preflight_validator_rejects_invalid_amount():
    validator = ExecutorPreflightValidator()

    with pytest.raises(ExecutorPreflightError) as exc_info:
        validator.validate(
            payload={
                "task_uuid": "task-1",
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "okx",
                "sell_exchange": "gate",
                "target_quote_amount": "0",
            },
            execution_accounts_by_exchange=None,
        )

    assert exc_info.value.reason == "executor_preflight_invalid_amount"


def test_executor_preflight_validator_rejects_missing_binding_resolution():
    validator = ExecutorPreflightValidator()

    with pytest.raises(ExecutorPreflightError) as exc_info:
        validator.validate(
            payload={
                "task_uuid": "task-1",
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "okx",
                "sell_exchange": "gate",
                "buy_account_id": "101",
                "sell_account_id": "202",
                "target_quote_amount": "40.0",
            },
            execution_accounts_by_exchange={},
        )

    assert exc_info.value.reason == "executor_preflight_account_resolution_failed"


def test_executor_preflight_validator_rejects_account_exchange_mismatch():
    validator = ExecutorPreflightValidator()
    wrong_buy_account = type(
        "ResolvedAccount",
        (),
        {"exchange": "bitget", "credentials": "cred-a", "proxies": {}},
    )()
    sell_account = type(
        "ResolvedAccount",
        (),
        {"exchange": "gate", "credentials": "cred-b", "proxies": {}},
    )()

    with pytest.raises(ExecutorPreflightError) as exc_info:
        validator.validate(
            payload={
                "task_uuid": "task-1",
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "okx",
                "sell_exchange": "gate",
                "buy_account_id": "101",
                "sell_account_id": "202",
                "target_quote_amount": "40.0",
            },
            execution_accounts_by_exchange={
                "okx": wrong_buy_account,
                "gate": sell_account,
            },
        )

    assert exc_info.value.reason == "executor_preflight_account_exchange_mismatch"
```

- [ ] **Step 2: Run the validator tests to verify they fail**

```bash
pytest -q tests/test_live_workers.py -k "executor_preflight_validator"
```

Expected: FAIL because `ExecutorPreflightError` and `ExecutorPreflightValidator` do not exist yet in `app/runtime/live_workers.py`.

- [ ] **Step 3: Write the minimal validator implementation**

```python
class ExecutorPreflightError(RuntimeError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class ExecutorPreflightValidator:
    required_fields = (
        "task_uuid",
        "user_id",
        "symbol",
        "buy_exchange",
        "sell_exchange",
    )

    def validate(
        self,
        *,
        payload: dict[str, Any],
        execution_accounts_by_exchange: dict[str, Any] | None,
    ) -> None:
        for field in self.required_fields:
            raw_value = payload.get(field)
            if raw_value is None or str(raw_value).strip() == "":
                raise ExecutorPreflightError(
                    "executor_preflight_invalid_payload",
                    f"missing required field: {field}",
                )

        buy_exchange = str(payload["buy_exchange"])
        sell_exchange = str(payload["sell_exchange"])
        if buy_exchange == sell_exchange:
            raise ExecutorPreflightError(
                "executor_preflight_same_exchange",
                "buy_exchange and sell_exchange must differ",
            )

        try:
            target_quote_amount = float(payload.get("target_quote_amount", "0"))
        except (TypeError, ValueError) as exc:
            raise ExecutorPreflightError(
                "executor_preflight_invalid_amount",
                "target_quote_amount must be a positive number",
            ) from exc
        if target_quote_amount <= 0:
            raise ExecutorPreflightError(
                "executor_preflight_invalid_amount",
                "target_quote_amount must be a positive number",
            )

        if (
            payload.get("buy_account_id") is not None
            and payload.get("sell_account_id") is not None
        ):
            if not execution_accounts_by_exchange:
                raise ExecutorPreflightError(
                    "executor_preflight_account_resolution_failed",
                    "binding payload requires resolved execution accounts",
                )
            for exchange in (buy_exchange, sell_exchange):
                if execution_accounts_by_exchange.get(exchange) is None:
                    raise ExecutorPreflightError(
                        "executor_preflight_account_resolution_failed",
                        f"missing resolved execution account for exchange={exchange}",
                    )

        if execution_accounts_by_exchange:
            for exchange in (buy_exchange, sell_exchange):
                resolved_account = execution_accounts_by_exchange.get(exchange)
                if resolved_account is None:
                    continue
                resolved_exchange = (
                    resolved_account.get("exchange")
                    if isinstance(resolved_account, dict)
                    else getattr(resolved_account, "exchange", None)
                )
                if resolved_exchange is None:
                    continue
                if str(resolved_exchange) != exchange:
                    raise ExecutorPreflightError(
                        "executor_preflight_account_exchange_mismatch",
                        f"resolved execution account exchange mismatch for {exchange}",
                    )
```

- [ ] **Step 4: Run the validator tests to verify they pass**

```bash
pytest -q tests/test_live_workers.py -k "executor_preflight_validator"
```

Expected: PASS with `5 passed` for the new validator tests.

- [ ] **Step 5: Commit the validator slice**

```bash
git add tests/test_live_workers.py app/runtime/live_workers.py
git commit -m "feat: add executor preflight validator"
```

## Task 2: Integrate Preflight Into RedisExecutionTaskConsumer

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`

- [ ] **Step 1: Write the failing executor-consumer integration tests**

```python
@pytest.mark.asyncio
async def test_executor_preflight_same_exchange_marks_task_failed_without_dispatch():
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
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object()},
        max_iterations=1,
    )

    assert processed == 0
    assert service.calls == []
    assert repository.executing == [("task-1", "node-a")]
    assert repository.failed == [("task-1", "executor_preflight_same_exchange")]


@pytest.mark.asyncio
async def test_executor_preflight_binding_resolution_failed_without_dispatch():
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
                            "buy_account_id": "101",
                            "sell_account_id": "202",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    resolver = FakeExecutorAccountTruthResolver(resolved={})
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(exchange="bitget"), FakeExchangeAccount(exchange="gate")]}
        ),
        account_truth_resolver=resolver,
        env_mode="testnet",
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(max_iterations=1)

    assert processed == 0
    assert service.calls == []
    assert repository.executing == [("task-1", "node-a")]
    assert repository.failed == [
        ("task-1", "executor_preflight_account_resolution_failed")
    ]
```

- [ ] **Step 2: Run the new executor-consumer tests to verify they fail**

```bash
pytest -q tests/test_live_workers.py -k "same_exchange_marks_task_failed_without_dispatch or binding_resolution_failed_without_dispatch"
```

Expected: FAIL because `RedisExecutionTaskConsumer` still dispatches without any preflight validation.

- [ ] **Step 3: Wire validator into the consumer**

```python
class RedisExecutionTaskConsumer(RedisSpotConsumer):
    def __init__(
        self,
        *,
        control_guard=None,
        task_repository=None,
        account_repository=None,
        account_truth_resolver=None,
        preflight_validator=None,
        env_mode: str = "testnet",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.control_guard = control_guard
        self.task_repository = task_repository
        self.account_repository = account_repository
        self.account_truth_resolver = account_truth_resolver
        self.preflight_validator = preflight_validator or ExecutorPreflightValidator()
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
                        task_uuid = (
                            str(payload["task_uuid"])
                            if payload.get("task_uuid") is not None
                            else None
                        )
                        if task_uuid is not None and self.task_repository is not None:
                            self.task_repository.mark_executing(
                                task_uuid,
                                worker_node_id=self.region,
                            )
                        effective_payload = payload
                        execution_accounts_by_exchange = (
                            self._resolve_execution_accounts(payload=effective_payload)
                        )
                        self.preflight_validator.validate(
                            payload=effective_payload,
                            execution_accounts_by_exchange=execution_accounts_by_exchange,
                        )

                        dispatch_credentials_by_exchange = credentials_by_exchange
                        proxies_by_exchange = None
                        if execution_accounts_by_exchange is not None:
                            dispatch_credentials_by_exchange = {
                                exchange: (
                                    account["credentials"]
                                    if isinstance(account, dict)
                                    else account.credentials
                                )
                                for exchange, account in execution_accounts_by_exchange.items()
                            }
                            proxies_by_exchange = {
                                exchange: (
                                    account["proxies"]
                                    if isinstance(account, dict)
                                    else account.proxies
                                )
                                for exchange, account in execution_accounts_by_exchange.items()
                            }
                        if dispatch_credentials_by_exchange is None:
                            raise RuntimeError(
                                "credentials_by_exchange is required when account truth resolution is unavailable"
                            )

                        await self.dispatcher.dispatch(
                            effective_payload,
                            execution_accounts_by_exchange=execution_accounts_by_exchange,
                            credentials_by_exchange=dispatch_credentials_by_exchange,
                            proxies_by_exchange=proxies_by_exchange,
                        )
                        if task_uuid is not None and self.task_repository is not None:
                            self.task_repository.mark_succeeded(task_uuid)
                        self.last_id = message_id
                        processed += 1
                    except Exception as exc:
                        if task_uuid is not None and self.task_repository is not None:
                            failure_reason = getattr(exc, "reason", str(exc))
                            self.task_repository.mark_failed(
                                task_uuid,
                                reason=failure_reason,
                            )
            iteration += 1
        return processed
```

The insertion point is immediately after the existing control-rule block/resize branch and immediately before `dispatch_credentials_by_exchange = credentials_by_exchange`, so the validator always sees the final `effective_payload`.

- [ ] **Step 4: Run the targeted executor regressions**

```bash
pytest -q tests/test_live_workers.py -k "executor_preflight or executor_marks_task_executing_and_succeeded or executor_binding_failure_persists_reason_and_does_not_call_spot_service or executor_emits_control_rule_resized_event"
```

Expected: PASS. The new preflight failures should persist stable reason codes, and the existing success plus binding plus resized-control-rule paths should still pass.

- [ ] **Step 5: Commit the consumer integration**

```bash
git add tests/test_live_workers.py app/runtime/live_workers.py
git commit -m "feat: enforce executor preflight checks"
```

## Task 3: Run Broader Regression And Finish Validation

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py` only if the regression run exposes a real mismatch
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py` only if the regression run exposes a real mismatch

- [ ] **Step 1: Run the full live-worker regression slice**

```bash
pytest -q tests/test_live_workers.py tests/test_worker_service.py tests/test_redis_opportunity_flow.py tests/test_task_repository.py
```

Expected: PASS. This confirms executor preflight did not regress task-account-binding, worker bootstrapping, payload generation, or repository persistence behavior.

- [ ] **Step 2: Run a lightweight syntax check on the touched runtime module**

```bash
python -m py_compile app/runtime/live_workers.py
```

Expected: PASS with no output.

- [ ] **Step 3: Check the working tree before handoff**

```bash
git status --short
```

Expected: show only the intended runtime/test changes before the final commit, or show a clean tree if Task 2 already committed everything.

- [ ] **Step 4: If Task 3 required cleanup edits, commit them**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "test: finalize executor preflight regression coverage"
```

Expected: only run this commit if the broader regression required a real follow-up code/test adjustment.
