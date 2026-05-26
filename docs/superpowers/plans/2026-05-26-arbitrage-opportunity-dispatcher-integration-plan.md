# Arbitrage Opportunity Dispatcher Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an independent arbitrage dispatch chain that consumes `stream:opportunities`, creates `OPEN` and `CLOSE` arbitrage dispatch records, and leaves the existing `spot` execution chain unchanged.

**Architecture:** Keep `RedisSpotConsumer` and `RedisNodeTaskDispatcher` for `stream:spot_opps` exactly as they are. Add a new `RedisArbitrageConsumer`, a new `RedisArbitrageTaskDispatcher`, and a small close-context query in `TaskRepository`, then wire them behind a new worker role so the arbitrage dispatch path is isolated from the current executor and repair workers.

**Tech Stack:** Python 3.10, `asyncio`, `pytest`, Redis Streams, SQLAlchemy task repository, existing worker runtime patterns in `app/runtime/live_workers.py` and `app/runtime/worker_service.py`.

---

### Task 1: Add Close Context Query In Task Repository

**Files:**
- Modify: `app/db/task_repository.py`
- Test: `tests/test_task_repository.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_task_repository.py`:

```python
def test_task_repository_finds_closeable_open_task_by_user_symbol_and_exchanges(session):
    repository = TaskRepository(session)
    created = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-open-1",
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

    matched = repository.find_closeable_task(
        user_id=42,
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        env_mode="testnet",
    )

    assert matched is not None
    assert matched.task_uuid == created.task_uuid


def test_task_repository_returns_none_when_only_close_task_exists(session):
    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-close-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="2-0",
            env_mode="testnet",
            task_type="close",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=18.0,
            expected_funding_bps=3.0,
            idempotency_key="42:2-0:close:11",
            home_region="main",
        )
    )

    matched = repository.find_closeable_task(
        user_id=42,
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        env_mode="testnet",
    )

    assert matched is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_task_repository.py -q
```

Expected:

```text
FAIL tests/test_task_repository.py::test_task_repository_finds_closeable_open_task_by_user_symbol_and_exchanges
FAIL tests/test_task_repository.py::test_task_repository_returns_none_when_only_close_task_exists
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/db/task_repository.py`:

```python
from sqlalchemy import desc, select


class TaskRepository:
    ...
    def find_closeable_task(
        self,
        *,
        user_id: int,
        symbol: str,
        spot_exchange: str,
        derivative_exchange: str,
        env_mode: str,
    ) -> ArbitrageTask | None:
        return self.session.scalar(
            select(ArbitrageTask)
            .where(
                ArbitrageTask.user_id == user_id,
                ArbitrageTask.symbol == symbol,
                ArbitrageTask.spot_exchange == spot_exchange,
                ArbitrageTask.derivative_exchange == derivative_exchange,
                ArbitrageTask.env_mode == env_mode,
                ArbitrageTask.task_type == "open",
            )
            .order_by(desc(ArbitrageTask.id))
            .limit(1)
        )
```

Keep this query intentionally narrow in this task:

- it only answers whether a closeable open-context exists
- it does not add new status filtering or position logic yet

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
git commit -m "feat: add arbitrage close context query"
```

### Task 2: Add Arbitrage Consumer And Dispatcher

**Files:**
- Modify: `app/runtime/live_workers.py`
- Test: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

Add these helpers and tests to `tests/test_live_workers.py`:

```python
class FakeArbitrageDispatchRepository(FakeTaskRepository):
    def __init__(self, *, task_uuid: str, closeable_contexts=None):
        super().__init__(task_uuid=task_uuid)
        self.closeable_contexts = list(closeable_contexts or [])
        self.close_context_calls = []

    def find_closeable_task(
        self,
        *,
        user_id: int,
        symbol: str,
        spot_exchange: str,
        derivative_exchange: str,
        env_mode: str,
    ):
        self.close_context_calls.append(
            {
                "user_id": user_id,
                "symbol": symbol,
                "spot_exchange": spot_exchange,
                "derivative_exchange": derivative_exchange,
                "env_mode": env_mode,
            }
        )
        return None if not self.closeable_contexts else self.closeable_contexts.pop(0)


