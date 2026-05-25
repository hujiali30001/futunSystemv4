# Depth Multi-Symbol Spot Scanning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the current single-symbol ticker-based spot scanner into a whitelist-driven multi-symbol scanner that computes opportunities from REST orderbook depth and writes richer opportunity payloads to Redis.

**Architecture:** Keep the existing worker, Redis, alerting, and deployment shell intact, and replace only the opportunity discovery internals. The implementation adds multi-symbol configuration, orderbook depth snapshots and weighted execution price calculation, extends `SpotOpportunity`, upgrades Redis payloads, and then validates the scanner on the remote host with a small symbol whitelist.

**Tech Stack:** Python 3.10+, asyncio, dataclasses, pydantic-settings, redis.asyncio, pytest, pytest-asyncio, ccxt async adapters

---

## Planned File Structure

**Modify**
- `app/runtime/worker_config.py`
- `app/exchanges/adapters.py`
- `app/market/opportunity.py`
- `app/runtime/redis_flow.py`
- `app/runtime/live_spot_flow.py`
- `app/runtime/live_workers.py`
- `tests/test_worker_config.py`
- `tests/test_market_scanner.py`
- `tests/test_redis_opportunity_flow.py`
- `tests/test_live_spot_flow.py`
- `tests/test_live_workers.py`
- `deploy/systemd/.env.worker.example`
- `docs/ops/live-workers-systemd.md`

## Task 1: Add Multi-Symbol And Depth Scanner Settings

**Files:**
- Modify: `app/runtime/worker_config.py`
- Modify: `tests/test_worker_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_worker_config.py
from app.runtime.worker_config import WorkerSettings


def test_worker_settings_parse_spot_symbols_csv():
    settings = WorkerSettings(
        spot_symbol="BTC/USDT",
        spot_symbols="BTC/USDT, ETH/USDT ,SOL/USDT",
        orderbook_depth_limit=5,
        target_quote_amount=100.0,
    )

    assert settings.spot_symbols == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    assert settings.orderbook_depth_limit == 5
    assert settings.target_quote_amount == 100.0


def test_worker_settings_fallback_to_single_spot_symbol_when_spot_symbols_missing():
    settings = WorkerSettings(spot_symbol="BTC/USDT")

    assert settings.active_spot_symbols == ["BTC/USDT"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_worker_config.py -q
```

