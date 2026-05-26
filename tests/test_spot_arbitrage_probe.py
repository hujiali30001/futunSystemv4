import asyncio

import pytest

from app.exchanges.session_manager import ExchangeAccountSession, ExchangeCredentials
from app.runtime.spot_arbitrage_probe import SpotArbitrageProbeService


class FakeClient:
    def __init__(self, exchange, bid, ask, fail_on_create=False):
        self.exchange = exchange
        self.bid = bid
        self.ask = ask
        self.fail_on_create = fail_on_create
        self.canceled_orders: set[str] = set()
        self.closed = False
        self.close_count = 0
        self.last_create_order = None

    async def load_markets(self):
        return {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "limits": {"amount": {"min": 0.001}},
                "precision": {"amount": 3, "price": 2},
            }
        }

    async def fetch_ticker(self, symbol):
        return {"symbol": symbol, "bid": self.bid, "ask": self.ask, "last": (self.bid + self.ask) / 2}

    def amount_to_precision(self, symbol, amount):
        return f"{amount:.3f}"

    def price_to_precision(self, symbol, price):
        return f"{price:.2f}"

    async def create_order(self, symbol, order_type, side, amount, price, params):
        if self.fail_on_create:
            raise RuntimeError("create order failed")
        self.last_create_order = {
            "id": f"{self.exchange}-{side}-1",
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "params": params,
        }
        return self.last_create_order

    async def fetch_order(self, order_id, symbol):
        status = "canceled" if order_id in self.canceled_orders else "open"
        return {"id": order_id, "symbol": symbol, "status": status}

    async def cancel_order(self, order_id, symbol):
        self.canceled_orders.add(order_id)
        return {"id": order_id, "symbol": symbol, "status": "canceled"}

    async def close(self):
        self.close_count += 1
        self.closed = True


class FakeFactory:
    def __init__(self):
        self.client_configs = {
            "okx": {"bid": 100.0, "ask": 101.0, "fail_on_create": False},
            "binance": {"bid": 97.0, "ask": 98.0, "fail_on_create": False},
            "bybit": {"bid": 104.0, "ask": 105.0, "fail_on_create": False},
            "bitget": {"bid": 99.0, "ask": 100.0, "fail_on_create": False},
            "gate": {"bid": 102.0, "ask": 103.0, "fail_on_create": False},
        }
        self.created_clients = {exchange: [] for exchange in self.client_configs}
        self.create_session_calls = []

    def create_session(self, exchange, env_mode, proxies, credentials):
        config = self.client_configs[exchange]
        client = FakeClient(
            exchange,
            config["bid"],
            config["ask"],
            fail_on_create=config["fail_on_create"],
        )
        self.created_clients[exchange].append(client)
        self.create_session_calls.append(exchange)
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=client,
        )


class CancelFailClient(FakeClient):
    async def cancel_order(self, order_id, symbol):
        raise RuntimeError("cancel order failed")


class FinalFetchFailClient(FakeClient):
    def __init__(self, exchange, bid, ask, fail_on_create=False):
        super().__init__(exchange, bid, ask, fail_on_create=fail_on_create)
        self.fetch_count = 0

    async def fetch_order(self, order_id, symbol):
        self.fetch_count += 1
        if self.fetch_count >= 2:
            raise RuntimeError("final fetch failed")
        return await super().fetch_order(order_id, symbol)


class CancelFailFactory(FakeFactory):
    def create_session(self, exchange, env_mode, proxies, credentials):
        config = self.client_configs[exchange]
        client = CancelFailClient(
            exchange,
            config["bid"],
            config["ask"],
            fail_on_create=config["fail_on_create"],
        )
        self.created_clients[exchange].append(client)
        self.create_session_calls.append(exchange)
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=client,
        )


class FinalFetchFailFactory(FakeFactory):
    def create_session(self, exchange, env_mode, proxies, credentials):
        config = self.client_configs[exchange]
        client = FinalFetchFailClient(
            exchange,
            config["bid"],
            config["ask"],
            fail_on_create=config["fail_on_create"],
        )
        self.created_clients[exchange].append(client)
        self.create_session_calls.append(exchange)
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=client,
        )


