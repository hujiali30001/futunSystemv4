import pytest

from app.market.opportunity import SpotOpportunity
from app.runtime.redis_flow import (
    MarketOpportunityPublisher,
    NodeExecutionTaskPublisher,
    RedisOpportunityDispatcher,
    UserNodeRouter,
    UserNodeRouteStore,
    build_node_execution_task_payload,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.zadds = []
        self.xadds = []
        self.sets = []
        self.deleted = []
        self.set_members = set()

    async def zadd(self, key, mapping):
        self.zadds.append((key, mapping))
        return 1

    async def xadd(self, key, fields):
        self.xadds.append((key, fields))
        return "1-0"

    async def set(self, key, value):
        self.values[key] = value
        self.sets.append((key, value))
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)
        return 1

    async def sadd(self, key, *values):
        self.set_members.update(values)
        return len(values)

    async def srem(self, key, *values):
        for value in values:
            self.set_members.discard(value)
        return len(values)

    async def smembers(self, key):
        return set(self.set_members)

    async def scan(self, cursor=0, match=None, count=None):
        matching_keys = sorted(
            key
            for key in self.values
            if match is None or key.startswith(match.rstrip("*"))
        )
        if cursor != 0:
            return 0, []
        return 0, matching_keys


class FakeSpotService:
    def __init__(self):
        self.calls = []

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


@pytest.mark.asyncio
async def test_publisher_writes_spot_opportunity_to_zset_and_stream():
    redis_client = FakeRedis()
    publisher = MarketOpportunityPublisher(redis_client, zset_key="arb:zset:spot", stream_key="stream:spot_opps")
    opportunity = SpotOpportunity(
        symbol="BTC/USDT",
        buy_exchange="bitget",
        sell_exchange="gate",
        buy_ask=100.0,
        sell_bid=102.0,
        spread_bps=200.0,
        redis_member="bitget:gate:BTC/USDT:1",
        timestamp=1.0,
        effective_buy_price=100.5,
        effective_sell_price=101.5,
        target_quote_amount=100.0,
        buy_depth_levels_used=2,
        sell_depth_levels_used=3,
    )

    await publisher.publish(opportunity)

    assert redis_client.zadds[0][0] == "arb:zset:spot"
    assert redis_client.xadds[0][0] == "stream:spot_opps"
    assert redis_client.xadds[0][1]["buy_exchange"] == "bitget"
    assert redis_client.xadds[0][1]["timestamp"] == "1.0"
    assert redis_client.xadds[0][1]["effective_buy_price"] == "100.5"
    assert redis_client.xadds[0][1]["effective_sell_price"] == "101.5"
    assert redis_client.xadds[0][1]["target_quote_amount"] == "100.0"
    assert redis_client.xadds[0][1]["buy_depth_levels_used"] == "2"
    assert redis_client.xadds[0][1]["sell_depth_levels_used"] == "3"


@pytest.mark.asyncio
async def test_dispatcher_transforms_stream_payload_into_task_call():
    service = FakeSpotService()
    dispatcher = RedisOpportunityDispatcher(service)

    await dispatcher.dispatch(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
        },
        credentials_by_exchange={
            "bitget": object(),
            "gate": object(),
        },
    )

    assert service.calls[0]["exchanges"] == ["bitget", "gate"]
    assert service.calls[0]["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_dispatcher_forwards_target_quote_amount_into_probe_service():
    service = FakeSpotService()
    dispatcher = RedisOpportunityDispatcher(service)

    await dispatcher.dispatch(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
            "target_quote_amount": "55.5",
        },
        credentials_by_exchange={
            "bitget": object(),
            "gate": object(),
        },
    )

    assert service.calls[0]["target_quote_amount"] == 55.5


@pytest.mark.asyncio
async def test_dispatcher_preserves_duplicate_exchange_payloads():
    service = FakeSpotService()
    dispatcher = RedisOpportunityDispatcher(service)
    credentials = object()

    await dispatcher.dispatch(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "okx",
            "sell_exchange": "okx",
        },
        credentials_by_exchange={
            "okx": credentials,
        },
    )

    assert service.calls[0]["exchanges"] == ["okx", "okx"]
    assert service.calls[0]["credentials_by_exchange"] == {"okx": credentials}


@pytest.mark.asyncio
async def test_dispatcher_accepts_execution_accounts_and_forwards_credentials_and_proxies():
    service = FakeSpotService()
    dispatcher = RedisOpportunityDispatcher(service)
    execution_accounts_by_exchange = {
        "bitget": {"account_id": "acc-bitget"},
        "gate": {"account_id": "acc-gate"},
    }
    credentials_by_exchange = {
        "bitget": object(),
        "gate": object(),
        "okx": object(),
    }
    proxies_by_exchange = {
        "bitget": {"http": "http://127.0.0.1:8001"},
        "gate": {"https": "http://127.0.0.1:8002"},
        "okx": {"http": "http://127.0.0.1:8003"},
    }

    await dispatcher.dispatch(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
        },
        execution_accounts_by_exchange=execution_accounts_by_exchange,
        credentials_by_exchange=credentials_by_exchange,
        proxies_by_exchange=proxies_by_exchange,
    )

    assert service.calls[0]["exchanges"] == ["bitget", "gate"]
    assert service.calls[0]["credentials_by_exchange"] is credentials_by_exchange
    assert service.calls[0]["proxies_by_exchange"] is proxies_by_exchange


