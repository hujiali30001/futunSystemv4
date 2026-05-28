import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from redis.asyncio import Redis

from app.admin.control_store import ControlPlaneStore
from app.db.account_repository import AccountRepository
from app.db.dispatch_user_repository import DispatchUserRepository
from app.db.strategy_config_repository import StrategyConfigRepository
from app.db.session import build_session_factory
from app.db.task_repository import TaskRepository
from app.exchanges.session_manager import ExchangeClientFactory
from app.runtime.alerting import (
    AlertRouter,
    EmailNotifier,
    FeishuNotifier,
    StructuredEventLogger,
)
from app.runtime.arbitrage_execution_adapter import ArbitrageExecutionAdapter
from app.runtime.executor_account_truth import ExecutorAccountTruthResolver
from app.runtime.live_arbitrage_flow import (
    LiveArbitrageFlowService,
    SwapSymbolDiscovery,
)
from app.runtime.live_spot_flow import LiveSpotFlowService, SymbolDiscovery
from app.runtime.live_workers import (
    ArbitrageExecutionTaskConsumer,
    ContinuousArbitrageScanner,
    ControlGuard,
    ControlPlaneLoader,
    ContinuousSpotScanner,
    RedisArbitrageTaskDispatcher,
    RedisExecutionTaskConsumer,
    RedisRepairTaskConsumer,
    RedisNodeTaskDispatcher,
    RedisSpotConsumer,
)
from app.runtime.repair_execution_service import RuntimeRepairExecutionService
from app.runtime.redis_flow import (
    NodeExecutionTaskPublisher,
    RepairTaskPublisher,
    RedisOpportunityDispatcher,
    UserNodeRouter,
    UserNodeRouteStore,
)
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.spot_arbitrage_probe import SpotArbitrageProbeService
from app.runtime.trade_execution_service import RuntimeTradeExecutionService
from app.runtime.worker_config import (
    AlertSettings,
    WorkerSettings,
    get_alert_settings,
    get_worker_settings,
    load_exchange_credentials_from_env,
    load_exchange_proxies_from_env,
)
from app.trading.risk_manager import RiskManager
from app.trading.order_recorder import OrderRecorder


class ScannerWorker:
    def __init__(self, scanner: ContinuousSpotScanner, settings: WorkerSettings) -> None:
        self.scanner = scanner
        self.settings = settings

    async def run(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        proxies_by_exchange: dict,
    ) -> None:
        symbols = self.settings.active_spot_symbols
        auto_map: dict[str, list[str]] | None = None
        if symbols == ["__auto__"]:
            discovery = SymbolDiscovery(self.scanner.flow_service.session_factory)
            auto_map = await discovery.discover(
                exchanges=exchanges,
                proxies_by_exchange=proxies_by_exchange,
            )
            if not auto_map:
                raise RuntimeError(
                    "auto symbol discovery returned zero symbols across "
                    f"{exchanges}"
                )
            symbols = sorted(auto_map.keys())

        await self.scanner.run(
            exchanges=exchanges,
            credentials_by_exchange=credentials_by_exchange,
            symbols=symbols,
            symbol_exchanges=auto_map,
            env_mode=self.settings.env_mode,
            proxies_by_exchange=proxies_by_exchange,
            orderbook_depth_limit=self.settings.orderbook_depth_limit,
            target_quote_amount=self.settings.target_quote_amount,
            max_iterations=None,
        )


class ArbitrageScannerWorker:
    def __init__(
        self, scanner: ContinuousArbitrageScanner, settings: WorkerSettings
    ) -> None:
        self.scanner = scanner
        self.settings = settings

    async def run(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        proxies_by_exchange: dict,
    ) -> None:
        symbols = self.settings.active_spot_symbols
        if symbols == ["__auto__"]:
            discovery = SwapSymbolDiscovery(self.scanner.flow_service.session_factory)
            symbol_swap_map = await discovery.discover(
                exchanges=exchanges,
                proxies_by_exchange=proxies_by_exchange,
            )
            if not symbol_swap_map:
                raise RuntimeError(
                    "arbitrage symbol discovery returned zero symbols across "
                    f"{exchanges}"
                )
        else:
            symbol_swap_map = await _build_fallback_swap_map(
                symbols=symbols,
                exchanges=exchanges,
                session_factory=self.scanner.flow_service.session_factory,
                proxies_by_exchange=proxies_by_exchange,
            )

        await self.scanner.run(
            exchanges=exchanges,
            credentials_by_exchange=credentials_by_exchange,
            symbol_swap_map=symbol_swap_map,
            env_mode=self.settings.env_mode,
            proxies_by_exchange=proxies_by_exchange,
            orderbook_depth_limit=self.settings.orderbook_depth_limit,
            max_iterations=None,
        )