Expected: FAIL because `spot_symbols`, `orderbook_depth_limit`, `target_quote_amount`, and `active_spot_symbols` do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
# app/runtime/worker_config.py
import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.exchanges.session_manager import ExchangeCredentials, build_proxy_urls


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    redis_url: str = "redis://127.0.0.1:6379/0"
    env_mode: str = "testnet"
    spot_symbol: str = "BTC/USDT"
    spot_symbols: Annotated[list[str], NoDecode] = Field(default_factory=list)
    spot_exchanges: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["okx", "bitget", "gate"]
    )
    orderbook_depth_limit: int = 5
    target_quote_amount: float = 100.0
    scanner_poll_interval_seconds: float = 1.0
    consumer_block_ms: int = 1000
    worker_role: Literal["scanner", "consumer"] = "scanner"
    worker_region: str = "default"

    @field_validator("spot_symbols", "spot_exchanges", mode="before")
    @classmethod
    def split_csv(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def active_spot_symbols(self) -> list[str]:
        return self.spot_symbols or [self.spot_symbol]


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    alerts_enabled: bool = True
    alert_feishu_enabled: bool = False
    alert_feishu_webhook: str | None = None
    alert_email_enabled: bool = False
    alert_email_smtp_host: str = "smtp.qq.com"
    alert_email_smtp_port: int = 465
    alert_email_username: str | None = None
    alert_email_password: str | None = None
    alert_email_to: Annotated[list[str], NoDecode] = Field(default_factory=list)
    alert_success_spread_bps_threshold: float = 0.0
    alert_dedupe_window_seconds: int = 60

    @field_validator("alert_email_to", mode="before")
    @classmethod
    def split_recipients(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()


@lru_cache(maxsize=1)
def get_alert_settings() -> AlertSettings:
    return AlertSettings()


def load_exchange_credential_from_env(exchange: str) -> ExchangeCredentials | None:
    prefix = exchange.upper().replace(".", "_")
    api_key = os.getenv(f"{prefix}_API_KEY")
    secret = os.getenv(f"{prefix}_SECRET")
    password = os.getenv(f"{prefix}_PASSWORD")
    if not api_key or not secret:
        return None
    return ExchangeCredentials(api_key=api_key, secret=secret, password=password)


def load_exchange_credentials_from_env(
    exchanges: list[str],
) -> dict[str, ExchangeCredentials]:
    credentials: dict[str, ExchangeCredentials] = {}
    for exchange in exchanges:
        loaded = load_exchange_credential_from_env(exchange)
        if loaded is not None:
            credentials[exchange] = loaded
    return credentials


def load_exchange_proxies_from_env(exchanges: list[str]) -> dict[str, dict[str, str]]:
    proxies_by_exchange: dict[str, dict[str, str]] = {}
    for exchange in exchanges:
        prefix = exchange.upper().replace(".", "_")
        host = os.getenv(f"{prefix}_PROXY_HOST")
        port = os.getenv(f"{prefix}_PROXY_PORT")
        if not host or not port:
            continue
        proxy_type = os.getenv(f"{prefix}_PROXY_TYPE", "http")
        username = os.getenv(f"{prefix}_PROXY_USERNAME")
        password = os.getenv(f"{prefix}_PROXY_PASSWORD")
        proxies_by_exchange[exchange] = build_proxy_urls(
            proxy_type=proxy_type,
            host=host,
            port=int(port),
            username=username,
            password=password,
        )
    return proxies_by_exchange
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_worker_config.py -q
```

Expected: PASS with the new multi-symbol setting tests green.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/worker_config.py tests/test_worker_config.py
git commit -m "feat: add multi-symbol depth scanner settings"
```

## Task 2: Add Orderbook Depth Pricing And Extend SpotOpportunity

**Files:**
- Modify: `app/exchanges/adapters.py`
- Modify: `app/market/opportunity.py`
- Modify: `tests/test_market_scanner.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_market_scanner.py
import pytest

from app.market.opportunity import OpportunityCalculator, OrderbookSnapshot


def test_calculator_builds_spot_opportunity_from_effective_depth_prices():
    calculator = OpportunityCalculator()

    buy_snapshot = OrderbookSnapshot(
        best_bid=99.0,
        best_ask=100.0,
        bids=[[99.0, 1.0]],
        asks=[[100.0, 0.5], [101.0, 0.5]],
    )
    sell_snapshot = OrderbookSnapshot(
        best_bid=103.0,
        best_ask=104.0,
        bids=[[103.0, 0.5], [102.0, 0.5]],
        asks=[[104.0, 1.0]],
    )

    opportunity = calculator.build_depth_spot_opportunity(
        symbol="BTC/USDT",
        buy_exchange="bitget",
        sell_exchange="gate",
        buy_snapshot=buy_snapshot,
        sell_snapshot=sell_snapshot,
        target_quote_amount=100.0,
    )

    assert opportunity.buy_exchange == "bitget"
    assert opportunity.sell_exchange == "gate"
    assert opportunity.effective_buy_price > 0
    assert opportunity.effective_sell_price > opportunity.effective_buy_price
    assert opportunity.target_quote_amount == 100.0
    assert opportunity.buy_depth_levels_used >= 1
    assert opportunity.sell_depth_levels_used >= 1


def test_calculator_returns_none_when_depth_is_insufficient():
    calculator = OpportunityCalculator()

    buy_snapshot = OrderbookSnapshot(
        best_bid=99.0,
        best_ask=100.0,
        bids=[[99.0, 0.1]],
        asks=[[100.0, 0.001]],
    )
    sell_snapshot = OrderbookSnapshot(
        best_bid=103.0,
        best_ask=104.0,
        bids=[[103.0, 0.001]],
        asks=[[104.0, 0.1]],
    )

    opportunity = calculator.build_depth_spot_opportunity(
        symbol="BTC/USDT",
        buy_exchange="bitget",
        sell_exchange="gate",
        buy_snapshot=buy_snapshot,
        sell_snapshot=sell_snapshot,
        target_quote_amount=100.0,
    )

    assert opportunity is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_market_scanner.py -q
```

Expected: FAIL because `build_depth_spot_opportunity()` and the extended `SpotOpportunity` fields do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
# app/market/opportunity.py
from dataclasses import dataclass
from time import time


@dataclass(slots=True)
class OrderbookSnapshot:
    best_bid: float
    best_ask: float
    bids: list[list[float]]
    asks: list[list[float]]


@dataclass(slots=True)
class Opportunity:
    symbol: str
    spot_exchange: str
    derivative_exchange: str
    open_spread_bps: float
    close_spread_bps: float
    funding_rate: float
    annualized_bps: float
    redis_member: str
    timestamp: float


@dataclass(slots=True)
class SpotOpportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_ask: float
    sell_bid: float
    spread_bps: float
    redis_member: str
    timestamp: float
    effective_buy_price: float
    effective_sell_price: float
    target_quote_amount: float
    buy_depth_levels_used: int
    sell_depth_levels_used: int


def spot_opportunity_to_payload(opportunity: SpotOpportunity) -> dict[str, object]:
    return {
        "symbol": opportunity.symbol,
        "buy_exchange": opportunity.buy_exchange,
        "sell_exchange": opportunity.sell_exchange,
        "buy_ask": opportunity.buy_ask,
        "sell_bid": opportunity.sell_bid,
        "spread_bps": opportunity.spread_bps,
        "redis_member": opportunity.redis_member,
        "timestamp": opportunity.timestamp,
        "effective_buy_price": opportunity.effective_buy_price,
        "effective_sell_price": opportunity.effective_sell_price,
        "target_quote_amount": opportunity.target_quote_amount,
        "buy_depth_levels_used": opportunity.buy_depth_levels_used,
        "sell_depth_levels_used": opportunity.sell_depth_levels_used,
    }


class OpportunityCalculator:
    def build_opportunity(
        self,
        *,
        symbol: str,
        spot_exchange: str,
        derivative_exchange: str,
        spot: OrderbookSnapshot,
        derivative: OrderbookSnapshot,
        funding_rate: float,
    ) -> Opportunity:
        open_spread = (derivative.best_ask - spot.best_ask) / spot.best_ask
        close_spread = (derivative.best_bid - spot.best_bid) / spot.best_bid
        current_time = time()
        return Opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            open_spread_bps=open_spread * 10000,
            close_spread_bps=close_spread * 10000,
            funding_rate=funding_rate,
            annualized_bps=(open_spread + funding_rate) * 365 * 10000,
            redis_member=f"{spot_exchange}:{derivative_exchange}:{symbol}:{int(current_time * 1000)}",
            timestamp=current_time,
        )

    def _weighted_buy_price(
        self,
        asks: list[list[float]],
        *,
        target_quote_amount: float,
    ) -> tuple[float, int] | None:
        remaining_quote = target_quote_amount
        acquired_base = 0.0
        spent_quote = 0.0
        levels_used = 0
        for price, size in asks:
            level_quote = float(price) * float(size)
            take_quote = min(level_quote, remaining_quote)
            if take_quote <= 0:
                continue
            spent_quote += take_quote
            acquired_base += take_quote / float(price)
            remaining_quote -= take_quote
            levels_used += 1
            if remaining_quote <= 1e-9:
                return spent_quote / acquired_base, levels_used
        return None

    def _weighted_sell_price(
        self,
        bids: list[list[float]],
        *,
        target_quote_amount: float,
    ) -> tuple[float, int] | None:
        remaining_quote = target_quote_amount
        sold_base = 0.0
        received_quote = 0.0
        levels_used = 0
        for price, size in bids:
            level_quote = float(price) * float(size)
            take_quote = min(level_quote, remaining_quote)
            if take_quote <= 0:
                continue
            received_quote += take_quote
            sold_base += take_quote / float(price)
            remaining_quote -= take_quote
            levels_used += 1
            if remaining_quote <= 1e-9:
                return received_quote / sold_base, levels_used
        return None

    def build_depth_spot_opportunity(
        self,
        *,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        buy_snapshot: OrderbookSnapshot,
        sell_snapshot: OrderbookSnapshot,
        target_quote_amount: float,
    ) -> SpotOpportunity | None:
        weighted_buy = self._weighted_buy_price(
            buy_snapshot.asks,
            target_quote_amount=target_quote_amount,
        )
        weighted_sell = self._weighted_sell_price(
            sell_snapshot.bids,
            target_quote_amount=target_quote_amount,
        )
        if weighted_buy is None or weighted_sell is None:
            return None
        effective_buy_price, buy_levels_used = weighted_buy
        effective_sell_price, sell_levels_used = weighted_sell
        current_time = time()
        spread = (effective_sell_price - effective_buy_price) / effective_buy_price
        return SpotOpportunity(
            symbol=symbol,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            buy_ask=buy_snapshot.best_ask,
            sell_bid=sell_snapshot.best_bid,
            spread_bps=spread * 10000,
            redis_member=f"{buy_exchange}:{sell_exchange}:{symbol}:{int(current_time * 1000)}",
            timestamp=current_time,
            effective_buy_price=effective_buy_price,
            effective_sell_price=effective_sell_price,
            target_quote_amount=target_quote_amount,
            buy_depth_levels_used=buy_levels_used,
            sell_depth_levels_used=sell_levels_used,
        )
```

```python
# app/exchanges/adapters.py
from dataclasses import dataclass


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: str
    order_type: str
    amount: float
    price: float | None = None
    reduce_only: bool = False
    post_only: bool = False


class ExchangeAdapter:
    def __init__(self, session) -> None:
        self.session = session
        self.client = session.client

    async def fetch_balance(self):
        return await self.client.fetch_balance()

    async def fetch_ticker(self, symbol: str):
        return await self.client.fetch_ticker(symbol)

    async def fetch_orderbook(self, symbol: str, limit: int = 5):
        return await self.client.fetch_order_book(symbol, limit=limit)

    async def create_order(self, request: OrderRequest):
        params = {}
        if request.reduce_only:
            params["reduceOnly"] = True
        if request.post_only:
            params["postOnly"] = True
        return await self.client.create_order(
            request.symbol,
            request.order_type,
            request.side,
            request.amount,
            request.price,
            params,
        )

    async def fetch_order(self, order_id: str, symbol: str):
        return await self.client.fetch_order(order_id, symbol)

    async def cancel_order(self, order_id: str, symbol: str):
        return await self.client.cancel_order(order_id, symbol)

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self.client.amount_to_precision(symbol, amount))

    def price_to_precision(self, symbol: str, price: float) -> float:
        return float(self.client.price_to_precision(symbol, price))

    async def close(self):
        return await self.client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_market_scanner.py -q
