import pytest

from app.runtime.runtime_events import RuntimeEvent
from app.runtime.worker_config import AlertSettings, WorkerSettings
from app.runtime.worker_service import (
    DefaultWorkerFactory,
    ScannerWorker,
    WorkerApp,
    parse_args,
)


class FakeRedis:
    def __init__(self):
        self.closed = False
        self.values = {}
        self.set_calls = []

    async def aclose(self):
        self.closed = True

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value
        self.set_calls.append((key, value))
        return True

    async def sadd(self, key, *values):
        return len(values)


class FakeWorker:
    def __init__(self):
        self.calls = []
        self.error = None

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return 1


class FakeFactory:
    def __init__(self, settings=None):
        self.settings = settings
        self.scanner_worker = FakeWorker()
        self.consumer_worker = FakeWorker()
        self.dispatcher_worker = FakeWorker()
        self.executor_worker = FakeWorker()

    def build_scanner_worker(self, **kwargs):
        if self.settings is None:
            raise AssertionError("settings must be provided for scanner worker tests")
        return ScannerWorker(scanner=self.scanner_worker, settings=self.settings)

    def build_consumer_worker(self, **kwargs):
        return self.consumer_worker

    def build_dispatcher_worker(self, **kwargs):
        return self.dispatcher_worker

    def build_executor_worker(self, **kwargs):
        return self.executor_worker


class FakeEventRouter:
    def __init__(self):
        self.events = []

    async def dispatch(self, event: RuntimeEvent):
        self.events.append(event)


def seed_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_API_KEY", "okx-key")
    monkeypatch.setenv("OKX_SECRET", "okx-secret")
    monkeypatch.setenv("BITGET_API_KEY", "bitget-key")
    monkeypatch.setenv("BITGET_SECRET", "bitget-secret")
    monkeypatch.setenv("GATE_API_KEY", "gate-key")
    monkeypatch.setenv("GATE_SECRET", "gate-secret")