async def _build_fallback_swap_map(
    *,
    symbols: list[str],
    exchanges: list[str],
    session_factory,
    proxies_by_exchange: dict | None = None,
) -> dict[str, dict[str, str]]:
    discovery = SwapSymbolDiscovery(session_factory)
    return await discovery.discover(
        exchanges=exchanges,
        proxies_by_exchange=proxies_by_exchange,
        min_exchange_count=1,
    )


class ConsumerWorker:
    def __init__(self, consumer: RedisSpotConsumer) -> None:
        self.consumer = consumer

    async def run(self, *, credentials_by_exchange: dict, stream_key: str) -> int:
        return await self.consumer.run(
            credentials_by_exchange=credentials_by_exchange,
            max_iterations=None,
        )


class ArbitrageExecutorWorker:
    def __init__(
        self,
        consumer: ArbitrageExecutionTaskConsumer,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.consumer = consumer
        self.poll_interval_seconds = poll_interval_seconds

    async def run(
        self,
        *,
        credentials_by_exchange: dict,
        stream_key: str,
        proxies_by_exchange: dict | None = None,
    ) -> int:
        processed = 0
        while True:
            current = await self.consumer.run_once(
                credentials_by_exchange=credentials_by_exchange,
                proxies_by_exchange=proxies_by_exchange,
            )
            processed += current
            if current == 0:
                await asyncio.sleep(self.poll_interval_seconds)


@dataclass(slots=True)
class DefaultWorkerFactory:
    settings: WorkerSettings
    event_router: Any
    session_factory: ExchangeClientFactory = field(default_factory=ExchangeClientFactory)
    spot_service: SpotArbitrageProbeService = field(default_factory=SpotArbitrageProbeService)
    trade_execution_service: RuntimeTradeExecutionService = field(
        default_factory=RuntimeTradeExecutionService
    )
    repair_execution_service: RuntimeRepairExecutionService = field(
        default_factory=RuntimeRepairExecutionService
    )

    def build_scanner_worker(self, *, redis_client: Redis) -> ScannerWorker:
        flow_service = LiveSpotFlowService(
            redis_client=redis_client,
            session_factory=self.session_factory,
            spot_service=self.spot_service,
        )
        scanner = ContinuousSpotScanner(
            flow_service=flow_service,
            poll_interval_seconds=self.settings.scanner_poll_interval_seconds,
            event_router=self.event_router,
            region=self.settings.worker_region,
        )
        return ScannerWorker(scanner=scanner, settings=self.settings)

    def build_arbitrage_scanner_worker(
        self, *, redis_client: Redis
    ) -> ArbitrageScannerWorker:
        flow_service = LiveArbitrageFlowService(
            redis_client=redis_client,
            session_factory=self.session_factory,
        )
        scanner = ContinuousArbitrageScanner(
            flow_service=flow_service,
            poll_interval_seconds=self.settings.arb_scanner_poll_interval_seconds,
            event_router=self.event_router,
            region=self.settings.worker_region,
        )
        return ArbitrageScannerWorker(scanner=scanner, settings=self.settings)

    def build_consumer_worker(self, *, redis_client: Redis) -> ConsumerWorker:
        dispatcher = RedisOpportunityDispatcher(self.spot_service)
        consumer = RedisSpotConsumer(
            redis_client=redis_client,
            dispatcher=dispatcher,
            stream_key="stream:spot_opps",
            block_ms=self.settings.consumer_block_ms,
            event_router=self.event_router,
            region=self.settings.worker_region,
        )
        return ConsumerWorker(consumer=consumer)

    def build_dispatcher_worker(self, *, redis_client: Redis) -> RedisNodeTaskDispatcher:
        control_guard = ControlGuard(
            control_plane_loader=ControlPlaneLoader(ControlPlaneStore(redis_client)),
            event_router=self.event_router,
            service_name="dispatcher",
            region=self.settings.worker_region,
        )
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
        return RedisNodeTaskDispatcher(
            redis_client=redis_client,
            user_ids=self.settings.dispatch_user_ids,
            dispatch_user_repository=dispatch_user_repository,
            account_repository=account_repository,
            route_resolver=UserNodeRouter(redis_client),
            task_publisher=NodeExecutionTaskPublisher(redis_client),
            stream_key=self.settings.dispatch_source_stream,
            strategy_repository=strategy_repository,
            control_guard=control_guard,
            task_repository=task_repository,
            block_ms=self.settings.consumer_block_ms,
            event_router=self.event_router,
            region=self.settings.worker_region,
            env_mode=self.settings.env_mode,
        )

    def build_arbitrage_dispatcher_worker(
        self, *, redis_client: Redis
    ) -> RedisArbitrageTaskDispatcher:
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
            stream_key=self.settings.resolved_dispatch_source_stream,
            block_ms=self.settings.consumer_block_ms,
            region=self.settings.worker_region,
            env_mode=self.settings.env_mode,
            event_router=self.event_router,
        )

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

    def build_arbitrage_executor_worker(
        self, *, redis_client: Redis
    ) -> ArbitrageExecutorWorker:
        if not self.settings.database_enabled:
            raise RuntimeError("arb_executor requires database_enabled=True")

        session_factory = build_session_factory(self.settings.database_url)
        session = session_factory()
        order_recorder = OrderRecorder(session_factory)
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
            event_router=self.event_router,
            region=self.settings.worker_region,
            order_recorder=order_recorder,
        )
        return ArbitrageExecutorWorker(
            consumer=consumer,
            poll_interval_seconds=self.settings.scanner_poll_interval_seconds,
        )

    def build_repair_worker(self, *, redis_client: Redis) -> ConsumerWorker:
        task_repository = None
        if self.settings.database_enabled:
            session_factory = build_session_factory(self.settings.database_url)
            session = session_factory()
            task_repository = TaskRepository(session)
        consumer = RedisRepairTaskConsumer(
            redis_client=redis_client,
            repair_service=self.repair_execution_service,
            stream_key=self.settings.resolved_repair_stream_key,
            task_repository=task_repository,
            env_mode=self.settings.env_mode,
            block_ms=self.settings.consumer_block_ms,
            event_router=self.event_router,
            region=self.settings.worker_region,
        )
        return ConsumerWorker(consumer=consumer)