```

Expected: PASS with the new depth-pricing tests green.

- [ ] **Step 5: Commit**

```bash
git add app/exchanges/adapters.py app/market/opportunity.py tests/test_market_scanner.py
git commit -m "feat: add depth-based spot opportunity calculation"
```

## Task 3: Upgrade Redis Flow And Live Spot Flow To Multi-Symbol Depth Scanning

**Files:**
- Modify: `app/runtime/redis_flow.py`
- Modify: `app/runtime/live_spot_flow.py`
- Modify: `app/runtime/live_workers.py`
- Modify: `tests/test_redis_opportunity_flow.py`
- Modify: `tests/test_live_spot_flow.py`
- Modify: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_redis_opportunity_flow.py
import pytest

from app.market.opportunity import SpotOpportunity
from app.runtime.redis_flow import MarketOpportunityPublisher, RedisOpportunityDispatcher


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


class FakeSpotService:
    def __init__(self):
        self.calls = []

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


@pytest.mark.asyncio
async def test_publisher_writes_depth_fields_to_stream():
    redis_client = FakeRedis()
    publisher = MarketOpportunityPublisher(
        redis_client,
        zset_key="arb:zset:spot",
        stream_key="stream:spot_opps",
    )
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
        effective_sell_price=101.8,
        target_quote_amount=100.0,
        buy_depth_levels_used=2,
        sell_depth_levels_used=2,
    )

    await publisher.publish(opportunity)

    assert redis_client.xadds[0][1]["effective_buy_price"] == "100.5"
    assert redis_client.xadds[0][1]["effective_sell_price"] == "101.8"
    assert redis_client.xadds[0][1]["target_quote_amount"] == "100.0"
```

