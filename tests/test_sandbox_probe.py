import pytest

from app.exchanges.session_manager import ExchangeAccountSession, ExchangeCredentials
from app.runtime.sandbox_probe import SandboxProbeService


class FakeClient:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.closed = False
        self.created_order = None
        self.canceled = False

    async def load_markets(self):
        if self.should_fail:
            raise RuntimeError("load markets failed")
        return {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "limits": {"amount": {"min": 0.001}},
                "precision": {"amount": 3, "price": 2},
            }
        }

    async def fetch_balance(self):
        if self.should_fail:
            raise RuntimeError("fetch balance failed")
        return {"total": {"USDT": 125.0, "BTC": 0.0}}

    async def fetch_ticker(self, symbol):
        if self.should_fail:
            raise RuntimeError("fetch ticker failed")
        return {"symbol": symbol, "bid": 100.0, "ask": 101.0, "last": 100.5}

    def amount_to_precision(self, symbol, amount):
        return f"{amount:.3f}"

    def price_to_precision(self, symbol, price):
        return f"{price:.2f}"

    async def create_order(self, symbol, order_type, side, amount, price, params):
        if self.should_fail:
            raise RuntimeError("create order failed")
        self.created_order = {
            "id": "order-1",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "params": params,
        }
        return self.created_order

    async def fetch_order(self, order_id, symbol):
        if self.should_fail:
            raise RuntimeError("fetch order failed")
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "canceled" if self.canceled else "open",
        }

    async def cancel_order(self, order_id, symbol):
        if self.should_fail:
            raise RuntimeError("cancel order failed")
        self.canceled = True
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "canceled",
        }

    async def close(self):
        self.closed = True


class FakeFactory:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.last_session = None

    def create_session(self, exchange, env_mode, proxies, credentials):
        session = ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=FakeClient(should_fail=self.should_fail),
        )
        self.last_session = session
        return session


@pytest.mark.asyncio
async def test_probe_service_reports_success_for_connected_exchange():
    factory = FakeFactory()
    service = SandboxProbeService(session_factory=factory)

    result = await service.probe_exchange(
        exchange="binance",
        credentials=ExchangeCredentials(api_key="k", secret="s"),
        env_mode="testnet",
        proxies={},
    )

    assert result.exchange == "binance"
    assert result.ok is True
    assert "USDT" in result.non_zero_assets
    assert factory.last_session is not None
    assert factory.last_session.closed is True


@pytest.mark.asyncio
async def test_probe_service_reports_error_for_failing_exchange():
    service = SandboxProbeService(session_factory=FakeFactory(should_fail=True))

    result = await service.probe_exchange(
        exchange="okx",
        credentials=ExchangeCredentials(api_key="k", secret="s"),
        env_mode="testnet",
        proxies={},
    )

    assert result.exchange == "okx"
    assert result.ok is False
    assert "failed" in result.message


@pytest.mark.asyncio
async def test_order_probe_creates_and_cancels_safe_limit_order():
    service = SandboxProbeService(session_factory=FakeFactory())

    result = await service.probe_order_lifecycle(
        exchange="gate",
        credentials=ExchangeCredentials(api_key="k", secret="s"),
        symbol="BTC/USDT",
        env_mode="testnet",
        proxies={},
    )

    assert result.exchange == "gate"
    assert result.ok is True
    assert result.order_id == "order-1"
    assert result.created_status == "open"
    assert result.cancel_status == "canceled"
    assert result.final_status == "canceled"


@pytest.mark.asyncio
async def test_order_probe_closes_session_after_failure():
    factory = FakeFactory(should_fail=True)
    service = SandboxProbeService(session_factory=factory)

    result = await service.probe_order_lifecycle(
        exchange="gate",
        credentials=ExchangeCredentials(api_key="k", secret="s"),
        symbol="BTC/USDT",
        env_mode="testnet",
        proxies={},
    )

    assert result.exchange == "gate"
    assert result.ok is False
    assert factory.last_session is not None
    assert factory.last_session.closed is True
