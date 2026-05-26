# Executor Repair Publisher Default Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the default `WorkerApp(role=executor)` path publish repair tasks without manual wiring by injecting `RepairTaskPublisher` in `DefaultWorkerFactory.build_executor_worker()`.

**Architecture:** Keep the change strictly in the executor wiring layer. First add focused tests that prove the default executor worker and `WorkerApp(role=executor)` should carry a `RepairTaskPublisher`; then implement the minimal factory change in `worker_service.py`; finally run nearby regressions to prove the existing executor publish path, repair worker path, and worker app behavior do not regress.

**Tech Stack:** Python 3.10+, pytest, Redis Streams, existing runtime worker factory and consumer classes

---

## File Structure

- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
  - Import `RepairTaskPublisher`
  - Inject `repair_task_publisher=RepairTaskPublisher(redis_client)` into the default executor consumer wiring
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`
  - Add focused tests that verify the executor worker carries the publisher by default
  - Add a `WorkerApp(role=executor)` assertion that the built consumer carries the publisher
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - Only if needed, add a narrow smoke test proving the default-factory-wired executor consumer still reaches the existing repair publish behavior

## Task 1: Lock The Default Executor Wiring Contract With Tests

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: Write the failing default wiring tests**

Add these tests near the existing executor/repair worker wiring tests:

```python
def test_build_executor_worker_uses_repair_task_publisher_by_default():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="executor",
            worker_region="node-a",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_executor_worker(redis_client=FakeRedis())

    assert worker.consumer.repair_task_publisher is not None
    assert type(worker.consumer.repair_task_publisher).__name__ == "RepairTaskPublisher"


@pytest.mark.asyncio
async def test_worker_app_runs_executor_role_with_repair_task_publisher(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="executor",
            worker_region="node-a",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
        ),
        event_router=FakeEventRouter(),
    )
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="executor",
            worker_region="node-a",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
        event_router=FakeEventRouter(),
    )

    build_calls = {}
    original_build = factory.build_executor_worker

    def wrapped_build_executor_worker(*, redis_client):
        worker = original_build(redis_client=redis_client)
        build_calls["repair_task_publisher"] = worker.consumer.repair_task_publisher
        return worker

    factory.build_executor_worker = wrapped_build_executor_worker

    with pytest.raises(AttributeError):
        await app.run()

    assert build_calls["repair_task_publisher"] is not None
    assert type(build_calls["repair_task_publisher"]).__name__ == "RepairTaskPublisher"
```

The second test intentionally allows the real consumer run to fail later because `FakeRedis` in this file does not implement the executor runtime stream methods; the contract we care about is that the default executor worker gets built with a publisher.

- [ ] **Step 2: Run the targeted tests to verify red**

Run:

```bash
python -m pytest -q tests/test_worker_service.py -k "build_executor_worker_uses_repair_task_publisher_by_default or worker_app_runs_executor_role_with_repair_task_publisher"
```

Expected: FAIL because `build_executor_worker()` currently leaves `repair_task_publisher` unset.

- [ ] **Step 3: Implement the minimal executor default wiring**

Update `app/runtime/worker_service.py` imports:

```python
from app.runtime.redis_flow import RepairTaskPublisher
```

Then update `DefaultWorkerFactory.build_executor_worker()`:

```python
    def build_executor_worker(self, *, redis_client: Redis) -> ConsumerWorker:
        dispatcher = RedisOpportunityDispatcher(self.trade_execution_service)
        control_guard = ControlGuard(
            control_plane_loader=ControlPlaneLoader(ControlPlaneStore(redis_client)),
            event_router=self.event_router,
            service_name="executor",
            region=self.settings.worker_region,
        )
        task_repository = None
        account_repository = None
        account_truth_resolver = None
        if self.settings.database_enabled:
            session_factory = build_session_factory(self.settings.database_url)
            session = session_factory()
            task_repository = TaskRepository(session)
            account_repository = AccountRepository(session)
            account_truth_resolver = ExecutorAccountTruthResolver()
        consumer = RedisExecutionTaskConsumer(
            redis_client=redis_client,
            dispatcher=dispatcher,
            stream_key=self.settings.resolved_executor_stream_key,
            control_guard=control_guard,
            task_repository=task_repository,
            account_repository=account_repository,
            account_truth_resolver=account_truth_resolver,
            risk_manager=RiskManager(),
            repair_task_publisher=RepairTaskPublisher(redis_client),
            env_mode=self.settings.env_mode,
            block_ms=self.settings.consumer_block_ms,
            event_router=self.event_router,
            region=self.settings.worker_region,
        )
        return ConsumerWorker(consumer=consumer)
```

- [ ] **Step 4: Re-run the targeted tests to verify green**

Run:

```bash
python -m pytest -q tests/test_worker_service.py -k "build_executor_worker_uses_repair_task_publisher_by_default or worker_app_runs_executor_role_with_repair_task_publisher"
```

Expected: PASS.

- [ ] **Step 5: Commit the wiring change**

```bash
git add app/runtime/worker_service.py tests/test_worker_service.py
git commit -m "feat: wire repair publisher into default executor worker"
```

## Task 2: Prove Existing Publish Behavior Still Works On The Default Path

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: Add a narrow default-factory smoke test only if coverage is still missing**

If the Task 1 tests already prove enough, keep this step as a no-op. Otherwise add this focused test to `tests/test_worker_service.py`:

```python
def test_build_executor_worker_default_publisher_uses_same_redis_client():
    redis_client = FakeRedis()
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="executor",
            worker_region="node-a",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_executor_worker(redis_client=redis_client)

    assert worker.consumer.repair_task_publisher is not None
    assert worker.consumer.repair_task_publisher.redis_client is redis_client
```

- [ ] **Step 2: Run the focused worker service slice**

Run:

```bash
python -m pytest -q tests/test_worker_service.py -k "repair_task_publisher or build_executor_worker_uses_trade_execution_service or build_repair_worker_uses_repair_execution_service"
```

Expected: PASS.

- [ ] **Step 3: Run the nearby executor publish regression slice**

Run:

```bash
python -m pytest -q tests/test_live_workers.py -k "publishes_repair_task_for_open_partial_result or repair_planned_event"
```

Expected: PASS and prove the existing executor publish behavior still holds after the default wiring change.

- [ ] **Step 4: Commit only if you added a new smoke test in this task**

```bash
git add tests/test_worker_service.py
git commit -m "test: cover default executor repair publisher wiring"
```

Expected: skip this commit if Task 2 did not introduce any new file changes.

## Task 3: Focused Regression And Working Tree Check

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: Run the focused regression set**

Run:

```bash
python -m pytest -q tests/test_worker_service.py tests/test_live_workers.py -k "executor or repair"
python -m py_compile app/runtime/worker_service.py tests/test_worker_service.py tests/test_live_workers.py
```

Expected: PASS with no syntax errors.

- [ ] **Step 2: Check the working tree and recent commits**

Run:

```bash
git status --short
git log -4 --oneline
```

Expected: either a clean tree after the planned commits, or only the intended files if a follow-up fix is still pending.

- [ ] **Step 3: If a real follow-up fix was needed, commit it**

```bash
git add app/runtime/worker_service.py tests/test_worker_service.py tests/test_live_workers.py
git commit -m "test: finalize executor repair publisher wiring regressions"
```

Expected: skip this commit if no follow-up fix was needed.