```python
# tests/test_live_spot_flow.py
import pytest

from app.exchanges.session_manager import ExchangeAccountSession, ExchangeCredentials
from app.market.opportunity import SpotOpportunity, spot_opportunity_to_payload
from app.runtime.live_spot_flow import LiveSpotFlowService


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
    def __init__(self, bid, ask, orderbook):
        self.bid = bid
        self.ask = ask
        self.orderbook = orderbook

    async def load_markets(self):
        return {"BTC/USDT": {"limits": {"amount": {"min": 0.001}}}}

    async def fetch_ticker(self, symbol):
        return {
            "symbol": symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": (self.bid + self.ask) / 2,
        }

    async def fetch_order_book(self, symbol, limit=5):
        return self.orderbook

    async def close(self):
        return None


class FakeFactory:
    def __init__(self):
        self.data = {
            "okx": {
                "ticker": (100.0, 101.0),
                "orderbook": {
                    "bids": [[100.0, 1.0], [99.5, 1.0]],
                    "asks": [[101.0, 1.0], [101.5, 1.0]],
                    "timestamp": 1,
                },
            },
            "bitget": {
                "ticker": (99.0, 100.0),
                "orderbook": {
                    "bids": [[99.0, 1.0], [98.5, 1.0]],
                    "asks": [[100.0, 1.0], [100.5, 1.0]],
                    "timestamp": 1,
                },
            },
            "gate": {
                "ticker": (102.0, 103.0),
                "orderbook": {
                    "bids": [[102.0, 1.0], [101.5, 1.0]],
                    "asks": [[103.0, 1.0], [103.5, 1.0]],
                    "timestamp": 1,
                },
            },
        }

    def create_session(self, exchange, env_mode, proxies, credentials):
        bid, ask = self.data[exchange]["ticker"]
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=FakeClient(bid, ask, self.data[exchange]["orderbook"]),
        )


class FakeSpotService:
    def __init__(self):
        self.calls = []

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "message": "triggered"}


@pytest.mark.asyncio
async def test_live_flow_uses_orderbook_depth_and_dispatches_extended_payload():
    redis_client = FakeRedis()
    service = FakeSpotService()
    flow = LiveSpotFlowService(
        redis_client=redis_client,
        session_factory=FakeFactory(),
        spot_service=service,
    )

    result = await flow.run_once(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={
            "okx": ExchangeCredentials(api_key="a", secret="b"),
            "bitget": ExchangeCredentials(api_key="a", secret="b"),
            "gate": ExchangeCredentials(api_key="a", secret="b"),
        },
        symbol="BTC/USDT",
        orderbook_depth_limit=5,
        target_quote_amount=100.0,
    )

    assert isinstance(result, SpotOpportunity)
    assert result.buy_exchange == "bitget"
    assert result.sell_exchange == "gate"
    assert result.effective_buy_price > 0
    assert result.effective_sell_price > result.effective_buy_price
    assert redis_client.xadds[0][1]["effective_buy_price"]
    assert service.calls[0]["symbol"] == "BTC/USDT"
```

```python
# tests/test_live_workers.py
import pytest

from app.market.opportunity import SpotOpportunity
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.live_workers import ContinuousSpotScanner, RedisSpotConsumer


class FakeFlowService:
    def __init__(self):
        self.calls = []

    async def run_once(self, **kwargs):
        self.calls.append(kwargs)
        return SpotOpportunity(
            symbol=kwargs["symbol"],
            buy_exchange="bitget",
            sell_exchange="gate",
            buy_ask=100.0,
            sell_bid=102.0,
            spread_bps=200.0,
            redis_member="bitget:gate:BTC/USDT:1",
            timestamp=123.0,
            effective_buy_price=100.5,
            effective_sell_price=101.8,
            target_quote_amount=100.0,
            buy_depth_levels_used=2,
            sell_depth_levels_used=2,
        )


class FakeDispatcher:
    def __init__(self):
        self.payloads = []

    async def dispatch(self, payload, *, credentials_by_exchange):
        self.payloads.append((payload, credentials_by_exchange))
        return {"ok": True}


class FakeEventRouter:
    def __init__(self):
        self.events = []

    async def dispatch(self, event: RuntimeEvent):
        self.events.append(event)


class FakeRedis:
    def __init__(self):
        self.read_calls = 0

    async def xread(self, streams, count=1, block=0):
        self.read_calls += 1
        if self.read_calls == 1:
            return [
                (
                    "stream:spot_opps",
                    [
                        (
                            "1-0",
                            {
                                "symbol": "BTC/USDT",
                                "buy_exchange": "bitget",
                                "sell_exchange": "gate",
                                "spread_bps": "200.0",
                            },
                        )
                    ],
                )
            ]
        return []


@pytest.mark.asyncio
async def test_continuous_scanner_runs_all_active_symbols():
    service = FakeFlowService()
    scanner = ContinuousSpotScanner(flow_service=service, poll_interval_seconds=0.0)

    await scanner.run(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "bitget": object(), "gate": object()},
        symbols=["BTC/USDT", "ETH/USDT"],
        max_iterations=1,
        orderbook_depth_limit=5,
        target_quote_amount=100.0,
    )

    assert [call["symbol"] for call in service.calls] == ["BTC/USDT", "ETH/USDT"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_redis_opportunity_flow.py tests/test_live_spot_flow.py tests/test_live_workers.py -q
```

