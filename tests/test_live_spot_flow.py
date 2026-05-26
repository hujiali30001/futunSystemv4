import pytest

from app.exchanges.session_manager import ExchangeAccountSession, ExchangeCredentials
from app.market.opportunity import SpotOpportunity, spot_opportunity_to_payload
from app.runtime.live_spot_flow import LiveSpotFlowService, SymbolDiscovery


class FakeRedis:
    def __init__(self):
        self.zadds = []
        self.xadds = []

    async def zadd(self, key, mapping):
        self.zadds.append((key, mapping))
        return 1

    async def xadd(self, key, fields):
        self.xadds.append((key, fields))
        return "1-0"


class FakeClient:
    def __init__(self, orderbook, fail_on_fetch=False):
        self.orderbook = orderbook
        self.orderbook_calls = []
        self.fail_on_fetch = fail_on_fetch
        self.closed = False

    async def load_markets(self):
        return {"BTC/USDT": {"limits": {"amount": {"min": 0.001}}}}

    async def fetch_order_book(self, symbol, limit=5):
        if self.fail_on_fetch:
            raise RuntimeError("fetch orderbook failed")
        self.orderbook_calls.append((symbol, limit))
        return self.orderbook

    async def close(self):
        self.closed = True


class FakeFactory:
    def __init__(self):
        self.clients = {
            "okx": FakeClient(
                {
                    "symbol": "BTC/USDT",
                    "bids": [[100.0, 1.0]],
                    "asks": [[101.0, 1.0]],
                }
            ),
            "binance": FakeClient(
                {
                    "symbol": "BTC/USDT",
                    "bids": [[97.0, 1.0]],
                    "asks": [[98.0, 1.0], [98.5, 1.0]],
                }
            ),
            "bybit": FakeClient(
                {
                    "symbol": "BTC/USDT",
                    "bids": [[104.0, 0.5], [103.5, 0.5]],
                    "asks": [[105.0, 1.0]],
                }
            ),
            "bitget": FakeClient(
                {
                    "symbol": "BTC/USDT",
                    "bids": [[99.0, 1.0]],
                    "asks": [[100.0, 0.5], [101.0, 0.5]],
                }
            ),
            "gate": FakeClient(
                {
                    "symbol": "BTC/USDT",
                    "bids": [[103.0, 0.5], [102.0, 0.5]],
                    "asks": [[104.0, 1.0]],
                }
            ),
        }

    def create_session(self, exchange, env_mode, proxies, credentials):
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=self.clients[exchange],
        )


class FakeSpotService:
    def __init__(self):
        self.calls = []

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "message": "triggered"}


def test_spot_opportunity_to_payload_contains_runtime_boundary_fields():
    opportunity = SpotOpportunity(
        symbol="BTC/USDT",
        buy_exchange="bitget",
        sell_exchange="gate",
        buy_ask=100.0,
        sell_bid=102.0,
        spread_bps=200.0,
        redis_member="bitget:gate:BTC/USDT:1",
        timestamp=123.0,
        effective_buy_price=100.5,
        effective_sell_price=101.5,
        target_quote_amount=100.0,
        buy_depth_levels_used=2,
        sell_depth_levels_used=3,
    )

    payload = spot_opportunity_to_payload(opportunity)

    assert payload == {
        "symbol": "BTC/USDT",
        "buy_exchange": "bitget",
        "sell_exchange": "gate",
        "buy_ask": 100.0,
        "sell_bid": 102.0,
        "spread_bps": 200.0,
        "redis_member": "bitget:gate:BTC/USDT:1",
        "timestamp": 123.0,
        "effective_buy_price": 100.5,
        "effective_sell_price": 101.5,
        "target_quote_amount": 100.0,
        "buy_depth_levels_used": 2,
        "sell_depth_levels_used": 3,
    }


@pytest.mark.asyncio
async def test_live_flow_returns_spot_opportunity_without_inline_dispatch_by_default():
    redis_client = FakeRedis()
    service = FakeSpotService()
    factory = FakeFactory()
    flow = LiveSpotFlowService(
        redis_client=redis_client,
        session_factory=factory,
        spot_service=service,
    )

    result = await flow.run_once(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange={
            "okx": ExchangeCredentials(api_key="a", secret="b"),
            "binance": object(),
            "bybit": object(),
            "bitget": ExchangeCredentials(api_key="a", secret="b"),
            "gate": ExchangeCredentials(api_key="a", secret="b"),
        },
        symbol="BTC/USDT",
    )

    assert isinstance(result, SpotOpportunity)
    assert result.buy_exchange == "binance"
    assert result.sell_exchange == "bybit"
    assert result.effective_buy_price > result.buy_ask
    assert result.effective_sell_price < result.sell_bid
    assert result.target_quote_amount == 100.0
    assert redis_client.zadds
    assert redis_client.xadds
    assert service.calls == []
    assert factory.clients["bitget"].orderbook_calls == [("BTC/USDT", 5)]
    assert factory.clients["gate"].orderbook_calls == [("BTC/USDT", 5)]
    assert factory.clients["okx"].orderbook_calls == [("BTC/USDT", 5)]