class ConcurrencySensitiveClient(FakeClient):
    active_calls = 0

    async def _enter_call(self):
        type(self).active_calls += 1
        await asyncio.sleep(0)
        if type(self).active_calls > 1:
            raise RuntimeError("concurrent exchange call detected")

    async def _exit_call(self):
        type(self).active_calls -= 1

    async def create_order(self, symbol, order_type, side, amount, price, params):
        await self._enter_call()
        try:
            return await super().create_order(symbol, order_type, side, amount, price, params)
        finally:
            await self._exit_call()

    async def fetch_order(self, order_id, symbol):
        await self._enter_call()
        try:
            return await super().fetch_order(order_id, symbol)
        finally:
            await self._exit_call()

    async def cancel_order(self, order_id, symbol):
        await self._enter_call()
        try:
            return await super().cancel_order(order_id, symbol)
        finally:
            await self._exit_call()


class ConcurrencySensitiveFactory(FakeFactory):
    def create_session(self, exchange, env_mode, proxies, credentials):
        config = self.client_configs[exchange]
        client = ConcurrencySensitiveClient(
            exchange,
            config["bid"],
            config["ask"],
            fail_on_create=config["fail_on_create"],
        )
        self.created_clients[exchange].append(client)
        self.create_session_calls.append(exchange)
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=client,
        )


class DelayedCloseClient(FakeClient):
    def __init__(self, exchange, bid, ask, fail_on_create=False):
        super().__init__(exchange, bid, ask, fail_on_create=fail_on_create)
        self.cleanup_done = False

    async def close(self):
        self.closed = True

        async def finalize_cleanup():
            await asyncio.sleep(0.02)
            self.cleanup_done = True

        asyncio.create_task(finalize_cleanup())


