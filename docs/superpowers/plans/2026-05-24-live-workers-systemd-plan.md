# Live Workers Systemd Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-ready runtime wrapper that runs the spot scanner and Redis consumer as separate long-lived `systemd` services on the remote Ubuntu host.

**Architecture:** Keep existing market and Redis business logic intact, and add a small runtime shell around it. The implementation introduces shared worker configuration loaders, a dedicated `app.runtime.worker_service` entrypoint, and checked-in `systemd` assets plus deployment docs so the existing verified short-run flow can become a restartable background service.

**Tech Stack:** Python 3.10+, asyncio, pydantic-settings, redis.asyncio, ccxt/ccxt.async_support, pytest, pytest-asyncio, systemd

---

## Planned File Structure

**Create**
- `app/runtime/worker_config.py`
- `app/runtime/worker_service.py`
- `app/runtime/systemd_assets.py`
- `tests/test_worker_config.py`
- `tests/test_worker_service.py`
- `tests/test_systemd_assets.py`
- `deploy/systemd/furun-spot-scanner.service`
- `deploy/systemd/furun-spot-consumer.service`
- `deploy/systemd/.env.worker.example`
- `docs/ops/live-workers-systemd.md`

**Modify**
- `app/runtime/sandbox_probe.py`

## Task 1: Add Shared Worker Settings, Credential Loaders, And Proxy Loaders

**Files:**
- Create: `app/runtime/worker_config.py`
- Modify: `app/runtime/sandbox_probe.py`
- Test: `tests/test_worker_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_config.py
from app.runtime.worker_config import (
    WorkerSettings,
    load_exchange_credential_from_env,
    load_exchange_credentials_from_env,
    load_exchange_proxies_from_env,
)


def test_worker_settings_parse_csv_exchange_list():
    settings = WorkerSettings(
        redis_url="redis://127.0.0.1:6379/0",
        spot_exchanges="okx, bitget ,gate",
        worker_role="scanner",
    )

    assert settings.spot_exchanges == ["okx", "bitget", "gate"]
    assert settings.spot_symbol == "BTC/USDT"
    assert settings.scanner_poll_interval_seconds == 1.0
    assert settings.consumer_block_ms == 1000


def test_load_exchange_credentials_and_proxies_from_env(monkeypatch):
    monkeypatch.setenv("OKX_API_KEY", "okx-key")
    monkeypatch.setenv("OKX_SECRET", "okx-secret")
    monkeypatch.setenv("OKX_PASSWORD", "okx-pass")
    monkeypatch.setenv("OKX_PROXY_TYPE", "http")
    monkeypatch.setenv("OKX_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("OKX_PROXY_PORT", "8080")
    monkeypatch.setenv("OKX_PROXY_USERNAME", "alice")
    monkeypatch.setenv("OKX_PROXY_PASSWORD", "secret")

    single = load_exchange_credential_from_env("okx")
    credentials = load_exchange_credentials_from_env(["okx", "gate"])
    proxies = load_exchange_proxies_from_env(["okx", "gate"])

    assert single is not None
    assert single.api_key == "okx-key"
    assert credentials["okx"].secret == "okx-secret"
    assert "gate" not in credentials
    assert proxies["okx"]["http"] == "http://alice:secret@127.0.0.1:8080"
    assert "gate" not in proxies
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_worker_config.py -q
```

