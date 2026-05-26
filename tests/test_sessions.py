import pytest

from app.exchanges.session_manager import (
    ExchangeAccountSession,
    ExchangeClientFactory,
    ExchangeCredentials,
    build_proxy_urls,
)


def test_build_proxy_urls_supports_http_auth():
    urls = build_proxy_urls(
        proxy_type="http",
        host="10.0.0.8",
        port=8080,
        username="alice",
        password="secret",
    )

    assert urls["http"] == "http://alice:secret@10.0.0.8:8080"
    assert urls["https"] == "http://alice:secret@10.0.0.8:8080"


def test_session_keeps_exchange_and_env_mode():
    session = ExchangeAccountSession(
        exchange="binance",
        env_mode="testnet",
        proxies={
            "http": "http://127.0.0.1:9000",
            "https": "http://127.0.0.1:9000",
        },
    )

    assert session.exchange == "binance"
    assert session.env_mode == "testnet"


def test_exchange_factory_injects_credentials_proxies_and_sandbox():
    class FakeExchangeClient:
        def __init__(self, config):
            self.config = config
            self.sandbox_enabled = False

        def set_sandbox_mode(self, enabled):
            self.sandbox_enabled = enabled

    class FakeCcxtModule:
        binance = FakeExchangeClient

    factory = ExchangeClientFactory(ccxt_module=FakeCcxtModule())
    session = factory.create_session(
        exchange="binance",
        env_mode="testnet",
        proxies={"http": "http://127.0.0.1:9000", "https": "http://127.0.0.1:9000"},
        credentials=ExchangeCredentials(
            api_key="demo-key",
            secret="demo-secret",
            password="demo-pass",
        ),
    )

    assert session.client.config["apiKey"] == "demo-key"
    assert session.client.config["secret"] == "demo-secret"
    assert session.client.config["password"] == "demo-pass"
    assert session.client.config["proxies"]["http"] == "http://127.0.0.1:9000"
    assert session.client.sandbox_enabled is True


class FakeClosableClient:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_session_close_is_idempotent_and_clears_client_state():
    client = FakeClosableClient()
    session = ExchangeAccountSession(
        exchange="okx",
        env_mode="testnet",
        proxies={},
        client=client,
        markets_loaded=True,
        markets={"BTC/USDT": {}},
    )

    await session.close()
    await session.close()

    assert client.close_calls == 1
    assert session.closed is True
    assert session.client is None
    assert session.markets == {}
    assert session.markets_loaded is False


@pytest.mark.asyncio
async def test_session_close_is_safe_when_client_missing_or_not_closable():
    session_without_client = ExchangeAccountSession(
        exchange="okx",
        env_mode="testnet",
        proxies={},
    )
    session_without_close = ExchangeAccountSession(
        exchange="okx",
        env_mode="testnet",
        proxies={},
        client=object(),
    )

    await session_without_client.close()
    await session_without_close.close()

    assert session_without_client.closed is True
    assert session_without_close.closed is True


def test_exchange_factory_creates_bybit_session():
    class FakeExchangeClient:
        def __init__(self, config):
            self.config = config
            self.sandbox_enabled = False
            self.urls = {"api": {"public": "", "private": ""}}

        def set_sandbox_mode(self, enabled):
            self.sandbox_enabled = enabled

    class FakeCcxtModule:
        bybit = FakeExchangeClient

    factory = ExchangeClientFactory(ccxt_module=FakeCcxtModule())
    session = factory.create_session(
        exchange="bybit",
        env_mode="testnet",
        proxies={},
        credentials=ExchangeCredentials(
            api_key="bybit-key",
            secret="bybit-secret",
        ),
    )

    assert session.exchange == "bybit"
    assert session.env_mode == "testnet"
    assert "apiKey" not in session.client.config
    assert session.client.sandbox_enabled is True
    assert session.client.urls["api"]["public"] == "https://api-demo.bybit.com"


def test_exchange_factory_creates_all_five_sessions():
    class FakeExchangeClient:
        def __init__(self, config):
            self.config = config
            self.sandbox_enabled = False

        def set_sandbox_mode(self, enabled):
            self.sandbox_enabled = enabled

    class FakeCcxtModule:
        okx = FakeExchangeClient
        binance = FakeExchangeClient
        bybit = FakeExchangeClient
        bitget = FakeExchangeClient
        gate = FakeExchangeClient

    factory = ExchangeClientFactory(ccxt_module=FakeCcxtModule())

    for exchange in ["okx", "binance", "bybit", "bitget", "gate"]:
        session = factory.create_session(
            exchange=exchange,
            env_mode="testnet",
            proxies={},
            credentials=ExchangeCredentials(api_key="k", secret="s"),
        )
        assert session.exchange == exchange
        assert session.client.sandbox_enabled is True