@pytest.mark.asyncio
async def test_arbitrage_consumer_reads_stream_opportunities_and_forwards_message_id():
    dispatcher = FakeDispatcher()
    consumer = RedisArbitrageConsumer(
        redis_client=FakeRedis(
            xread_messages=[
                (
                    "stream:opportunities",
                    [
                        (
                            "9-1",
                            {
                                "symbol": "BTC/USDT",
                                "spot_exchange": "binance",
                                "derivative_exchange": "okx",
                                "opportunity_type": "OPEN",
                                "open_spread_bps": "20.0",
                                "close_spread_bps": "10.0",
                                "funding_rate": "0.0005",
                                "annualized_bps": "40.0",
                                "redis_member": "binance:okx:BTC/USDT:OPEN:1",
                                "timestamp": "1.0",
                            },
                        )
                    ],
                )
            ]
        ),
        dispatcher=dispatcher,
        stream_key="stream:opportunities",
        block_ms=0,
    )

    processed = await consumer.run(max_iterations=1)

    assert processed == 1
    assert dispatcher.calls[0]["payload"]["source_message_id"] == "9-1"
    assert dispatcher.calls[0]["payload"]["opportunity_type"] == "OPEN"


@pytest.mark.asyncio
async def test_arbitrage_dispatcher_creates_open_task_record_from_opportunity():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:opportunities",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "spot_exchange": "binance",
                            "derivative_exchange": "okx",
                            "opportunity_type": "OPEN",
                            "open_spread_bps": "25.0",
                            "close_spread_bps": "14.0",
                            "funding_rate": "0.0005",
                            "annualized_bps": "55.0",
                            "redis_member": "binance:okx:BTC/USDT:OPEN:1",
                            "timestamp": "1.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeArbitrageDispatchRepository(task_uuid="arb-open-1")
    repository.generated_task_uuids = ["arb-open-1"]
    strategy_repository = FakeStrategyConfigRepository(
        [FakeStrategyConfig(id=11, target_quote_amount=80.0, open_spread_bps_threshold=20.0)]
    )
    dispatcher = RedisArbitrageTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_repository=repository,
        strategy_repository=strategy_repository,
        stream_key="stream:opportunities",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert repository.created[0].task_type == "open"
    assert repository.created[0].spot_exchange == "binance"
    assert repository.created[0].derivative_exchange == "okx"


@pytest.mark.asyncio
async def test_arbitrage_dispatcher_uses_db_discovered_users_for_open_opportunity():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:opportunities",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "spot_exchange": "binance",
                            "derivative_exchange": "okx",
                            "opportunity_type": "OPEN",
                            "open_spread_bps": "25.0",
                            "close_spread_bps": "14.0",
                            "funding_rate": "0.0005",
                            "annualized_bps": "55.0",
                            "redis_member": "binance:okx:BTC/USDT:OPEN:1",
                            "timestamp": "1.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeArbitrageDispatchRepository(task_uuid="arb-open-1")
    repository.generated_task_uuids = ["arb-open-1"]
    dispatcher = RedisArbitrageTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        route_resolver=UserNodeRouter(redis_client),
        task_repository=repository,
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0, open_spread_bps_threshold=20.0)]
        ),
        stream_key="stream:opportunities",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert [item.user_id for item in repository.created] == [42]


@pytest.mark.asyncio
async def test_arbitrage_dispatcher_skips_open_when_exchange_coverage_is_missing():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:opportunities",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "spot_exchange": "binance",
                            "derivative_exchange": "okx",
                            "opportunity_type": "OPEN",
                            "open_spread_bps": "25.0",
                            "close_spread_bps": "14.0",
                            "funding_rate": "0.0005",
                            "annualized_bps": "55.0",
                            "redis_member": "binance:okx:BTC/USDT:OPEN:1",
                            "timestamp": "1.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeArbitrageDispatchRepository(task_uuid="arb-open-1")
    dispatcher = RedisArbitrageTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_repository=repository,
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0, open_spread_bps_threshold=20.0)]
        ),
        account_repository=FakeAccountRepository({"42": [FakeExchangeAccount(exchange="binance")]}),
        stream_key="stream:opportunities",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert repository.created == []