Expected: FAIL with `ModuleNotFoundError` because `app.runtime.worker_config` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# app/runtime/worker_config.py
import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    spot_exchanges: list[str] = Field(default_factory=lambda: ["okx", "bitget", "gate"])
    scanner_poll_interval_seconds: float = 1.0
    consumer_block_ms: int = 1000
    worker_role: Literal["scanner", "consumer"] = "scanner"
    worker_region: str = "default"

    @field_validator("spot_exchanges", mode="before")
    @classmethod
    def split_exchanges(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()


def load_exchange_credential_from_env(exchange: str) -> ExchangeCredentials | None:
    prefix = exchange.upper().replace(".", "_")
    api_key = os.getenv(f"{prefix}_API_KEY")
    secret = os.getenv(f"{prefix}_SECRET")
    password = os.getenv(f"{prefix}_PASSWORD")
    if not api_key or not secret:
        return None
    return ExchangeCredentials(api_key=api_key, secret=secret, password=password)


def load_exchange_credentials_from_env(exchanges: list[str]) -> dict[str, ExchangeCredentials]:
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

```python
# app/runtime/sandbox_probe.py
import asyncio
from dataclasses import dataclass, field

from app.exchanges.adapters import ExchangeAdapter
from app.exchanges.session_manager import (
    ExchangeClientFactory,
    ExchangeCredentials,
)
from app.runtime.worker_config import load_exchange_credential_from_env


@dataclass(slots=True)
class SandboxProbeResult:
    exchange: str
    ok: bool
    message: str
    non_zero_assets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OrderLifecycleProbeResult:
    exchange: str
    ok: bool
    message: str
    symbol: str
    order_id: str | None = None
    created_status: str | None = None
    fetched_status: str | None = None
    cancel_status: str | None = None
    final_status: str | None = None


class SandboxProbeService:
    def __init__(self, session_factory: ExchangeClientFactory | None = None) -> None:
        self.session_factory = session_factory or ExchangeClientFactory()

    async def probe_exchange(
        self,
        *,
        exchange: str,
        credentials: ExchangeCredentials,
        env_mode: str = "testnet",
        proxies: dict[str, str] | None = None,
    ) -> SandboxProbeResult:
        session = self.session_factory.create_session(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies or {},
            credentials=credentials,
        )
        adapter = ExchangeAdapter(session)
        try:
            await session.mark_ready()
            balance = await adapter.fetch_balance()
            assets = [
                asset
                for asset, value in balance.get("total", {}).items()
                if isinstance(value, (int, float)) and value > 0
            ]
            return SandboxProbeResult(
                exchange=exchange,
                ok=True,
                message="connected",
                non_zero_assets=assets,
            )
        except Exception as exc:
            return SandboxProbeResult(
                exchange=exchange,
                ok=False,
                message=str(exc),
                non_zero_assets=[],
            )
        finally:
            await adapter.close()

    async def probe_order_lifecycle(
        self,
        *,
        exchange: str,
        credentials: ExchangeCredentials,
        symbol: str,
        env_mode: str = "testnet",
        proxies: dict[str, str] | None = None,
    ) -> OrderLifecycleProbeResult:
        session = self.session_factory.create_session(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies or {},
            credentials=credentials,
        )
        adapter = ExchangeAdapter(session)
        try:
            await session.mark_ready()
            market = session.markets[symbol]
            ticker = await adapter.fetch_ticker(symbol)
            amount = self._build_safe_amount(market=market, ticker=ticker)
            price = self._build_safe_price(ticker=ticker)
            order = await adapter.create_order(
                request=self._build_limit_buy_request(
                    symbol=symbol,
                    amount=adapter.amount_to_precision(symbol, amount),
                    price=adapter.price_to_precision(symbol, price),
                    exchange=exchange,
                )
            )
            fetched = await adapter.fetch_order(order["id"], symbol)
            canceled = await adapter.cancel_order(order["id"], symbol)
            final_state = await adapter.fetch_order(order["id"], symbol)
            return OrderLifecycleProbeResult(
                exchange=exchange,
                ok=True,
                message="order_lifecycle_ok",
                symbol=symbol,
                order_id=order.get("id"),
                created_status=order.get("status"),
                fetched_status=fetched.get("status"),
                cancel_status=canceled.get("status"),
                final_status=final_state.get("status"),
            )
        except Exception as exc:
            return OrderLifecycleProbeResult(
                exchange=exchange,
                ok=False,
                message=str(exc),
                symbol=symbol,
            )
        finally:
            await adapter.close()

    @staticmethod
    def _build_safe_amount(*, market: dict, ticker: dict) -> float:
        min_amount = (
            market.get("limits", {})
            .get("amount", {})
            .get("min")
            or 0.0001
        )
        reference_price = ticker.get("bid") or ticker.get("last") or ticker.get("ask") or 1.0
        quote_budget = 15.0
        return max(float(min_amount), quote_budget / float(reference_price))

    @staticmethod
    def _build_safe_price(*, ticker: dict) -> float:
        bid = ticker.get("bid") or ticker.get("last") or ticker.get("ask")
        if bid is None:
            raise RuntimeError("missing bid price")
        return float(bid) * 0.95

    @staticmethod
    def _build_limit_buy_request(*, symbol: str, amount: float, price: float, exchange: str):
        from app.exchanges.adapters import OrderRequest

        request = OrderRequest(
            symbol=symbol,
            side="buy",
            order_type="limit",
            amount=amount,
            price=price,
        )
        if exchange in {"okx", "gate", "gateio"}:
            request.post_only = True
        return request


async def _run_probe() -> None:
    service = SandboxProbeService()
    exchanges = [
        item.strip()
        for item in os.getenv(
            "SANDBOX_PROBE_EXCHANGES",
            "binance,okx,bybit,bitget,gate",
        ).split(",")
        if item.strip()
    ]
    order_mode = os.getenv("SANDBOX_ORDER_PROBE", "0") == "1"
    default_symbol = os.getenv("SANDBOX_ORDER_SYMBOL", "BTC/USDT")
    for exchange in exchanges:
        credentials = load_exchange_credential_from_env(exchange)
        if credentials is None:
            print(f"{exchange}: skipped (missing credentials)")
            continue
        if order_mode:
            result = await service.probe_order_lifecycle(
                exchange=exchange,
                credentials=credentials,
                symbol=default_symbol,
            )
            print(
                f"{result.exchange}: {'ok' if result.ok else 'error'} | "
                f"{result.message} | symbol={result.symbol} | order_id={result.order_id or '-'} | "
                f"created={result.created_status or '-'} | fetched={result.fetched_status or '-'} | "
                f"canceled={result.cancel_status or '-'} | final={result.final_status or '-'}"
            )
        else:
            result = await service.probe_exchange(exchange=exchange, credentials=credentials)
            print(
                f"{result.exchange}: {'ok' if result.ok else 'error'} | "
                f"{result.message} | assets={','.join(result.non_zero_assets) or '-'}"
            )


def main() -> None:
    asyncio.run(_run_probe())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_worker_config.py tests/test_sandbox_probe.py -q
```

Expected: PASS with `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/worker_config.py app/runtime/sandbox_probe.py tests/test_worker_config.py
git commit -m "feat: add shared worker configuration loaders"
```

## Task 2: Add Dedicated Worker Runtime Entrypoint For Scanner And Consumer

**Files:**
- Create: `app/runtime/worker_service.py`
- Test: `tests/test_worker_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_service.py
import pytest

from app.runtime.worker_config import WorkerSettings
from app.runtime.worker_service import WorkerApp


class FakeRedis:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeWorker:
    def __init__(self):
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return 1


class FakeFactory:
    def __init__(self):
        self.scanner_worker = FakeWorker()
        self.consumer_worker = FakeWorker()

    def build_scanner_worker(self, **kwargs):
        return self.scanner_worker

    def build_consumer_worker(self, **kwargs):
        return self.consumer_worker


@pytest.mark.asyncio
async def test_worker_app_dispatches_scanner_role_and_closes_redis():
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(worker_role="scanner", spot_exchanges=["okx", "bitget"], spot_symbol="BTC/USDT"),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert len(factory.scanner_worker.calls) == 1
    assert factory.scanner_worker.calls[0]["exchanges"] == ["okx", "bitget"]
    assert redis_client.closed is True


@pytest.mark.asyncio
async def test_worker_app_dispatches_consumer_role_and_closes_redis():
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(worker_role="consumer", spot_exchanges=["okx", "bitget"], spot_symbol="BTC/USDT"),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert len(factory.consumer_worker.calls) == 1
    assert factory.consumer_worker.calls[0]["stream_key"] == "stream:spot_opps"
    assert redis_client.closed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_worker_service.py -q
```

Expected: FAIL with `ModuleNotFoundError` because `app.runtime.worker_service` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# app/runtime/worker_service.py
import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from redis.asyncio import Redis

from app.exchanges.session_manager import ExchangeClientFactory
from app.runtime.live_spot_flow import LiveSpotFlowService
from app.runtime.live_workers import ContinuousSpotScanner, RedisSpotConsumer
from app.runtime.redis_flow import RedisOpportunityDispatcher
from app.runtime.spot_arbitrage_probe import SpotArbitrageProbeService
from app.runtime.worker_config import (
    WorkerSettings,
    get_worker_settings,
    load_exchange_credentials_from_env,
    load_exchange_proxies_from_env,
)


class ScannerWorker:
    def __init__(self, scanner: ContinuousSpotScanner, settings: WorkerSettings) -> None:
        self.scanner = scanner
        self.settings = settings

    async def run(self, *, exchanges: list[str], credentials_by_exchange: dict, proxies_by_exchange: dict) -> None:
        await self.scanner.run(
            exchanges=exchanges,
            credentials_by_exchange=credentials_by_exchange,
            symbol=self.settings.spot_symbol,
            env_mode=self.settings.env_mode,
            proxies_by_exchange=proxies_by_exchange,
            max_iterations=None,
        )


class ConsumerWorker:
    def __init__(self, consumer: RedisSpotConsumer) -> None:
        self.consumer = consumer

    async def run(self, *, credentials_by_exchange: dict, stream_key: str) -> int:
        return await self.consumer.run(
            credentials_by_exchange=credentials_by_exchange,
            max_iterations=None,
        )


@dataclass(slots=True)
class DefaultWorkerFactory:
    settings: WorkerSettings
    session_factory: ExchangeClientFactory = field(default_factory=ExchangeClientFactory)
    spot_service: SpotArbitrageProbeService = field(default_factory=SpotArbitrageProbeService)

    def build_scanner_worker(self, *, redis_client: Redis) -> ScannerWorker:
        flow = LiveSpotFlowService(
            redis_client=redis_client,
            session_factory=self.session_factory,
            spot_service=self.spot_service,
        )
        scanner = ContinuousSpotScanner(
            flow_service=flow,
            poll_interval_seconds=self.settings.scanner_poll_interval_seconds,
        )
        return ScannerWorker(scanner=scanner, settings=self.settings)

    def build_consumer_worker(self, *, redis_client: Redis) -> ConsumerWorker:
        dispatcher = RedisOpportunityDispatcher(self.spot_service)
        consumer = RedisSpotConsumer(
            redis_client=redis_client,
            dispatcher=dispatcher,
            stream_key="stream:spot_opps",
            block_ms=self.settings.consumer_block_ms,
        )
        return ConsumerWorker(consumer=consumer)


@dataclass(slots=True)
class WorkerApp:
    settings: WorkerSettings
    redis_factory: Callable[[str], Any] = staticmethod(lambda url: Redis.from_url(url, decode_responses=True))
    worker_factory: Any | None = None

    async def run(self) -> None:
        exchanges = self.settings.spot_exchanges
        credentials_by_exchange = load_exchange_credentials_from_env(exchanges)
        proxies_by_exchange = load_exchange_proxies_from_env(exchanges)
        if set(exchanges) - set(credentials_by_exchange):
            missing = sorted(set(exchanges) - set(credentials_by_exchange))
            raise RuntimeError(f"missing credentials for exchanges: {','.join(missing)}")

        redis_client = self.redis_factory(self.settings.redis_url)
        factory = self.worker_factory or DefaultWorkerFactory(settings=self.settings)
        try:
            if self.settings.worker_role == "scanner":
                worker = factory.build_scanner_worker(redis_client=redis_client)
                await worker.run(
                    exchanges=exchanges,
                    credentials_by_exchange=credentials_by_exchange,
                    proxies_by_exchange=proxies_by_exchange,
                )
            else:
                worker = factory.build_consumer_worker(redis_client=redis_client)
                await worker.run(
                    credentials_by_exchange=credentials_by_exchange,
                    stream_key="stream:spot_opps",
                )
        finally:
            await redis_client.aclose()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["scanner", "consumer"], default=None)
    return parser.parse_args(argv)