Expected: FAIL because the current publisher does not write depth fields, `run_once()` still uses ticker fetches, and the scanner still only accepts one `symbol`.

- [ ] **Step 3: Write the minimal implementation**

```python
# app/runtime/redis_flow.py
from app.market.opportunity import SpotOpportunity


class MarketOpportunityPublisher:
    def __init__(self, redis_client, *, zset_key: str, stream_key: str) -> None:
        self.redis_client = redis_client
        self.zset_key = zset_key
        self.stream_key = stream_key

    async def publish(self, opportunity: SpotOpportunity) -> None:
        await self.redis_client.zadd(
            self.zset_key,
            {opportunity.redis_member: opportunity.spread_bps},
        )
        await self.redis_client.xadd(
            self.stream_key,
            {
                "symbol": opportunity.symbol,
                "buy_exchange": opportunity.buy_exchange,
                "sell_exchange": opportunity.sell_exchange,
                "buy_ask": str(opportunity.buy_ask),
                "sell_bid": str(opportunity.sell_bid),
                "spread_bps": str(opportunity.spread_bps),
                "redis_member": opportunity.redis_member,
                "effective_buy_price": str(opportunity.effective_buy_price),
                "effective_sell_price": str(opportunity.effective_sell_price),
                "target_quote_amount": str(opportunity.target_quote_amount),
                "buy_depth_levels_used": str(opportunity.buy_depth_levels_used),
                "sell_depth_levels_used": str(opportunity.sell_depth_levels_used),
            },
        )


class RedisOpportunityDispatcher:
    def __init__(self, spot_service) -> None:
        self.spot_service = spot_service

    async def dispatch(self, payload: dict, *, credentials_by_exchange: dict) -> object:
        exchanges = [payload["buy_exchange"], payload["sell_exchange"]]
        scoped_credentials = {
            exchange: credentials_by_exchange[exchange]
            for exchange in exchanges
        }
        return await self.spot_service.run_task(
            exchanges=exchanges,
            credentials_by_exchange=scoped_credentials,
            symbol=payload["symbol"],
            env_mode="testnet",
        )
```

```python
# app/runtime/live_spot_flow.py
import asyncio

from app.exchanges.adapters import ExchangeAdapter
from app.exchanges.session_manager import ExchangeClientFactory
from app.market.opportunity import (
    OpportunityCalculator,
    OrderbookSnapshot,
    SpotOpportunity,
    spot_opportunity_to_payload,
)
from app.runtime.redis_flow import MarketOpportunityPublisher, RedisOpportunityDispatcher


class LiveSpotFlowService:
    def __init__(
        self,
        *,
        redis_client,
        session_factory: ExchangeClientFactory,
        spot_service,
    ) -> None:
        self.redis_client = redis_client
        self.session_factory = session_factory
        self.spot_service = spot_service
        self.calculator = OpportunityCalculator()
        self.publisher = MarketOpportunityPublisher(
            redis_client,
            zset_key="arb:zset:spot",
            stream_key="stream:spot_opps",
        )
        self.dispatcher = RedisOpportunityDispatcher(spot_service)

    async def run_once(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        symbol: str,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
        orderbook_depth_limit: int = 5,
        target_quote_amount: float = 100.0,
    ) -> SpotOpportunity | None:
        sessions = {}
        adapters = {}
        try:
            for exchange in exchanges:
                session = self.session_factory.create_session(
                    exchange=exchange,
                    env_mode=env_mode,
                    proxies=(proxies_by_exchange or {}).get(exchange, {}),
                    credentials=credentials_by_exchange[exchange],
                )
                await session.mark_ready()
                sessions[exchange] = session
                adapters[exchange] = ExchangeAdapter(session)

            orderbooks = {
                exchange: await adapters[exchange].fetch_orderbook(
                    symbol,
                    limit=orderbook_depth_limit,
                )
                for exchange in exchanges
            }
            snapshots = {}
            for exchange, orderbook in orderbooks.items():
                if not orderbook.get("bids") or not orderbook.get("asks"):
                    continue
                snapshots[exchange] = OrderbookSnapshot(
                    best_bid=float(orderbook["bids"][0][0]),
                    best_ask=float(orderbook["asks"][0][0]),
                    bids=[[float(price), float(size)] for price, size in orderbook["bids"]],
                    asks=[[float(price), float(size)] for price, size in orderbook["asks"]],
                )
            if len(snapshots) < 2:
                return None

            best_opportunity = None
            for buy_exchange, buy_snapshot in snapshots.items():
                for sell_exchange, sell_snapshot in snapshots.items():
                    if buy_exchange == sell_exchange:
                        continue
                    candidate = self.calculator.build_depth_spot_opportunity(
                        symbol=symbol,
                        buy_exchange=buy_exchange,
                        sell_exchange=sell_exchange,
                        buy_snapshot=buy_snapshot,
                        sell_snapshot=sell_snapshot,
                        target_quote_amount=target_quote_amount,
                    )
                    if candidate is None:
                        continue
                    if best_opportunity is None or candidate.spread_bps > best_opportunity.spread_bps:
                        best_opportunity = candidate
            if best_opportunity is None:
                return None

            await self.publisher.publish(best_opportunity)
            payload = spot_opportunity_to_payload(best_opportunity)
            await self.dispatcher.dispatch(
                {
                    "symbol": payload["symbol"],
                    "buy_exchange": payload["buy_exchange"],
                    "sell_exchange": payload["sell_exchange"],
                },
                credentials_by_exchange=credentials_by_exchange,
            )
            return best_opportunity
        finally:
            await asyncio.gather(
                *[adapter.close() for adapter in adapters.values()],
                return_exceptions=True,
            )
```