class DelayedCloseFactory(FakeFactory):
    def create_session(self, exchange, env_mode, proxies, credentials):
        config = self.client_configs[exchange]
        client = DelayedCloseClient(
            exchange,
            config["bid"],
            config["ask"],
            fail_on_create=config["fail_on_create"],
        )
        self.created_clients[exchange].append(client)
        self.create_session_calls.append(exchange)
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=client,
        )


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_records_leg_statuses_for_full_success():
    service = SpotArbitrageProbeService(session_factory=FakeFactory())
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "binance": ExchangeCredentials(api_key="a", secret="b"),
        "bybit": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.execution_status == "OPEN_HEDGED"
    assert getattr(result, "buy_leg_status", None) == "final_fetched"
    assert getattr(result, "sell_leg_status", None) == "final_fetched"
    assert getattr(result, "failed_stage", None) is None
    assert getattr(result, "buy_leg_error_code", None) is None
    assert getattr(result, "sell_leg_error_code", None) is None


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_records_create_sell_failure_details():
    factory = FakeFactory()
    factory.client_configs["bybit"]["fail_on_create"] = True
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "binance": ExchangeCredentials(api_key="a", secret="b"),
        "bybit": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.execution_status == "OPEN_PARTIAL"
    assert getattr(result, "buy_leg_status", None) == "created"
    assert getattr(result, "sell_leg_status", None) == "create_failed"
    assert getattr(result, "failed_stage", None) == "create_sell"
    assert getattr(result, "sell_leg_error_code", None) == "sell_create_failed"
    assert "create order failed" in (getattr(result, "sell_leg_error_detail", None) or "")


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_records_cancel_failure_details():
    service = SpotArbitrageProbeService(session_factory=CancelFailFactory())
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.execution_status == "OPEN_PARTIAL"
    assert getattr(result, "failed_stage", None) in {"cancel_buy", "cancel_sell"}
    assert getattr(result, "buy_leg_status", None) in {
        "cancel_failed",
        "cancelled",
        "final_fetched",
    }
    assert getattr(result, "sell_leg_status", None) in {
        "cancel_failed",
        "cancelled",
        "final_fetched",
    }
    assert {
        getattr(result, "buy_leg_error_code", None),
        getattr(result, "sell_leg_error_code", None),
    } & {"buy_cancel_failed", "sell_cancel_failed"}


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_records_final_fetch_failure_details():
    service = SpotArbitrageProbeService(session_factory=FinalFetchFailFactory())
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.execution_status == "OPEN_PARTIAL"
    assert getattr(result, "failed_stage", None) in {
        "fetch_final_buy",
        "fetch_final_sell",
    }
    assert getattr(result, "buy_leg_status", None) in {
        "final_fetch_failed",
        "final_fetched",
    }
    assert getattr(result, "sell_leg_status", None) in {
        "final_fetch_failed",
        "final_fetched",
    }
    assert {
        getattr(result, "buy_leg_error_code", None),
        getattr(result, "sell_leg_error_code", None),
    } & {"buy_final_fetch_failed", "sell_final_fetch_failed"}


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_selects_best_buy_and_sell_and_closes_orders():
    service = SpotArbitrageProbeService(session_factory=FakeFactory())
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "binance": ExchangeCredentials(api_key="a", secret="b"),
        "bybit": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.ok is True
    assert result.buy_exchange == "binance"
    assert result.sell_exchange == "bybit"
    assert result.buy_order_id == "binance-buy-1"
    assert result.sell_order_id == "bybit-sell-1"
    assert result.buy_final_status == "canceled"
    assert result.sell_final_status == "canceled"


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_returns_open_hedged_summary_when_both_legs_finish():
    service = SpotArbitrageProbeService(session_factory=FakeFactory())
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "binance": ExchangeCredentials(api_key="a", secret="b"),
        "bybit": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.execution_status == "OPEN_HEDGED"
    assert result.filled_exchanges == ["binance", "bybit"]
    assert result.failed_exchanges == []


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_returns_open_partial_summary_when_second_leg_create_fails():
    factory = FakeFactory()
    factory.client_configs["bybit"]["fail_on_create"] = True
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "binance": ExchangeCredentials(api_key="a", secret="b"),
        "bybit": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.ok is False
    assert result.execution_status == "OPEN_PARTIAL"
    assert result.filled_exchanges == ["binance"]
    assert result.failed_exchanges == ["bybit"]


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_closes_all_sessions_after_success():
    factory = FakeFactory()
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "binance": ExchangeCredentials(api_key="a", secret="b"),
        "bybit": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.ok is True
    assert len(factory.created_clients["okx"]) == 1
    assert len(factory.created_clients["binance"]) == 1
    assert len(factory.created_clients["bybit"]) == 1
    assert len(factory.created_clients["bitget"]) == 1
    assert len(factory.created_clients["gate"]) == 1
    assert factory.created_clients["okx"][0].closed is True
    assert factory.created_clients["binance"][0].closed is True
    assert factory.created_clients["bybit"][0].closed is True
    assert factory.created_clients["bitget"][0].closed is True
    assert factory.created_clients["gate"][0].closed is True


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_uses_target_quote_amount_to_reduce_order_size():
    factory = FakeFactory()
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        target_quote_amount=5.0,
        env_mode="testnet",
    )

    assert result.ok is True
    buy_client = factory.created_clients["bitget"][0]
    sell_client = factory.created_clients["gate"][0]
    assert float(buy_client.last_create_order["amount"]) == 0.051
    assert float(sell_client.last_create_order["amount"]) == 0.049


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_closes_all_sessions_after_order_failure():
    factory = FakeFactory()
    factory.client_configs["bybit"]["fail_on_create"] = True
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "binance": ExchangeCredentials(api_key="a", secret="b"),
        "bybit": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "binance", "bybit", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.ok is False
    assert len(factory.created_clients["okx"]) == 1
    assert len(factory.created_clients["binance"]) == 1
    assert len(factory.created_clients["bybit"]) == 1
    assert len(factory.created_clients["bitget"]) == 1
    assert len(factory.created_clients["gate"]) == 1
    assert factory.created_clients["okx"][0].closed is True
    assert factory.created_clients["binance"][0].closed is True
    assert factory.created_clients["bybit"][0].closed is True
    assert factory.created_clients["bitget"][0].closed is True
    assert factory.created_clients["gate"][0].closed is True


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_reuses_duplicate_exchange_session_and_closes_it_once():
    factory = FakeFactory()
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
    }

    result = await service.run_task(
        exchanges=["okx", "okx"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.ok is True
    assert result.buy_exchange == "okx"
    assert result.sell_exchange == "okx"
    assert factory.create_session_calls == ["okx"]
    assert len(factory.created_clients["okx"]) == 1
    assert factory.created_clients["okx"][0].close_count == 1


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_avoids_concurrent_exchange_calls():
    ConcurrencySensitiveClient.active_calls = 0
    factory = ConcurrencySensitiveFactory()
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.ok is True


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_waits_for_close_cleanup_before_returning():
    factory = DelayedCloseFactory()
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.ok is True
    assert factory.created_clients["okx"][0].cleanup_done is True
    assert factory.created_clients["gate"][0].cleanup_done is True