@pytest.mark.asyncio
async def test_arbitrage_dispatcher_skips_close_without_closeable_context():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:opportunities",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "spot_exchange": "binance",
                            "derivative_exchange": "okx",
                            "opportunity_type": "CLOSE",
                            "open_spread_bps": "25.0",
                            "close_spread_bps": "14.0",
                            "funding_rate": "0.0005",
                            "annualized_bps": "55.0",
                            "redis_member": "binance:okx:BTC/USDT:CLOSE:1",
                            "timestamp": "1.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeArbitrageDispatchRepository(task_uuid="arb-close-1", closeable_contexts=[])
    dispatcher = RedisArbitrageTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_repository=repository,
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        stream_key="stream:opportunities",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert repository.created == []
    assert repository.close_context_calls[0]["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_arbitrage_dispatcher_creates_close_task_when_closeable_context_exists():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:opportunities",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "spot_exchange": "binance",
                            "derivative_exchange": "okx",
                            "opportunity_type": "CLOSE",
                            "open_spread_bps": "25.0",
                            "close_spread_bps": "14.0",
                            "funding_rate": "0.0005",
                            "annualized_bps": "55.0",
                            "redis_member": "binance:okx:BTC/USDT:CLOSE:1",
                            "timestamp": "1.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeArbitrageDispatchRepository(
        task_uuid="arb-close-1",
        closeable_contexts=[type("Task", (), {"task_uuid": "open-ctx-1"})()],
    )
    repository.generated_task_uuids = ["arb-close-1"]
    dispatcher = RedisArbitrageTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_repository=repository,
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        stream_key="stream:opportunities",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert repository.created[0].task_type == "close"
    assert repository.created[0].opportunity_id == "1-0"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_workers.py -q
```

Expected:

```text
FAIL tests/test_live_workers.py::test_arbitrage_consumer_reads_stream_opportunities_and_forwards_message_id
FAIL tests/test_live_workers.py::test_arbitrage_dispatcher_creates_open_task_record_from_opportunity
FAIL tests/test_live_workers.py::test_arbitrage_dispatcher_uses_db_discovered_users_for_open_opportunity
FAIL tests/test_live_workers.py::test_arbitrage_dispatcher_skips_open_when_exchange_coverage_is_missing
FAIL tests/test_live_workers.py::test_arbitrage_dispatcher_skips_close_without_closeable_context
FAIL tests/test_live_workers.py::test_arbitrage_dispatcher_creates_close_task_when_closeable_context_exists
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/live_workers.py` with a dedicated consumer and dispatcher:

```python
class RedisArbitrageConsumer(RedisSpotConsumer):
    processed_event_type = "arb.consumer.message.processed"
    processed_event_service = "arb_consumer"
    processed_event_message = "arbitrage opportunity processed"
    failed_event_type = "arb.consumer.message.failed"
    failed_event_service = "arb_consumer"
    failed_event_message = "arbitrage opportunity failed"

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
                    enriched_payload = dict(payload)
                    enriched_payload["source_message_id"] = message_id
                    await self.dispatcher.dispatch(
                        enriched_payload,
                        credentials_by_exchange=credentials_by_exchange or {},
                    )
                    self.last_id = message_id
                    processed += 1
            iteration += 1
        return processed


