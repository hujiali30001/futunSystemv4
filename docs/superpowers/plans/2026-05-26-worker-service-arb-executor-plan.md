# Arb Executor Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `arb_executor` worker role that wires `ArbitrageExecutionAdapter` into `ArbitrageExecutionTaskConsumer` without changing the existing spot `executor` path.

**Architecture:** Extend worker configuration and CLI role parsing so `arb_executor` is a first-class worker role. Add a dedicated `DefaultWorkerFactory.build_arbitrage_executor_worker()` constructor and a `WorkerApp` dispatch branch that reuse the existing executor stream key while keeping `build_executor_worker()` and the spot executor branch unchanged.

**Tech Stack:** Python, pytest, pydantic-settings, redis asyncio

---

### Task 1: Add red tests for arb_executor config and service routing

**Files:**
- Modify: `tests/test_worker_config.py`
- Modify: `tests/test_worker_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_worker_settings_accept_arb_executor_role():
    settings = WorkerSettings(worker_role="arb_executor")

    assert settings.worker_role == "arb_executor"


def test_parse_args_accepts_arb_executor_role_override():
    args = parse_args(["--role", "arb_executor"])

    assert args.role == "arb_executor"
```

```python
def test_default_worker_factory_builds_arbitrage_executor_worker():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="arb_executor",
            worker_region="node-a",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_arbitrage_executor_worker(redis_client=FakeRedis())

    assert type(worker.consumer).__name__ == "ArbitrageExecutionTaskConsumer"
    assert type(worker.consumer.execution_adapter).__name__ == "ArbitrageExecutionAdapter"
    assert worker.consumer.execution_adapter.execution_service is factory.trade_execution_service


@pytest.mark.asyncio
async def test_worker_app_dispatches_arb_executor_role(monkeypatch):
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="arb_executor",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert factory.arb_executor_worker.calls[0]["stream_key"] == "stream:spot_exec_tasks:node-a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_service.py tests/test_worker_config.py -q`
Expected: FAIL because `arb_executor` is not in the allowed worker roles / CLI choices and `DefaultWorkerFactory.build_arbitrage_executor_worker()` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
worker_role: Literal[
    "scanner",
    "consumer",
    "dispatcher",
    "arb_dispatcher",
    "executor",
    "arb_executor",
    "repair",
] = "scanner"
```

```python
def build_arbitrage_executor_worker(self, *, redis_client: Redis) -> ConsumerWorker:
    consumer = ArbitrageExecutionTaskConsumer(
        task_repository=TaskRepository(session),
        execution_adapter=ArbitrageExecutionAdapter(
            execution_service=self.trade_execution_service
        ),
        repair_service=self.repair_execution_service,
        account_repository=AccountRepository(session),
        worker_node_id=self.settings.node_id,
        env_mode=self.settings.env_mode,
        risk_manager=RiskManager(),
    )
    return ConsumerWorker(consumer=consumer)
```

```python
if self.settings.worker_role == "arb_executor":
    worker = factory.build_arbitrage_executor_worker(redis_client=redis_client)
    await worker.run(
        credentials_by_exchange=credentials_by_exchange,
        stream_key=self.settings.resolved_executor_stream_key,
    )
    return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_service.py tests/test_worker_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_worker_service.py tests/test_worker_config.py app/runtime/worker_config.py app/runtime/worker_service.py docs/superpowers/plans/2026-05-26-worker-service-arb-executor-plan.md
git commit -m "feat: add arb executor worker"
```