async def _run(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_worker_settings()
    if args.role is not None:
        settings = settings.model_copy(update={"worker_role": args.role})
    app = WorkerApp(settings=settings)
    await app.run()


def main(argv: list[str] | None = None) -> None:
    asyncio.run(_run(argv))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_worker_service.py -q
```

Expected: PASS with `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/worker_service.py tests/test_worker_service.py
git commit -m "feat: add live worker runtime entrypoint"
```

## Task 3: Add Rendered Systemd Assets And Worker Deployment Documentation

**Files:**
- Create: `app/runtime/systemd_assets.py`
- Create: `tests/test_systemd_assets.py`
- Create: `deploy/systemd/furun-spot-scanner.service`
- Create: `deploy/systemd/furun-spot-consumer.service`
- Create: `deploy/systemd/.env.worker.example`
- Create: `docs/ops/live-workers-systemd.md`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_systemd_assets.py
from app.runtime.systemd_assets import render_systemd_unit, render_worker_env_example


def test_render_systemd_unit_contains_expected_execstart_for_scanner():
    content = render_systemd_unit(role="scanner")

    assert "Description=FuRun spot scanner worker" in content
    assert "EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker" in content
    assert "ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.worker_service --role scanner" in content
    assert "Restart=always" in content


def test_render_worker_env_example_contains_core_runtime_keys():
    content = render_worker_env_example()

    assert "REDIS_URL=redis://127.0.0.1:6379/0" in content
    assert "SPOT_SYMBOL=BTC/USDT" in content
    assert "SPOT_EXCHANGES=okx,bitget,gate" in content
    assert "OKX_API_KEY=" in content
    assert "OKX_PROXY_HOST=" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_systemd_assets.py -q
```

Expected: FAIL with `ModuleNotFoundError` because `app.runtime.systemd_assets` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# app/runtime/systemd_assets.py
def render_systemd_unit(*, role: str) -> str:
    description = f"FuRun spot {role} worker"
    return f"""[Unit]
Description={description}
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.worker_service --role {role}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


def render_worker_env_example() -> str:
    return """REDIS_URL=redis://127.0.0.1:6379/0
ENV_MODE=testnet
SPOT_SYMBOL=BTC/USDT
SPOT_EXCHANGES=okx,bitget,gate
SCANNER_POLL_INTERVAL_SECONDS=1.0
CONSUMER_BLOCK_MS=1000
WORKER_REGION=default
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
"""
```

```ini
# deploy/systemd/furun-spot-scanner.service
[Unit]
Description=FuRun spot scanner worker
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.worker_service --role scanner
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```ini
# deploy/systemd/furun-spot-consumer.service
[Unit]
Description=FuRun spot consumer worker
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.worker_service --role consumer
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```dotenv
# deploy/systemd/.env.worker.example
REDIS_URL=redis://127.0.0.1:6379/0
ENV_MODE=testnet
SPOT_SYMBOL=BTC/USDT
SPOT_EXCHANGES=okx,bitget,gate
SCANNER_POLL_INTERVAL_SECONDS=1.0
CONSUMER_BLOCK_MS=1000
WORKER_REGION=default
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
# Live Workers Systemd Deployment

## Files

- `deploy/systemd/furun-spot-scanner.service`
- `deploy/systemd/furun-spot-consumer.service`
- `deploy/systemd/.env.worker.example`

## Remote Setup

1. Copy the example env file and fill in real credentials:

```bash
cd /home/ubuntu/furunsystemv4/current
cp deploy/systemd/.env.worker.example .env.worker
nano .env.worker
```

2. Install the unit files:

```bash
sudo cp deploy/systemd/furun-spot-scanner.service /etc/systemd/system/
sudo cp deploy/systemd/furun-spot-consumer.service /etc/systemd/system/
sudo systemctl daemon-reload
```

3. Enable and start both services:

```bash
sudo systemctl enable furun-spot-scanner.service
sudo systemctl enable furun-spot-consumer.service
sudo systemctl start furun-spot-scanner.service
sudo systemctl start furun-spot-consumer.service
```

4. Check status and recent logs:

```bash
sudo systemctl status furun-spot-scanner.service --no-pager
sudo systemctl status furun-spot-consumer.service --no-pager
sudo journalctl -u furun-spot-scanner.service -n 50 --no-pager
sudo journalctl -u furun-spot-consumer.service -n 50 --no-pager
```

5. Restart one side independently when needed:

```bash
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-consumer.service
```
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_systemd_assets.py -q
```

Expected: PASS with `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/systemd_assets.py tests/test_systemd_assets.py deploy/systemd/furun-spot-scanner.service deploy/systemd/furun-spot-consumer.service deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md
git commit -m "feat: add systemd deployment assets for live workers"
```

## Task 4: Run Full Local Regression And Complete Remote Systemd Verification

**Files:**
- Modify: `docs/ops/live-workers-systemd.md`

- [ ] **Step 1: Run the local test suite**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests -q
```

Expected: PASS with the existing suite plus the new worker and systemd asset tests.

- [ ] **Step 2: Sync the changed files to the remote host**

Run:

```powershell
$keyDir = Join-Path $env:TEMP "ssh-work"
New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
$keyPath = Join-Path $keyDir "futunsystemv3_deploy_ed25519"
Copy-Item -Force "d:\old\FuRunSystemV4\.keys\futunsystemv3_deploy_ed25519" $keyPath
& "$env:SystemRoot\System32\icacls.exe" $keyPath /inheritance:r /grant:r "$($env:USERNAME):(R)"
& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\runtime\worker_config.py" `
  "d:\old\FuRunSystemV4\app\runtime\worker_service.py" `
  "d:\old\FuRunSystemV4\app\runtime\systemd_assets.py" `
  "d:\old\FuRunSystemV4\app\runtime\sandbox_probe.py" `
  "d:\old\FuRunSystemV4\deploy\systemd\furun-spot-scanner.service" `
  "d:\old\FuRunSystemV4\deploy\systemd\furun-spot-consumer.service" `
  "d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example" `
  "d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/
```

Expected: upload finishes without `Permission denied` or `No such file`.

- [ ] **Step 3: Install env file and unit files on the remote host**

Run:

```powershell
$keyPath = Join-Path (Join-Path $env:TEMP "ssh-work") "futunsystemv3_deploy_ed25519"
& "C:\Windows\System32\OpenSSH\ssh.exe" -o StrictHostKeyChecking=no -i $keyPath ubuntu@43.165.166.57 @"
set -e
cd /home/ubuntu/furunsystemv4/current
mkdir -p deploy/systemd
cp furun-spot-scanner.service deploy/systemd/furun-spot-scanner.service
cp furun-spot-consumer.service deploy/systemd/furun-spot-consumer.service
cp .env.worker.example .env.worker
sudo cp deploy/systemd/furun-spot-scanner.service /etc/systemd/system/
sudo cp deploy/systemd/furun-spot-consumer.service /etc/systemd/system/
sudo systemctl daemon-reload
"@
```

Expected: `systemctl daemon-reload` exits successfully.

- [ ] **Step 4: Fill real credentials into `.env.worker` and start both services**

Run:

```powershell
$keyPath = Join-Path (Join-Path $env:TEMP "ssh-work") "futunsystemv3_deploy_ed25519"
& "C:\Windows\System32\OpenSSH\ssh.exe" -o StrictHostKeyChecking=no -i $keyPath ubuntu@43.165.166.57 @"
set -e
cd /home/ubuntu/furunsystemv4/current
python3 - <<'PY'
from pathlib import Path

env_path = Path(".env.worker")
content = env_path.read_text(encoding="utf-8")
replacements = {
    "OKX_API_KEY=": "OKX_API_KEY=<fill-real-value>",
    "OKX_SECRET=": "OKX_SECRET=<fill-real-value>",
    "OKX_PASSWORD=": "OKX_PASSWORD=<fill-real-value>",
    "BITGET_API_KEY=": "BITGET_API_KEY=<fill-real-value>",
    "BITGET_SECRET=": "BITGET_SECRET=<fill-real-value>",
    "BITGET_PASSWORD=": "BITGET_PASSWORD=<fill-real-value>",
    "GATE_API_KEY=": "GATE_API_KEY=<fill-real-value>",
    "GATE_SECRET=": "GATE_SECRET=<fill-real-value>",
}
for before, after in replacements.items():
    content = content.replace(before, after)
env_path.write_text(content, encoding="utf-8")
PY
sudo systemctl enable furun-spot-scanner.service furun-spot-consumer.service
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-consumer.service
sudo systemctl status furun-spot-scanner.service --no-pager
sudo systemctl status furun-spot-consumer.service --no-pager
"@
```

Expected: both units show `active (running)`.

- [ ] **Step 5: Verify logs and Redis activity, then capture the commands in the ops doc**

Run:

```powershell
$keyPath = Join-Path (Join-Path $env:TEMP "ssh-work") "futunsystemv3_deploy_ed25519"
& "C:\Windows\System32\OpenSSH\ssh.exe" -o StrictHostKeyChecking=no -i $keyPath ubuntu@43.165.166.57 @"
set -e
sudo journalctl -u furun-spot-scanner.service -n 30 --no-pager
sudo journalctl -u furun-spot-consumer.service -n 30 --no-pager
redis-cli ZCARD arb:zset:spot
redis-cli XLEN stream:spot_opps
"@
```

Expected: scanner logs show iterations, consumer logs show message handling, and both Redis counts are non-zero.

- [ ] **Step 6: Commit**

```bash
git add docs/ops/live-workers-systemd.md
git commit -m "docs: record live worker deployment verification"
```

## Coverage Check

- Shared worker settings and CSV exchange parsing: Task 1
- Shared environment credential and proxy loading: Task 1
- Dedicated scanner and consumer runtime entrypoint: Task 2
- Remote-friendly `systemd` unit generation and checked-in deployment assets: Task 3
- Full local regression plus remote `systemd` verification: Task 4

## Self-Review

- Spec coverage: the plan covers the worker entrypoint, environment file, separate `systemd` units, restart policy, remote verification, and deployment docs.
- Placeholder scan: the only placeholder values are the intentionally marked `<fill-real-value>` secrets in the remote `.env.worker` editing step.
- Type consistency: `WorkerSettings`, `WorkerApp`, and the loader function names are consistent across all tasks.