```python
# app/runtime/live_workers.py
import asyncio

from app.runtime.runtime_events import RuntimeEvent


class ContinuousSpotScanner:
    def __init__(
        self,
        *,
        flow_service,
        poll_interval_seconds: float = 1.0,
        event_router=None,
        region: str = "default",
    ) -> None:
        self.flow_service = flow_service
        self.poll_interval_seconds = poll_interval_seconds
        self.event_router = event_router
        self.region = region

    async def run(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        symbols: list[str],
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
        max_iterations: int | None = None,
        orderbook_depth_limit: int = 5,
        target_quote_amount: float = 100.0,
    ) -> None:
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            for symbol in symbols:
                try:
                    result = await self.flow_service.run_once(
                        exchanges=exchanges,
                        credentials_by_exchange=credentials_by_exchange,
                        symbol=symbol,
                        env_mode=env_mode,
                        proxies_by_exchange=proxies_by_exchange,
                        orderbook_depth_limit=orderbook_depth_limit,
                        target_quote_amount=target_quote_amount,
                    )
                    if self.event_router is not None and result is not None:
                        await self.event_router.dispatch(
                            RuntimeEvent(
                                event_type="opportunity.detected",
                                level="INFO",
                                service="scanner",
                                region=self.region,
                                symbol=symbol,
                                message="opportunity detected",
                                payload={
                                    "buy_exchange": result.buy_exchange,
                                    "sell_exchange": result.sell_exchange,
                                    "spread_bps": result.spread_bps,
                                },
                            )
                        )
                        await self.event_router.dispatch(
                            RuntimeEvent(
                                event_type="scanner.iteration.succeeded",
                                level="INFO",
                                service="scanner",
                                region=self.region,
                                symbol=symbol,
                                message="scanner iteration succeeded",
                                payload={"exchanges": exchanges},
                            )
                        )
                except Exception as exc:
                    if self.event_router is not None:
                        await self.event_router.dispatch(
                            RuntimeEvent(
                                event_type="scanner.iteration.failed",
                                level="ERROR",
                                service="scanner",
                                region=self.region,
                                symbol=symbol,
                                message="scanner iteration failed",
                                payload={"error": str(exc)},
                            )
                        )
            iteration += 1
            if max_iterations is None or iteration < max_iterations:
                await asyncio.sleep(self.poll_interval_seconds)


class RedisSpotConsumer:
    def __init__(
        self,
        *,
        redis_client,
        dispatcher,
        stream_key: str,
        block_ms: int = 1000,
        event_router=None,
        region: str = "default",
    ) -> None:
        self.redis_client = redis_client
        self.dispatcher = dispatcher
        self.stream_key = stream_key
        self.block_ms = block_ms
        self.event_router = event_router
        self.region = region
        self.last_id = "0-0"

    async def run(
        self,
        *,
        credentials_by_exchange: dict,
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
                    try:
                        await self.dispatcher.dispatch(
                            payload,
                            credentials_by_exchange=credentials_by_exchange,
                        )
                        self.last_id = message_id
                        processed += 1
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                RuntimeEvent(
                                    event_type="consumer.message.processed",
                                    level="INFO",
                                    service="consumer",
                                    region=self.region,
                                    symbol=payload.get("symbol"),
                                    message="consumer message processed",
                                    payload={
                                        "message_id": message_id,
                                        "buy_exchange": payload.get("buy_exchange"),
                                        "sell_exchange": payload.get("sell_exchange"),
                                        "spread_bps": float(payload.get("spread_bps", 0.0)),
                                    },
                                )
                            )
                    except Exception as exc:
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                RuntimeEvent(
                                    event_type="consumer.message.failed",
                                    level="ERROR",
                                    service="consumer",
                                    region=self.region,
                                    symbol=payload.get("symbol"),
                                    message="consumer message failed",
                                    payload={
                                        "message_id": message_id,
                                        "error": str(exc),
                                    },
                                )
                            )
            iteration += 1
        return processed
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_redis_opportunity_flow.py tests/test_live_spot_flow.py tests/test_live_workers.py -q
```