@pytest.mark.asyncio
async def test_user_node_router_reads_route_key_from_redis():
    class FakeRedisWithRoutes:
        def __init__(self, route_values):
            self.route_values = route_values

        async def get(self, key):
            return self.route_values.get(key)

    redis_client = FakeRedisWithRoutes({"route:user_node:42": "node-a"})
    router = UserNodeRouter(redis_client)

    node_id = await router.get_user_node("42")

    assert node_id == "node-a"


@pytest.mark.asyncio
async def test_user_node_route_store_syncs_routes_into_redis():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)

    synced = await store.sync_routes(
        {
            "42": "node-a",
            "99": "node-b",
        }
    )

    assert synced == 2
    assert redis_client.sets == [
        ("route:user_node:42", "node-a"),
        ("route:user_node:99", "node-b"),
    ]


@pytest.mark.asyncio
async def test_route_store_lists_routes_from_index_and_single_keys():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)

    await store.set_user_node("42", "node-a")
    await store.set_user_node("99", "main")

    routes = await store.list_routes()

    assert routes == {"42": "node-a", "99": "main"}


@pytest.mark.asyncio
async def test_route_store_delete_removes_single_key_and_index_member():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)

    await store.set_user_node("42", "node-a")
    await store.delete_user_node("42")

    assert await store.get_user_node("42") is None
    assert "42" not in redis_client.set_members


@pytest.mark.asyncio
async def test_route_store_sync_defaults_only_fills_missing_routes():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)
    await store.set_user_node("42", "node-a")

    synced = await store.sync_default_routes({"42": "main", "99": "node-b"})

    assert synced == 1
    assert await store.get_user_node("42") == "node-a"
    assert await store.get_user_node("99") == "node-b"


@pytest.mark.asyncio
async def test_backfill_route_index_returns_newly_and_already_indexed_counts():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)
    redis_client.values.update(
        {
            "route:user_node:42": "node-a",
            "route:user_node:99": "main",
            "route:user_node:empty": "",
            UserNodeRouteStore.ROUTE_INDEX_KEY: "ignored",
        }
    )
    redis_client.set_members.add("42")

    result = await store.backfill_route_index()

    assert result == {
        "found": 2,
        "newly_indexed": 1,
        "already_indexed": 1,
        "skipped": 1,
        "dry_run": False,
    }
    assert redis_client.set_members == {"42", "99"}


@pytest.mark.asyncio
async def test_backfill_route_index_dry_run_reports_without_writing():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)
    redis_client.values.update(
        {
            "route:user_node:42": "node-a",
            "route:user_node:99": "main",
            "route:user_node:empty": "",
        }
    )
    redis_client.set_members.add("42")

    result = await store.backfill_route_index(dry_run=True)

    assert result == {
        "found": 2,
        "newly_indexed": 1,
        "already_indexed": 1,
        "skipped": 1,
        "dry_run": True,
    }
    assert redis_client.set_members == {"42"}


@pytest.mark.asyncio
async def test_node_execution_task_publisher_writes_node_task_stream():
    redis_client = FakeRedis()
    publisher = NodeExecutionTaskPublisher(redis_client)

    await publisher.publish(
        node_id="node-a",
        task_payload={
            "user_id": "42",
            "symbol": "BTC/USDT",
            "buy_exchange": "okx",
            "sell_exchange": "gate",
            "source_message_id": "1-0",
        },
    )

    assert redis_client.xadds[0][0] == "stream:spot_exec_tasks:node-a"
    assert redis_client.xadds[0][1]["user_id"] == "42"


def test_build_node_execution_task_payload_adds_routing_and_task_uuid_fields():
    task_payload = build_node_execution_task_payload(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "okx",
            "sell_exchange": "gate",
            "spread_bps": "25.0",
        },
        user_id="42",
        source_message_id="1-0",
        task_uuid="task-1",
    )

    assert task_payload["user_id"] == "42"
    assert task_payload["source_message_id"] == "1-0"
    assert task_payload["task_uuid"] == "task-1"
    assert task_payload["spread_bps"] == "25.0"


def test_build_node_execution_task_payload_includes_strategy_config_id():
    task_payload = build_node_execution_task_payload(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
        },
        user_id="42",
        source_message_id="1-0",
        task_uuid="task-1",
        strategy_config_id="11",
    )

    assert task_payload["task_uuid"] == "task-1"
    assert task_payload["strategy_config_id"] == "11"
    assert task_payload["user_id"] == "42"


def test_build_node_execution_task_payload_includes_bound_account_ids():
    task_payload = build_node_execution_task_payload(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
        },
        user_id="42",
        source_message_id="1-0",
        task_uuid="task-1",
        buy_account_id="101",
        sell_account_id="202",
    )

    assert task_payload["task_uuid"] == "task-1"
    assert task_payload["buy_account_id"] == "101"
    assert task_payload["sell_account_id"] == "202"
