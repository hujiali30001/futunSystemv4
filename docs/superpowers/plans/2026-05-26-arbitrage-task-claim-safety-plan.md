# Arbitrage Task Claim Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an atomic claim path for arbitrage tasks so `arb_executor` can safely run with multiple workers without double-claiming the same task.

**Architecture:** Keep the existing `ArbitrageExecutionAdapter`, repair compatibility, and spot executor path unchanged. Add a repository-level `claim_next_executable_task()` boundary that atomically transitions one eligible arbitrage task into `RUNNING`, then change `ArbitrageExecutionTaskConsumer` to use that claim path instead of `list_executable_tasks() + mark_executing()`.

**Tech Stack:** Python 3.10, `asyncio`, `pytest`, SQLAlchemy ORM/Core update queries, existing runtime worker patterns in `app/db/task_repository.py` and `app/runtime/live_workers.py`.

---

### Task 1: Add Atomic Arbitrage Task Claiming

**Files:**
- Modify: `app/db/task_repository.py`
- Test: `tests/test_task_repository.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_task_repository.py`:

```python
def test_claim_next_executable_task_claims_created_task_and_sets_running_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-open-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )

    claimed = repository.claim_next_executable_task(
        worker_node_id="node-a",
        env_mode="testnet",
    )

    assert claimed is not None
    assert claimed.task_uuid == "arb-open-1"
    assert claimed.status == "RUNNING"
    assert claimed.worker_node_id == "node-a"
    assert claimed.started_at is not None


def test_claim_next_executable_task_does_not_return_same_task_twice():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-open-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )

    first = repository.claim_next_executable_task(
        worker_node_id="node-a",
        env_mode="testnet",
    )
    second = repository.claim_next_executable_task(
        worker_node_id="node-b",
        env_mode="testnet",
    )

    assert first is not None
    assert second is None


def test_claim_next_executable_task_skips_terminal_and_running_states():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-succeeded",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_succeeded("arb-succeeded")

    assert (
        repository.claim_next_executable_task(
            worker_node_id="node-a",
            env_mode="testnet",
        )
        is None
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_task_repository.py -q
```

Expected:

```text
FAIL tests/test_task_repository.py::test_claim_next_executable_task_claims_created_task_and_sets_running_fields
FAIL tests/test_task_repository.py::test_claim_next_executable_task_does_not_return_same_task_twice
FAIL tests/test_task_repository.py::test_claim_next_executable_task_skips_terminal_and_running_states
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/db/task_repository.py` to add an atomic claim helper:

```python
from sqlalchemy import desc, select, update


class TaskRepository:
    ...
    def claim_next_executable_task(
        self,
        *,
        worker_node_id: str,
        env_mode: str,
    ) -> ArbitrageTask | None:
        claimed_at = datetime.utcnow()
        candidate_id = (
            select(ArbitrageTask.id)
            .where(
                ArbitrageTask.env_mode == env_mode,
                ArbitrageTask.status.in_(("CREATED", "DISPATCHED")),
            )
            .order_by(ArbitrageTask.id.asc())
            .limit(1)
            .scalar_subquery()
        )
        claimed_id = self.session.execute(
            update(ArbitrageTask)
            .where(
                ArbitrageTask.id == candidate_id,
                ArbitrageTask.status.in_(("CREATED", "DISPATCHED")),
            )
            .values(
                status="RUNNING",
                worker_node_id=worker_node_id,
                started_at=claimed_at,
            )
            .returning(ArbitrageTask.id)
        ).scalar_one_or_none()
        if claimed_id is None:
            self.session.rollback()
            return None
        self.session.commit()
        return self.session.get(ArbitrageTask, claimed_id)
```

Keep `list_executable_tasks()` in place for now. This task only adds the new safe claim boundary and does not remove the old helper yet.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_task_repository.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/db/task_repository.py tests/test_task_repository.py
git commit -m "feat: add atomic arbitrage task claim"
```

### Task 2: Switch `ArbitrageExecutionTaskConsumer` To Claim-Only

**Files:**
- Modify: `app/runtime/live_workers.py`
- Test: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

Update `FakeTaskRepository` in `tests/test_live_workers.py` to support a claim path:

```python
self.claim_calls = []
self.claimed_tasks = []

