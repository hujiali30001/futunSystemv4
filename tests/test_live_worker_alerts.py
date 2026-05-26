import pytest

from app.market.opportunity import SpotOpportunity
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.live_workers import (
    ContinuousSpotScanner,
    RedisExecutionTaskConsumer,
    RedisSpotConsumer,
)


class FakeEventRouter:
    def __init__(self):
        self.events = []

    async def dispatch(self, event: RuntimeEvent):
        self.events.append(event)


class FakeFlowService:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    async def run_once(self, **kwargs):
        if self.should_fail:
            raise RuntimeError("scanner failed")
        return SpotOpportunity(
            symbol=kwargs["symbol"],
            buy_exchange="bitget",
            sell_exchange="gate",
            buy_ask=100.0,
            sell_bid=100.88,
            spread_bps=88.0,
            redis_member="bitget:gate:BTC/USDT:1",
            timestamp=123.0,
            effective_buy_price=100.1,
            effective_sell_price=100.8,
            target_quote_amount=100.0,
            buy_depth_levels_used=1,
            sell_depth_levels_used=1,
        )


class FakeDispatcher:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    async def dispatch(self, payload, *, credentials_by_exchange=None, execution_accounts_by_exchange=None, proxies_by_exchange=None):
        if self.should_fail:
            raise RuntimeError("dispatch failed")
        return {"ok": True}


class FakeRedis:
    def __init__(self, xread_messages=None):
        self.xread_messages = xread_messages or [
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "88.0",
                        },
                    )
                ],
            )
        ]

    async def xread(self, streams, count=1, block=0):
        return self.xread_messages


@pytest.mark.asyncio
async def test_scanner_emits_opportunity_detected_event_from_dataclass_result():
    router = FakeEventRouter()
    scanner = ContinuousSpotScanner(
        flow_service=FakeFlowService(),
        poll_interval_seconds=0.0,
        event_router=router,
        region="default",
    )

    await scanner.run(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "binance": object(), "bybit": object(), "bitget": object(), "gate": object()},
        symbol="BTC/USDT",
        max_iterations=1,
    )

    assert [event.event_type for event in router.events] == [
        "opportunity.detected",
        "scanner.iteration.succeeded",
    ]
    assert router.events[0].payload == {
        "buy_exchange": "bitget",
        "sell_exchange": "gate",
        "spread_bps": 88.0,
    }


@pytest.mark.asyncio
async def test_consumer_emits_processed_event():
    router = FakeEventRouter()
    consumer = RedisSpotConsumer(
        redis_client=FakeRedis(),
        dispatcher=FakeDispatcher(),
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
        region="default",
    )

    processed = await consumer.run(
        credentials_by_exchange={"bitget": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert router.events[0].event_type == "consumer.message.processed"
    assert router.events[0].payload["message_id"] == "1-0"


@pytest.mark.asyncio
async def test_executor_emits_executor_processed_event():
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
                            "target_quote_amount": "100.0",
                        },
                    )
                ],
            )
        ]
    )
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=FakeDispatcher(),
        stream_key="stream:spot_exec_tasks:node-a",
        block_ms=0,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"bitget": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert router.events[0].event_type == "executor.task.processed"
    assert router.events[0].service == "executor"
    assert router.events[0].payload["message_id"] == "1-0"


@pytest.mark.asyncio
async def test_executor_emits_executor_failed_event():
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
                            "target_quote_amount": "100.0",
                        },
                    )
                ],
            )
        ]
    )
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=FakeDispatcher(should_fail=True),
        stream_key="stream:spot_exec_tasks:node-a",
        block_ms=0,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"bitget": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 0
    assert router.events[0].event_type == "executor.task.failed"
    assert router.events[0].service == "executor"
    assert router.events[0].payload["message_id"] == "1-0"
    assert router.events[0].payload["error"] == "dispatch failed"