Expected: PASS with the new depth and multi-symbol tests green.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/redis_flow.py app/runtime/live_spot_flow.py app/runtime/live_workers.py tests/test_redis_opportunity_flow.py tests/test_live_spot_flow.py tests/test_live_workers.py
git commit -m "feat: add depth-based multi-symbol spot scanning"
```

## Task 4: Update Env And Ops Docs, Then Validate A Small Whitelist On The Remote Host

**Files:**
- Modify: `deploy/systemd/.env.worker.example`
- Modify: `docs/ops/live-workers-systemd.md`

- [ ] **Step 1: Write the failing integration-oriented tests**

```python
# tests/test_live_workers.py
import pytest

from app.market.opportunity import SpotOpportunity
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.live_workers import ContinuousSpotScanner, RedisSpotConsumer


class FakeFlowService:
    def __init__(self):
        self.calls = []

    async def run_once(self, **kwargs):
        self.calls.append(kwargs)
        return SpotOpportunity(
            symbol=kwargs["symbol"],
            buy_exchange="bitget",
            sell_exchange="gate",
            buy_ask=100.0,
            sell_bid=102.0,
            spread_bps=200.0,
            redis_member=f"bitget:gate:{kwargs['symbol']}:1",
            timestamp=123.0,
            effective_buy_price=100.5,
            effective_sell_price=101.8,
            target_quote_amount=100.0,
            buy_depth_levels_used=2,
            sell_depth_levels_used=2,
        )


class FakeDispatcher:
    def __init__(self):
        self.payloads = []

    async def dispatch(self, payload, *, credentials_by_exchange):
        self.payloads.append((payload, credentials_by_exchange))
        return {"ok": True}


class FakeEventRouter:
    def __init__(self):
        self.events = []

    async def dispatch(self, event: RuntimeEvent):
        self.events.append(event)


class FakeRedis:
    def __init__(self):
        self.read_calls = 0

    async def xread(self, streams, count=1, block=0):
        self.read_calls += 1
        if self.read_calls == 1:
            return [
                (
                    "stream:spot_opps",
                    [
                        (
                            "1-0",
                            {
                                "symbol": "BTC/USDT",
                                "buy_exchange": "bitget",
                                "sell_exchange": "gate",
                                "spread_bps": "200.0",
                            },
                        )
                    ],
                )
            ]
        return []


@pytest.mark.asyncio
async def test_continuous_scanner_emits_events_for_each_active_symbol():
    service = FakeFlowService()
    router = FakeEventRouter()
    scanner = ContinuousSpotScanner(
        flow_service=service,
        poll_interval_seconds=0.0,
        event_router=router,
        region="default",
    )

    await scanner.run(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "bitget": object(), "gate": object()},
        symbols=["BTC/USDT", "ETH/USDT"],
        max_iterations=1,
        orderbook_depth_limit=5,
        target_quote_amount=100.0,
    )

    assert [event.symbol for event in router.events if event.event_type == "opportunity.detected"] == [
        "BTC/USDT",
        "ETH/USDT",
    ]
```

- [ ] **Step 2: Run the focused tests to verify they fail only if missing**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_live_workers.py -q
```

Expected: if the multi-symbol scanner event test does not exist yet, add it and watch it fail first; if it already exists and passes because of Task 3, leave the file unchanged and continue.

- [ ] **Step 3: Update the env example and deployment doc**

```dotenv
# deploy/systemd/.env.worker.example
REDIS_URL=redis://127.0.0.1:6379/0
ENV_MODE=testnet
SPOT_SYMBOL=BTC/USDT
SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
SPOT_EXCHANGES=okx,bitget,gate
ORDERBOOK_DEPTH_LIMIT=5
TARGET_QUOTE_AMOUNT=100
SCANNER_POLL_INTERVAL_SECONDS=2
CONSUMER_BLOCK_MS=1000
WORKER_REGION=default
# Alert routing
# Feishu notifications use Chinese text and only send success alerts for
# opportunity.detected events above the spread threshold below.
# QQ email notifications use Chinese text for CRITICAL alerts when SMTP
# credentials and recipients are set.
# consumer.message.processed no longer sends external notifications.
ALERTS_ENABLED=1
ALERT_FEISHU_ENABLED=1
ALERT_FEISHU_WEBHOOK=
ALERT_EMAIL_ENABLED=1
ALERT_EMAIL_SMTP_HOST=smtp.qq.com
ALERT_EMAIL_SMTP_PORT=465
ALERT_EMAIL_USERNAME=
ALERT_EMAIL_PASSWORD=
ALERT_EMAIL_TO=
ALERT_SUCCESS_SPREAD_BPS_THRESHOLD=20
ALERT_DEDUPE_WINDOW_SECONDS=300
# Exchange credentials
OKX_API_KEY=
OKX_SECRET=
OKX_PASSWORD=
OKX_PROXY_TYPE=http
OKX_PROXY_HOST=
OKX_PROXY_PORT=
OKX_PROXY_USERNAME=
OKX_PROXY_PASSWORD=
BITGET_API_KEY=
BITGET_SECRET=
BITGET_PASSWORD=
BITGET_PROXY_TYPE=http
BITGET_PROXY_HOST=
BITGET_PROXY_PORT=
BITGET_PROXY_USERNAME=
BITGET_PROXY_PASSWORD=
GATE_API_KEY=
GATE_SECRET=
GATE_PASSWORD=
GATE_PROXY_TYPE=http
GATE_PROXY_HOST=
GATE_PROXY_PORT=
GATE_PROXY_USERNAME=
GATE_PROXY_PASSWORD=
```