def claim_next_executable_task(self, *, worker_node_id: str, env_mode: str):
    self.claim_calls.append(
        {"worker_node_id": worker_node_id, "env_mode": env_mode}
    )
    return None if not self.claimed_tasks else self.claimed_tasks.pop(0)
```

Then update/add these tests:

```python
@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_claims_task_before_running_adapter():
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
    repository.claimed_tasks = [task]
    account_repository = FakeAccountRepository(
        {
            "42": [
                FakeExchangeAccount(account_id=11, exchange="binance"),
                FakeExchangeAccount(account_id=12, exchange="okx"),
            ]
        }
    )
    execution_adapter = ArbitrageExecutionAdapterStub(
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
    )
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=repository,
        execution_adapter=execution_adapter,
        repair_service=FakeRepairExecutionService(result=None),
        account_repository=account_repository,
        worker_node_id="node-a",
        env_mode="testnet",
    )

    processed = await consumer.run_once(
        credentials_by_exchange={"binance": object(), "okx": object()},
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert processed == 1
    assert repository.claim_calls == [
        {"worker_node_id": "node-a", "env_mode": "testnet"}
    ]
    assert repository.list_executable_calls == []
    assert repository.executing == []


@pytest.mark.asyncio
async def test_arbitrage_execution_consumer_returns_zero_when_claim_finds_no_task():
    repository = FakeTaskRepository(task_uuid="arb-open-1")
    repository.claimed_tasks = []
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=repository,
        execution_adapter=ArbitrageExecutionAdapterStub(result=None),
        repair_service=FakeRepairExecutionService(result=None),
        account_repository=FakeAccountRepository({"42": []}),
        worker_node_id="node-a",
        env_mode="testnet",
    )

    processed = await consumer.run_once(
        credentials_by_exchange={},
        proxies_by_exchange={},
    )

    assert processed == 0
    assert repository.claim_calls == [
        {"worker_node_id": "node-a", "env_mode": "testnet"}
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_workers.py -q
```

Expected:

```text
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_claims_task_before_running_adapter
FAIL tests/test_live_workers.py::test_arbitrage_execution_consumer_returns_zero_when_claim_finds_no_task
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/live_workers.py`:

```python
class ArbitrageExecutionTaskConsumer:
    ...
    async def run_once(
        self,
        *,
        credentials_by_exchange: dict[str, Any],
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> int:
        task = self.task_repository.claim_next_executable_task(
            worker_node_id=self.worker_node_id,
            env_mode=self.env_mode,
        )
        if task is None:
            return 0

        try:
            execution_accounts = self._build_execution_accounts(task)
            result = await self.execution_adapter.execute_task(
                task=task,
                credentials_by_exchange=credentials_by_exchange,
                execution_accounts_by_exchange=execution_accounts,
                env_mode=self.env_mode,
                proxies_by_exchange=proxies_by_exchange,
            )
            ...
```

Do not call `list_executable_tasks()` or `mark_executing()` anywhere in the arbitrage consumer after this change.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_live_workers.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: claim arbitrage tasks atomically"
```

### Task 3: Run Focused Claim-Safety Regressions

**Files:**
- Review: `docs/superpowers/specs/2026-05-26-arbitrage-task-claim-safety-design.md`
- Test: `tests/test_task_repository.py`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_worker_service.py`
- Test: `tests/test_worker_config.py`

- [ ] **Step 1: Run the focused regression suite**

Run:

```bash
python -m pytest tests/test_task_repository.py tests/test_live_workers.py tests/test_worker_service.py tests/test_worker_config.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Re-check legacy spot executor coverage**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "RedisExecutionTaskConsumer or executor or repair_planned"
```

Expected:

```text
selected legacy executor tests pass unchanged
```

- [ ] **Step 3: Check git status**

Run:

```bash
git status --short
```

Expected:

```text
working tree clean
```

- [ ] **Step 4: Inspect recent commits**

Run:

```bash
git log --oneline -n 4
```

Expected:

```text
shows the two B1-4 implementation commits at the top, followed by the B1-4 spec/plan commits
```