@pytest.mark.asyncio
async def test_scanner_worker_passes_symbols_depth_and_quote_amount():
    scanner = FakeWorker()
    settings = WorkerSettings(
        worker_role="scanner",
        spot_symbol="BTC/USDT",
        spot_symbols=["BTC/USDT", "ETH/USDT"],
        orderbook_depth_limit=9,
        target_quote_amount=250.0,
    )
    worker = ScannerWorker(scanner=scanner, settings=settings)

    await worker.run(
        exchanges=["okx", "bitget"],
        credentials_by_exchange={"okx": object(), "bitget": object()},
        proxies_by_exchange={"okx": {"http": "http://127.0.0.1:1"}},
    )

    assert scanner.calls[0]["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert scanner.calls[0]["orderbook_depth_limit"] == 9
    assert scanner.calls[0]["target_quote_amount"] == 250.0


@pytest.mark.asyncio
async def test_worker_app_dispatches_scanner_role_and_closes_redis(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    settings = WorkerSettings(
        worker_role="scanner",
        spot_exchanges=["okx", "bitget"],
        spot_symbol="BTC/USDT",
    )
    factory = FakeFactory(settings=settings)
    router = FakeEventRouter()
    app = WorkerApp(
        settings=settings,
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
        event_router=router,
    )

    await app.run()

    assert len(factory.scanner_worker.calls) == 1
    assert factory.scanner_worker.calls[0]["exchanges"] == ["okx", "bitget"]
    assert factory.scanner_worker.calls[0]["symbols"] == ["BTC/USDT"]
    assert [event.event_type for event in router.events] == [
        "worker.started",
        "worker.stopped",
    ]
    assert redis_client.closed is True


@pytest.mark.asyncio
async def test_worker_app_passes_symbols_depth_and_quote_amount_to_scanner(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    settings = WorkerSettings(
        worker_role="scanner",
        spot_symbol="BTC/USDT",
        spot_symbols=["BTC/USDT", "ETH/USDT"],
        spot_exchanges=["okx", "bitget"],
        orderbook_depth_limit=7,
        target_quote_amount=321.0,
    )
    factory = FakeFactory(settings=settings)
    app = WorkerApp(
        settings=settings,
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert factory.scanner_worker.calls[0]["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert factory.scanner_worker.calls[0]["orderbook_depth_limit"] == 7
    assert factory.scanner_worker.calls[0]["target_quote_amount"] == 321.0


@pytest.mark.asyncio
async def test_worker_app_dispatches_consumer_role_and_closes_redis(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = FakeFactory()
    router = FakeEventRouter()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="consumer",
            spot_exchanges=["okx", "bitget"],
            spot_symbol="BTC/USDT",
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
        event_router=router,
    )

    await app.run()

    assert len(factory.consumer_worker.calls) == 1
    assert factory.consumer_worker.calls[0]["stream_key"] == "stream:spot_opps"
    assert [event.event_type for event in router.events] == [
        "worker.started",
        "worker.stopped",
    ]
    assert redis_client.closed is True


@pytest.mark.asyncio
async def test_worker_app_dispatches_dispatcher_role(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="dispatcher",
            spot_exchanges=["okx", "bitget"],
            dispatch_user_ids=["42"],
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert len(factory.dispatcher_worker.calls) == 1


@pytest.mark.asyncio
async def test_default_worker_factory_builds_dispatcher_with_control_guard():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="dispatcher",
            worker_region="main",
            spot_exchanges=["okx", "bitget"],
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_dispatcher_worker(redis_client=FakeRedis())

    assert worker.control_guard is not None
    assert worker.control_guard.service_name == "dispatcher"
    assert worker.control_guard.region == "main"


@pytest.mark.asyncio
async def test_default_worker_factory_builds_dispatcher_with_task_repository_when_database_enabled():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="dispatcher",
            worker_region="main",
            spot_exchanges=["okx", "bitget"],
            database_enabled=True,
            database_url="sqlite:///:memory:",
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_dispatcher_worker(redis_client=FakeRedis())

    assert worker.task_repository is not None


@pytest.mark.asyncio
async def test_default_worker_factory_builds_dispatcher_with_strategy_repository_when_database_enabled():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="dispatcher",
            worker_region="main",
            spot_exchanges=["okx", "bitget"],
            database_enabled=True,
            database_url="sqlite:///:memory:",
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_dispatcher_worker(redis_client=FakeRedis())

    assert worker.strategy_repository is not None


@pytest.mark.asyncio
async def test_default_worker_factory_builds_dispatcher_with_account_repository_when_database_enabled():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="dispatcher",
            worker_region="main",
            spot_exchanges=["okx", "bitget"],
            database_enabled=True,
            database_url="sqlite:///:memory:",
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_dispatcher_worker(redis_client=FakeRedis())

    assert worker.account_repository is not None


@pytest.mark.asyncio
async def test_default_worker_factory_builds_dispatcher_with_dispatch_user_repository_when_database_enabled():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="dispatcher",
            worker_region="main",
            spot_exchanges=["okx", "bitget"],
            database_enabled=True,
            database_url="sqlite:///:memory:",
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_dispatcher_worker(redis_client=FakeRedis())

    assert worker.dispatch_user_repository is not None


@pytest.mark.asyncio
async def test_default_worker_factory_builds_executor_with_control_guard():
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

    assert worker.consumer.control_guard is not None
    assert worker.consumer.control_guard.service_name == "executor"
    assert worker.consumer.control_guard.region == "node-a"


@pytest.mark.asyncio
async def test_default_worker_factory_builds_executor_worker_with_account_truth_dependencies():
    settings = WorkerSettings(
        worker_role="executor",
        database_enabled=True,
        database_url="sqlite:///:memory:",
        env_mode="testnet",
        worker_region="main",
    )
    factory = DefaultWorkerFactory(settings=settings, event_router=FakeEventRouter())

    worker = factory.build_executor_worker(redis_client=FakeRedis())

    assert worker.consumer.account_repository is not None
    assert worker.consumer.account_truth_resolver is not None
    assert worker.consumer.env_mode == settings.env_mode


@pytest.mark.asyncio
async def test_worker_app_syncs_user_node_routes_before_dispatcher_run(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="dispatcher",
            spot_exchanges=["okx", "bitget"],
            dispatch_user_ids=["42", "99"],
            user_node_routes={"42": "node-a", "99": "node-b"},
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert redis_client.set_calls == [
        ("route:user_node:42", "node-a"),
        ("route:user_node:99", "node-b"),
    ]
    assert len(factory.dispatcher_worker.calls) == 1


@pytest.mark.asyncio
async def test_worker_app_syncs_default_routes_without_overwriting_existing_redis(
    monkeypatch,
):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    redis_client.values["route:user_node:42"] = "node-a"
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="dispatcher",
            spot_exchanges=["okx", "bitget"],
            dispatch_user_ids=["42", "99"],
            user_node_routes={"42": "main", "99": "node-b"},
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert redis_client.values["route:user_node:42"] == "node-a"
    assert redis_client.values["route:user_node:99"] == "node-b"


@pytest.mark.asyncio
async def test_worker_app_dispatches_executor_role(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="executor",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert factory.executor_worker.calls[0]["stream_key"] == "stream:spot_exec_tasks:node-a"


@pytest.mark.asyncio
async def test_worker_app_executor_does_not_require_env_exchange_credentials(monkeypatch):
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="executor",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
            env_mode="testnet",
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
        event_router=FakeEventRouter(),
    )
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_SECRET", raising=False)
    monkeypatch.delenv("GATE_API_KEY", raising=False)
    monkeypatch.delenv("GATE_SECRET", raising=False)

    await app.run()

    assert len(factory.executor_worker.calls) == 1
    assert factory.executor_worker.calls[0] == {
        "credentials_by_exchange": {},
        "stream_key": "stream:spot_exec_tasks:node-a",
    }


@pytest.mark.asyncio
async def test_worker_app_scanner_failure_bubbles_and_closes_redis(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    settings = WorkerSettings(
        worker_role="scanner",
        spot_exchanges=["okx", "bitget"],
        spot_symbols=["BTC/USDT", "ETH/USDT"],
    )
    factory = FakeFactory(settings=settings)
    factory.scanner_worker.error = RuntimeError("scanner boom")
    app = WorkerApp(
        settings=settings,
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    with pytest.raises(RuntimeError, match="scanner boom"):
        await app.run()

    assert redis_client.closed is True


def test_parse_args_accepts_role_override():
    args = parse_args(["--role", "executor"])

    assert args.role == "executor"