```markdown
# docs/ops/live-workers-systemd.md
## Depth Scanner Config

- `SPOT_SYMBOLS` is the preferred whitelist for multi-symbol scanning.
- If `SPOT_SYMBOLS` is empty, the scanner falls back to `SPOT_SYMBOL`.
- `ORDERBOOK_DEPTH_LIMIT=5` keeps the first rollout bounded to the top five levels.
- `TARGET_QUOTE_AMOUNT=100` defines the quote-size simulation used to compute `effective_buy_price` and `effective_sell_price`.
- `SCANNER_POLL_INTERVAL_SECONDS=2` is the recommended initial interval for the depth-based scanner.

## Remote Validation

1. Upload the updated `worker_config.py`, `adapters.py`, `opportunity.py`, `redis_flow.py`, `live_spot_flow.py`, `live_workers.py`, `.env.worker.example`, and `docs/ops/live-workers-systemd.md`.
2. Update the remote `.env.worker` to include:

```dotenv
SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
ORDERBOOK_DEPTH_LIMIT=5
TARGET_QUOTE_AMOUNT=100
SCANNER_POLL_INTERVAL_SECONDS=2
```

3. Restart both services.
4. Confirm:
   - scanner stays `active`
   - Redis `arb:zset:spot` and `stream:spot_opps` keep growing
   - stream entries now include `effective_buy_price`, `effective_sell_price`, and depth-level fields
   - at least two whitelist symbols appear in recent scanner activity
```

- [ ] **Step 4: Run full local regression**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests -q
```

Expected: PASS with the existing suite plus the new multi-symbol depth tests.

- [ ] **Step 5: Sync to the remote host and validate the whitelist scanner**

Run:

```powershell
$keyDir = "d:\old\FuRunSystemV4\.tmp-ssh"
New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
$keyPath = Join-Path $keyDir "futunsystemv3_deploy_ed25519"
Copy-Item -Force "d:\old\FuRunSystemV4\.keys\futunsystemv3_deploy_ed25519" $keyPath
& "C:\Windows\System32\icacls.exe" $keyPath /inheritance:r | Out-Null
& "C:\Windows\System32\icacls.exe" $keyPath /grant:r "${env:USERNAME}:(R)" | Out-Null

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\runtime\worker_config.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/runtime/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\exchanges\adapters.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/exchanges/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\market\opportunity.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/market/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\runtime\redis_flow.py" `
  "d:\old\FuRunSystemV4\app\runtime\live_spot_flow.py" `
  "d:\old\FuRunSystemV4\app\runtime\live_workers.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/runtime/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/deploy/systemd/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/docs/ops/
```

Then update the remote `.env.worker`:

```powershell
& "C:\Windows\System32\OpenSSH\ssh.exe" -o StrictHostKeyChecking=no -i $keyPath ubuntu@43.165.166.57 @"
set -e
cd /home/ubuntu/furunsystemv4/current
python3 - <<'PY'
from pathlib import Path

env_path = Path('.env.worker')
content = env_path.read_text(encoding='utf-8')
updates = {
    'SPOT_SYMBOLS=': 'SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT',
    'ORDERBOOK_DEPTH_LIMIT=': 'ORDERBOOK_DEPTH_LIMIT=5',
    'TARGET_QUOTE_AMOUNT=': 'TARGET_QUOTE_AMOUNT=100',
    'SCANNER_POLL_INTERVAL_SECONDS=': 'SCANNER_POLL_INTERVAL_SECONDS=2',
}
lines = []
seen = set()
for line in content.splitlines():
    replaced = False
    for prefix, replacement in updates.items():
        if line.startswith(prefix):
            lines.append(replacement)
            seen.add(prefix)
            replaced = True
            break
    if not replaced:
        lines.append(line)
for prefix, replacement in updates.items():
    if prefix not in seen:
        lines.append(replacement)
env_path.write_text("\n".join(lines) + "\n", encoding='utf-8')
PY
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-consumer.service
sleep 8
printf 'scanner='
systemctl is-active furun-spot-scanner.service
printf 'consumer='
systemctl is-active furun-spot-consumer.service
printf 'zcard='
redis-cli ZCARD arb:zset:spot
printf 'xlen='
redis-cli XLEN stream:spot_opps
echo 'latest_stream'
redis-cli XREVRANGE stream:spot_opps + - COUNT 3
"@
```

Expected:
- both workers stay `active`
- Redis counters keep increasing
- recent stream payloads include the new depth fields
- at least two symbols from the whitelist appear in the latest Redis entries or scanner logs

- [ ] **Step 6: Commit**

```bash
git add deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md
git commit -m "docs: add depth multi-symbol scanner defaults"
```

## Coverage Check

- Multi-symbol scanner settings and fallback behavior: Task 1
- Weighted orderbook depth pricing and extended `SpotOpportunity`: Task 2
- Redis payload upgrades and multi-symbol scanner loop: Task 3
- Env defaults, docs, and remote whitelist validation: Task 4

## Self-Review

- Spec coverage: the plan covers whitelist symbols, depth snapshots, effective prices, extended Redis payloads, scanner loop changes, config additions, and remote validation.
- Placeholder scan: all steps include concrete code, file paths, commands, env values, and expected outcomes.
- Type consistency: `WorkerSettings.active_spot_symbols`, `OrderbookSnapshot`, `SpotOpportunity`, `build_depth_spot_opportunity()`, and the new Redis payload fields use consistent names across tasks.