@pytest.mark.asyncio
async def test_live_flow_can_optionally_inline_dispatch_payload():
    redis_client = FakeRedis()
    service = FakeSpotService()
    factory = FakeFactory()
    flow = LiveSpotFlowService(
        redis_client=redis_client,
        session_factory=factory,
        spot_service=service,
        inline_dispatch_enabled=True,
    )

    result = await flow.run_once(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange={
            "okx": ExchangeCredentials(api_key="a", secret="b"),
            "binance": object(),
            "bybit": object(),
            "bitget": ExchangeCredentials(api_key="a", secret="b"),
            "gate": ExchangeCredentials(api_key="a", secret="b"),
        },
        symbol="BTC/USDT",
    )

    assert isinstance(result, SpotOpportunity)
    assert service.calls[0]["exchanges"] == ["binance", "bybit"]


@pytest.mark.asyncio
async def test_live_flow_uses_requested_orderbook_depth_limit_and_quote_amount():
    redis_client = FakeRedis()
    service = FakeSpotService()
    factory = FakeFactory()
    flow = LiveSpotFlowService(
        redis_client=redis_client,
        session_factory=factory,
        spot_service=service,
    )

    result = await flow.run_once(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange={
            "okx": ExchangeCredentials(api_key="a", secret="b"),
            "binance": object(),
            "bybit": object(),
            "bitget": ExchangeCredentials(api_key="a", secret="b"),
            "gate": ExchangeCredentials(api_key="a", secret="b"),
        },
        symbol="BTC/USDT",
        orderbook_depth_limit=2,
        target_quote_amount=50.0,
    )

    assert isinstance(result, SpotOpportunity)
    assert result.target_quote_amount == 50.0
    assert factory.clients["bitget"].orderbook_calls == [("BTC/USDT", 2)]
    assert factory.clients["gate"].orderbook_calls == [("BTC/USDT", 2)]
    assert factory.clients["okx"].orderbook_calls == [("BTC/USDT", 2)]


@pytest.mark.asyncio
async def test_live_flow_closes_all_sessions_after_success():
    redis_client = FakeRedis()
    service = FakeSpotService()
    factory = FakeFactory()
    flow = LiveSpotFlowService(
        redis_client=redis_client,
        session_factory=factory,
        spot_service=service,
    )

    await flow.run_once(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange={
            "okx": ExchangeCredentials(api_key="a", secret="b"),
            "binance": object(),
            "bybit": object(),
            "bitget": ExchangeCredentials(api_key="a", secret="b"),
            "gate": ExchangeCredentials(api_key="a", secret="b"),
        },
        symbol="BTC/USDT",
    )

    assert factory.clients["okx"].closed is True
    assert factory.clients["bitget"].closed is True
    assert factory.clients["gate"].closed is True


@pytest.mark.asyncio
async def test_live_flow_closes_created_sessions_when_fetch_fails():
    redis_client = FakeRedis()
    service = FakeSpotService()
    factory = FakeFactory()
    factory.clients["okx"].fail_on_fetch = True
    flow = LiveSpotFlowService(
        redis_client=redis_client,
        session_factory=factory,
        spot_service=service,
    )

    with pytest.raises(RuntimeError, match="fetch orderbook failed"):
        await flow.run_once(
            exchanges=["okx", "binance", "bybit", "bitget", "gate"],
            credentials_by_exchange={
                "okx": ExchangeCredentials(api_key="a", secret="b"),
                "binance": object(),
                "bybit": object(),
                "bitget": ExchangeCredentials(api_key="a", secret="b"),
                "gate": ExchangeCredentials(api_key="a", secret="b"),
            },
            symbol="BTC/USDT",
        )

    assert factory.clients["okx"].closed is True
    assert factory.clients["bitget"].closed is True
    assert factory.clients["gate"].closed is True

def test_symbol_discovery_filters_usdt_spot_only():
    discovery = SymbolDiscovery.__new__(SymbolDiscovery)
    markets = {
        "BTC/USDT": {"quote": "USDT", "active": True, "type": "spot", "base": "BTC"},
        "ETH/USDT": {"quote": "USDT", "active": True, "type": "spot", "base": "ETH"},
        "BTC/USDC": {"quote": "USDC", "active": True, "type": "spot", "base": "BTC"},
        "SOL/USDT": {"quote": "USDT", "active": False, "type": "spot", "base": "SOL"},
        "1000PEPE/USDT": {"quote": "USDT", "active": True, "type": "spot", "base": "1000PEPE"},
        "1MSATS/USDT": {"quote": "USDT", "active": True, "type": "spot", "base": "1MSATS"},
        "BTC/USDT:USDT": {"quote": "USDT", "active": True, "type": "swap", "base": "BTC"},
    }
    result = discovery._extract_spot_usdt_pairs(markets)
    assert result == {"BTC/USDT", "ETH/USDT"}