class RedisArbitrageTaskDispatcher:
    def __init__(
        self,
        *,
        redis_client,
        user_ids: list[str],
        route_resolver,
        task_repository=None,
        strategy_repository=None,
        dispatch_user_repository=None,
        account_repository=None,
        stream_key: str,
        block_ms: int = 1000,
        region: str = "default",
        env_mode: str = "testnet",
    ) -> None:
        self.redis_client = redis_client
        self.user_ids = user_ids
        self.route_resolver = route_resolver
        self.task_repository = task_repository
        self.strategy_repository = strategy_repository
        self.dispatch_user_repository = dispatch_user_repository
        self.account_repository = account_repository
        self.stream_key = stream_key
        self.block_ms = block_ms
        self.region = region
        self.env_mode = env_mode
        self.last_id = "0-0"

    def _resolve_candidate_user_ids(self) -> list[str]:
        if self.dispatch_user_repository is None:
            return list(self.user_ids)
        discovered_user_ids = self.dispatch_user_repository.list_dispatchable_user_ids(
            env_mode=self.env_mode
        )
        if not self.user_ids:
            return discovered_user_ids
        allowed_user_ids = set(discovered_user_ids)
        return [user_id for user_id in self.user_ids if user_id in allowed_user_ids]

    def _load_user_accounts(self, *, user_id: str) -> list | None:
        if self.account_repository is None:
            return None
        return list(
            self.account_repository.list_enabled_accounts(
                user_id=int(user_id),
                env_mode=self.env_mode,
            )
            or []
        )

    def _has_required_account_coverage(self, *, payload: dict, accounts: list | None) -> bool:
        if accounts is None:
            return True
        coverage = _evaluate_account_exchange_coverage(
            payload={
                "buy_exchange": payload["spot_exchange"],
                "sell_exchange": payload["derivative_exchange"],
            },
            accounts=accounts,
            dispatcher_region=self.region,
        )
        return coverage["has_exchange_coverage"] and coverage["has_auto_trade_coverage"]

    def _iter_matching_strategies(self, *, user_id: str, payload: dict) -> list:
        if self.strategy_repository is None:
            return [None]
        strategies = self.strategy_repository.list_enabled_for_user(
            user_id=int(user_id),
            strategy_type="spot_futures",
        )
        matched = []
        for strategy in strategies:
            threshold = float(getattr(strategy, "open_spread_bps_threshold", 0.0) or 0.0)
            if payload["opportunity_type"] == "OPEN" and float(payload["open_spread_bps"]) < threshold:
                continue
            matched.append(strategy)
        return matched

    def _create_arbitrage_task(self, *, user_id: str, node_id: str, payload: dict, strategy):
        if self.task_repository is None:
            return None
        strategy_id = None if strategy is None else int(strategy.id)
        task_type = "open" if payload["opportunity_type"] == "OPEN" else "close"
        notional = 0.0 if strategy is None else float(strategy.target_quote_amount)
        return self.task_repository.create_task(
            ArbitrageTaskCreate(
                task_uuid=uuid4().hex,
                user_id=int(user_id),
                strategy_config_id=strategy_id,
                opportunity_id=str(payload["source_message_id"]),
                env_mode=self.env_mode,
                task_type=task_type,
                symbol=str(payload["symbol"]),
                spot_exchange=str(payload["spot_exchange"]),
                derivative_exchange=str(payload["derivative_exchange"]),
                target_notional=notional,
                expected_spread_bps=(
                    float(payload["open_spread_bps"])
                    if payload["opportunity_type"] == "OPEN"
                    else float(payload["close_spread_bps"])
                ),
                expected_funding_bps=float(payload.get("funding_rate", 0.0)) * 10000,
                idempotency_key=(
                    f"{user_id}:{payload['source_message_id']}:"
                    f"{payload['opportunity_type'].lower()}:{strategy_id}"
                ),
                home_region=self.region,
            )
        )

    async def run(self, *, max_iterations: int | None = None) -> int:
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
                    for user_id in self._resolve_candidate_user_ids():
                        node_id = await self.route_resolver.get_user_node(user_id)
                        if node_id is None:
                            continue
                        accounts = self._load_user_accounts(user_id=user_id)
                        if not self._has_required_account_coverage(
                            payload=payload,
                            accounts=accounts,
                        ):
                            continue
                        for strategy in self._iter_matching_strategies(user_id=user_id, payload=payload):
                            if payload["opportunity_type"] == "CLOSE":
                                if self.task_repository is None:
                                    continue
                                closeable = self.task_repository.find_closeable_task(
                                    user_id=int(user_id),
                                    symbol=str(payload["symbol"]),
                                    spot_exchange=str(payload["spot_exchange"]),
                                    derivative_exchange=str(payload["derivative_exchange"]),
                                    env_mode=self.env_mode,
                                )
                                if closeable is None:
                                    continue
                            self._create_arbitrage_task(
                                user_id=user_id,
                                node_id=node_id,
                                payload=payload,
                                strategy=strategy,
                            )
                    self.last_id = message_id
                    processed += 1
            iteration += 1
        return processed
```

This task deliberately stops at task-record creation:

- no `NodeExecutionTaskPublisher`
- no executor stream publish
- no repair publish

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
git commit -m "feat: add arbitrage dispatch workers"
```

### Task 3: Wire A Dedicated `arb_dispatcher` Worker Role

**Files:**
- Modify: `app/runtime/worker_config.py`
- Modify: `app/runtime/worker_service.py`
- Test: `tests/test_worker_service.py`
- Test: `tests/test_worker_config.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_worker_service.py`:

