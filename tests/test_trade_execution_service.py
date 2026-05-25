import pytest

from app.runtime.trade_execution_service import RuntimeTradeExecutionService


class FakeAdapter:
    def __init__(self, exchange: str, *, bid: float, ask: float, fail_create: bool = False):
        self.exchange = exchange
        self.bid = bid
        self.ask = ask
        self.fail_create = fail_create
        self.created_requests = []
        self.closed = False

    async def fetch_ticker(self, symbol: str) -> dict[str, float | str]:
        return {
            "symbol": symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": (self.bid + self.ask) / 2,
        }

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        _ = symbol
        return round(amount, 6)

    def price_to_precision(self, symbol: str, price: float) -> float:
        _ = symbol
        return round(price, 6)

    async def create_order(self, order_request):
        self.created_requests.append(order_request)
        if self.fail_create:
            raise RuntimeError(f"{self.exchange} create failed")
        return {"id": f"{self.exchange}-1"}

    async def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, exchange: str, *, bid: float, ask: float, fail_create: bool = False):
        self.exchange = exchange
        self.bid = bid
        self.ask = ask
        self.fail_create = fail_create
        self.mark_ready_calls = 0
        self.closed = False
        self.markets = {
            "BTC/USDT": {
                "limits": {"amount": {"min": 0.001}},
            }
        }

    async def mark_ready(self) -> None:
        self.mark_ready_calls += 1


class FakeSessionFactory:
    def __init__(self, configs: dict[str, dict[str, float | bool]]) -> None:
        self.configs = configs
        self.sessions = {}

    def create_session(self, *, exchange: str, env_mode: str, proxies: dict, credentials: object):
        _ = env_mode, proxies, credentials
        config = self.configs[exchange]
        session = FakeSession(
            exchange,
            bid=float(config["bid"]),
            ask=float(config["ask"]),
            fail_create=bool(config.get("fail_create", False)),
        )
        self.sessions[exchange] = session
        return session


@pytest.mark.asyncio
async def test_runtime_trade_execution_service_returns_open_hedged_for_two_successful_legs(
    monkeypatch,
):
    adapters = {}

    def build_adapter(session):
        adapter = FakeAdapter(
            session.exchange,
            bid=session.bid,
            ask=session.ask,
            fail_create=session.fail_create,
        )
        adapters[session.exchange] = adapter
        return adapter

    monkeypatch.setattr(
        "app.runtime.trade_execution_service.ExchangeAdapter",
        build_adapter,
    )
    service = RuntimeTradeExecutionService(
        session_factory=FakeSessionFactory(
            {
                "okx": {"bid": 100.0, "ask": 101.0},
                "gate": {"bid": 103.0, "ask": 104.0},
            }
        )
    )

    result = await service.run_task(
        exchanges=["okx", "gate"],
        credentials_by_exchange={"okx": object(), "gate": object()},
        execution_accounts_by_exchange={"okx": object(), "gate": object()},
        symbol="BTC/USDT",
        target_quote_amount=40.0,
        env_mode="testnet",
        proxies_by_exchange={"okx": {}, "gate": {}},
    )

    assert result.ok is True
    assert result.execution_status == "OPEN_HEDGED"
    assert result.filled_exchanges == ["okx", "gate"]
    assert result.failed_exchanges == []
    assert adapters["okx"].closed is True
    assert adapters["gate"].closed is True


@pytest.mark.asyncio
async def test_runtime_trade_execution_service_returns_open_partial_when_one_leg_fails(
    monkeypatch,
):
    def build_adapter(session):
        return FakeAdapter(
            session.exchange,
            bid=session.bid,
            ask=session.ask,
            fail_create=session.fail_create,
        )

    monkeypatch.setattr(
        "app.runtime.trade_execution_service.ExchangeAdapter",
        build_adapter,
    )
    service = RuntimeTradeExecutionService(
        session_factory=FakeSessionFactory(
            {
                "okx": {"bid": 100.0, "ask": 101.0},
                "gate": {"bid": 103.0, "ask": 104.0, "fail_create": True},
            }
        )
    )

    result = await service.run_task(
        exchanges=["okx", "gate"],
        credentials_by_exchange={"okx": object(), "gate": object()},
        execution_accounts_by_exchange={"okx": object(), "gate": object()},
        symbol="BTC/USDT",
        target_quote_amount=40.0,
        env_mode="testnet",
        proxies_by_exchange={"okx": {}, "gate": {}},
    )

    assert result.ok is False
    assert result.execution_status == "OPEN_PARTIAL"
    assert result.filled_exchanges == ["okx"]
    assert result.failed_exchanges == ["gate"]