def default_redis_factory(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


def build_event_router(alert_settings: AlertSettings) -> AlertRouter:
    feishu = None
    if alert_settings.alert_feishu_enabled and alert_settings.alert_feishu_webhook:
        feishu = FeishuNotifier(alert_settings.alert_feishu_webhook)

    email = None
    if (
        alert_settings.alert_email_enabled
        and alert_settings.alert_email_username
        and alert_settings.alert_email_password
        and alert_settings.alert_email_to
    ):
        email = EmailNotifier(
            smtp_host=alert_settings.alert_email_smtp_host,
            smtp_port=alert_settings.alert_email_smtp_port,
            username=alert_settings.alert_email_username,
            password=alert_settings.alert_email_password,
            recipients=alert_settings.alert_email_to,
        )

    return AlertRouter(
        logger=StructuredEventLogger(),
        feishu_notifier=feishu,
        email_notifier=email,
        alerts_enabled=alert_settings.alerts_enabled,
        feishu_enabled=alert_settings.alert_feishu_enabled,
        email_enabled=alert_settings.alert_email_enabled,
        success_spread_bps_threshold=alert_settings.alert_success_spread_bps_threshold,
        dedupe_window_seconds=alert_settings.alert_dedupe_window_seconds,
        opportunity_feishu_enabled=alert_settings.alert_opportunity_feishu_enabled,
    )


@dataclass(slots=True)
class WorkerApp:
    settings: WorkerSettings
    alert_settings: AlertSettings | None = None
    redis_factory: Callable[[str], Any] = default_redis_factory
    worker_factory: Any | None = None
    event_router: Any | None = None

    async def run(self) -> None:
        exchanges = self.settings.spot_exchanges
        router = self.event_router or build_event_router(
            self.alert_settings or get_alert_settings()
        )
        credentials_by_exchange = {}
        proxies_by_exchange = {}
        if self.settings.worker_role in ("arb_scanner", "scanner"):
            pass
        elif self.settings.worker_role != "executor":
            credentials_by_exchange = load_exchange_credentials_from_env(exchanges)
            proxies_by_exchange = load_exchange_proxies_from_env(exchanges)
            missing = sorted(set(exchanges) - set(credentials_by_exchange))
            if missing:
                await router.dispatch(
                    RuntimeEvent(
                        event_type="worker.start_failed",
                        level="CRITICAL",
                        service=self.settings.worker_role,
                        region=self.settings.worker_region,
                        message="worker start failed",
                        payload={
                            "error": f"missing credentials for exchanges: {','.join(missing)}"
                        },
                    )
                )
                raise RuntimeError(
                    f"missing credentials for exchanges: {','.join(missing)}"
                )

        redis_client = self.redis_factory(self.settings.redis_url)
        if (
            self.settings.worker_role == "dispatcher"
            and self.settings.user_node_routes
        ):
            route_store = UserNodeRouteStore(redis_client)
            await route_store.sync_default_routes(self.settings.user_node_routes)
        factory = self.worker_factory or DefaultWorkerFactory(
            settings=self.settings,
            event_router=router,
        )
        await router.dispatch(
            RuntimeEvent(
                event_type="worker.started",
                level="INFO",
                service=self.settings.worker_role,
                region=self.settings.worker_region,
                message="worker started",
                payload={"exchanges": exchanges},
            )
        )
        try:
            if self.settings.worker_role == "scanner":
                worker = factory.build_scanner_worker(redis_client=redis_client)
                await worker.run(
                    exchanges=exchanges,
                    credentials_by_exchange=credentials_by_exchange,
                    proxies_by_exchange=proxies_by_exchange,
                )
                return

            if self.settings.worker_role == "arb_scanner":
                worker = factory.build_arbitrage_scanner_worker(
                    redis_client=redis_client
                )
                await worker.run(
                    exchanges=exchanges,
                    credentials_by_exchange=credentials_by_exchange,
                    proxies_by_exchange=proxies_by_exchange,
                )
                return

            if self.settings.worker_role == "dispatcher":
                worker = factory.build_dispatcher_worker(redis_client=redis_client)
                await worker.run(max_iterations=None)
                return

            if self.settings.worker_role == "arb_dispatcher":
                worker = factory.build_arbitrage_dispatcher_worker(
                    redis_client=redis_client
                )
                await worker.run(max_iterations=None)
                return

            if self.settings.worker_role == "executor":
                worker = factory.build_executor_worker(redis_client=redis_client)
                await worker.run(
                    credentials_by_exchange=credentials_by_exchange,
                    stream_key=self.settings.resolved_executor_stream_key,
                )
                return

            if self.settings.worker_role == "arb_executor":
                worker = factory.build_arbitrage_executor_worker(
                    redis_client=redis_client
                )
                await worker.run(
                    credentials_by_exchange=credentials_by_exchange,
                    stream_key=self.settings.resolved_executor_stream_key,
                    proxies_by_exchange=proxies_by_exchange,
                )
                return

            if self.settings.worker_role == "repair":
                worker = factory.build_repair_worker(redis_client=redis_client)
                await worker.run(
                    credentials_by_exchange=credentials_by_exchange,
                    stream_key=self.settings.resolved_repair_stream_key,
                )
                return

            worker = factory.build_consumer_worker(redis_client=redis_client)
            await worker.run(
                credentials_by_exchange=credentials_by_exchange,
                stream_key="stream:spot_opps",
            )
        finally:
            await redis_client.aclose()
            await router.dispatch(
                RuntimeEvent(
                    event_type="worker.stopped",
                    level="INFO",
                    service=self.settings.worker_role,
                    region=self.settings.worker_region,
                    message="worker stopped",
                    payload={},
                )
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role",
        choices=[
            "scanner",
            "arb_scanner",
            "consumer",
            "dispatcher",
            "arb_dispatcher",
            "executor",
            "arb_executor",
            "repair",
        ],
        default=None,
    )
    return parser.parse_args(argv)


async def _run(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_worker_settings()
    if args.role is not None:
        settings = settings.model_copy(update={"worker_role": args.role})
    app = WorkerApp(settings=settings, alert_settings=get_alert_settings())
    await app.run()


def main(argv: list[str] | None = None) -> None:
    asyncio.run(_run(argv))


if __name__ == "__main__":
    main()