```python
@pytest.mark.asyncio
async def test_worker_app_dispatches_arb_dispatcher_role(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="arb_dispatcher",
            spot_exchanges=["okx", "bitget"],
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert len(factory.arb_dispatcher_worker.calls) == 1


@pytest.mark.asyncio
async def test_default_worker_factory_builds_arb_dispatcher_with_opportunity_stream():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="arb_dispatcher",
            worker_region="main",
            dispatch_source_stream="stream:opportunities",
            spot_exchanges=["okx", "bitget"],
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_arbitrage_dispatcher_worker(redis_client=FakeRedis())

    assert worker.stream_key == "stream:opportunities"
```

Add this test to `tests/test_worker_config.py`:

```python
def test_worker_settings_accept_arb_dispatcher_role():
    settings = WorkerSettings(worker_role="arb_dispatcher")
    assert settings.worker_role == "arb_dispatcher"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_worker_service.py tests/test_worker_config.py -q
```

Expected:

```text
FAIL tests/test_worker_service.py::test_worker_app_dispatches_arb_dispatcher_role
FAIL tests/test_worker_service.py::test_default_worker_factory_builds_arb_dispatcher_with_opportunity_stream
FAIL tests/test_worker_config.py::test_worker_settings_accept_arb_dispatcher_role
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/worker_config.py`:

```python
worker_role: Literal[
    "scanner",
    "consumer",
    "dispatcher",
    "arb_dispatcher",
    "executor",
    "repair",
] = "scanner"
```

Update `app/runtime/worker_service.py`:

```python
class DefaultWorkerFactory:
    ...
    def build_arbitrage_dispatcher_worker(self, *, redis_client: Redis) -> RedisArbitrageTaskDispatcher:
        task_repository = None
        strategy_repository = None
        dispatch_user_repository = None
        account_repository = None
        if self.settings.database_enabled:
            session_factory = build_session_factory(self.settings.database_url)
            session = session_factory()
            task_repository = TaskRepository(session)
            strategy_repository = StrategyConfigRepository(session)
            dispatch_user_repository = DispatchUserRepository(session)
            account_repository = AccountRepository(session)
        return RedisArbitrageTaskDispatcher(
            redis_client=redis_client,
            user_ids=self.settings.dispatch_user_ids,
            dispatch_user_repository=dispatch_user_repository,
            account_repository=account_repository,
            route_resolver=UserNodeRouter(redis_client),
            task_repository=task_repository,
            strategy_repository=strategy_repository,
            stream_key=self.settings.dispatch_source_stream,
            block_ms=self.settings.consumer_block_ms,
            region=self.settings.worker_region,
            env_mode=self.settings.env_mode,
        )
```

Add the new worker-role branch:

```python
if self.settings.worker_role == "arb_dispatcher":
    worker = factory.build_arbitrage_dispatcher_worker(redis_client=redis_client)
    await worker.run(max_iterations=None)
    return
```

Update `parse_args()` choices:

```python
choices=["scanner", "consumer", "dispatcher", "arb_dispatcher", "executor", "repair"]
```

If `tests/test_worker_service.py` uses a `FakeFactory`, add:

```python
self.arb_dispatcher_worker = FakeWorker()

def build_arbitrage_dispatcher_worker(self, *, redis_client):
    self.arb_dispatcher_worker.redis_client = redis_client
    return self.arb_dispatcher_worker
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_worker_service.py tests/test_worker_config.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/worker_config.py app/runtime/worker_service.py tests/test_worker_service.py tests/test_worker_config.py
git commit -m "feat: wire arbitrage dispatcher worker"
```

### Task 4: Run Focused Regressions

**Files:**
- Review: `docs/superpowers/specs/2026-05-26-arbitrage-opportunity-dispatcher-integration-design.md`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_worker_service.py`
- Test: `tests/test_worker_config.py`
- Test: `tests/test_task_repository.py`

- [ ] **Step 1: Run the focused regression suite**

Run:

```bash
python -m pytest tests/test_live_workers.py tests/test_worker_service.py tests/test_worker_config.py tests/test_task_repository.py tests/test_redis_opportunity_flow.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Verify old spot stream still appears in tests**

Run:

```bash
python -m pytest tests/test_live_workers.py -q -k "spot_opps or redis_consumer or dispatcher_worker_routes_public_opportunity"
```

Expected:

```text
selected legacy spot-dispatch tests pass unchanged
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

- [ ] **Step 4: Commit if any final test-only cleanup is needed**

Run:

```bash
git log --oneline -n 3
```

Expected:

```text
shows the three B1-2 implementation commits at the top
```
