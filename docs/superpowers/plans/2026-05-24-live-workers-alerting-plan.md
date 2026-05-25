# Live Workers Alerting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured runtime events, Feishu alerts, QQ email alerts, and success-notification routing to the existing live scanner and Redis consumer workers.

**Architecture:** Keep the current `systemd` worker runtime intact and add a thin alerting layer around it. The implementation introduces a shared runtime event model, JSON logger, alert router, notifier adapters, and explicit event hooks in `worker_service.py` and `live_workers.py`, then validates the real channels on the remote host.

**Tech Stack:** Python 3.10+, asyncio, json, smtplib, email.message, urllib.request, pydantic-settings, pytest, pytest-asyncio, systemd

---

## Planned File Structure

**Create**
- `app/runtime/runtime_events.py`
- `app/runtime/alerting.py`
- `tests/test_runtime_events.py`
- `tests/test_alerting.py`
- `tests/test_live_worker_alerts.py`

**Modify**
- `app/runtime/worker_config.py`
- `app/runtime/live_workers.py`
- `app/runtime/worker_service.py`
- `deploy/systemd/.env.worker.example`
- `docs/ops/live-workers-systemd.md`

## Task 1: Add Runtime Event Model, JSON Logger, And Alert Router Rules

**Files:**
- Create: `app/runtime/runtime_events.py`
- Create: `tests/test_runtime_events.py`
- Create: `tests/test_alerting.py`
- Create: `app/runtime/alerting.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runtime_events.py
import json

from app.runtime.runtime_events import RuntimeEvent


def test_runtime_event_to_json_contains_core_fields():
    event = RuntimeEvent(
        event_type="scanner.iteration.failed",
        level="ERROR",
        service="scanner",
        message="scanner iteration failed",
        symbol="BTC/USDT",
        exchange="okx",
        payload={"error": "timeout"},
    )

    data = json.loads(event.to_json())

    assert data["event_type"] == "scanner.iteration.failed"
    assert data["level"] == "ERROR"
    assert data["service"] == "scanner"
    assert data["symbol"] == "BTC/USDT"
    assert data["exchange"] == "okx"
    assert data["payload"]["error"] == "timeout"
    assert data["created_at"].endswith("+00:00")
```

```python
# tests/test_alerting.py
from app.runtime.alerting import AlertRouter, StructuredEventLogger
from app.runtime.runtime_events import RuntimeEvent


class FakeLogger:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


class FakeNotifier:
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)
        return {"status": "ok"}


async def noop_sleep(_: float) -> None:
    return None


def build_router():
    return AlertRouter(
        logger=FakeLogger(),
        feishu_notifier=FakeNotifier(),
        email_notifier=FakeNotifier(),
        alerts_enabled=True,
        feishu_enabled=True,
        email_enabled=True,
        success_spread_bps_threshold=50.0,
        dedupe_window_seconds=60,
        time_provider=lambda: 100.0,
    )


async def test_alert_router_routes_info_opportunity_to_feishu_only_when_spread_is_high():
    router = build_router()
    event = RuntimeEvent(
        event_type="opportunity.detected",
        level="INFO",
        service="scanner",
        message="opportunity detected",
        symbol="BTC/USDT",
        payload={"spread_bps": 80.0},
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert len(router.feishu_notifier.events) == 1
    assert len(router.email_notifier.events) == 0


async def test_alert_router_dedupes_error_events_in_the_same_window():
    router = build_router()
    event = RuntimeEvent(
        event_type="scanner.iteration.failed",
        level="ERROR",
        service="scanner",
        message="scanner iteration failed",
        symbol="BTC/USDT",
        exchange="okx",
        payload={"error": "timeout"},
    )

    await router.dispatch(event)
    await router.dispatch(event)

    assert len(router.logger.events) == 2
    assert len(router.feishu_notifier.events) == 1
    assert len(router.email_notifier.events) == 0


async def test_alert_router_routes_critical_events_to_feishu_and_email():
    router = build_router()
    event = RuntimeEvent(
        event_type="worker.start_failed",
        level="CRITICAL",
        service="scanner",
        message="worker start failed",
        payload={"error": "missing credentials"},
    )

    await router.dispatch(event)

    assert len(router.feishu_notifier.events) == 1
    assert len(router.email_notifier.events) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_runtime_events.py tests/test_alerting.py -q
```

Expected: FAIL with `ModuleNotFoundError` because `app.runtime.runtime_events` and `app.runtime.alerting` do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
# app/runtime/runtime_events.py
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class RuntimeEvent:
    event_type: str
    level: str
    service: str
    message: str
    region: str | None = None
    symbol: str | None = None
    exchange: str | None = None
    exchanges: list[str] | None = None
    payload: dict = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "level": self.level,
            "service": self.service,
            "region": self.region,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "exchanges": self.exchanges,
            "message": self.message,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, separators=(",", ":"))
```

```python
# app/runtime/alerting.py
from dataclasses import dataclass, field
from typing import Any, Callable

from app.runtime.runtime_events import RuntimeEvent


class StructuredEventLogger:
    def __init__(self, sink: Callable[[str], None] = print) -> None:
        self.sink = sink

    def record(self, event: RuntimeEvent) -> None:
        self.sink(event.to_json())


@dataclass(slots=True)
class AlertRouter:
    logger: Any
    feishu_notifier: Any | None = None
    email_notifier: Any | None = None
    alerts_enabled: bool = True
    feishu_enabled: bool = True
    email_enabled: bool = True
    success_spread_bps_threshold: float = 0.0
    dedupe_window_seconds: int = 60
    time_provider: Callable[[], float] = field(default_factory=lambda: __import__("time").time)
    _dedupe_cache: dict[str, float] = field(default_factory=dict)

    async def dispatch(self, event: RuntimeEvent) -> None:
        self.logger.record(event)
        if not self.alerts_enabled:
            return
        if event.level == "CRITICAL":
            await self._send_feishu(event)
            await self._send_email(event)
            return
        if event.level == "ERROR":
            if self._should_dedupe(event):
                return
            await self._send_feishu(event)
            return
        if event.level == "INFO" and event.event_type in {
            "opportunity.detected",
            "consumer.message.processed",
        }:
            spread_bps = float(event.payload.get("spread_bps", 0.0))
            if spread_bps >= self.success_spread_bps_threshold:
                await self._send_feishu(event)

    def _should_dedupe(self, event: RuntimeEvent) -> bool:
        key = f"{event.event_type}:{event.symbol or '-'}:{event.exchange or '-'}"
        now = self.time_provider()
        last_sent = self._dedupe_cache.get(key)
        if last_sent is not None and now - last_sent < self.dedupe_window_seconds:
            return True
        self._dedupe_cache[key] = now
        return False

    async def _send_feishu(self, event: RuntimeEvent) -> None:
        if self.feishu_enabled and self.feishu_notifier is not None:
            await self.feishu_notifier.send(event)

    async def _send_email(self, event: RuntimeEvent) -> None:
        if self.email_enabled and self.email_notifier is not None:
            await self.email_notifier.send(event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_runtime_events.py tests/test_alerting.py -q
```

Expected: PASS with `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/runtime_events.py app/runtime/alerting.py tests/test_runtime_events.py tests/test_alerting.py
git commit -m "feat: add runtime event model and alert router"
```

## Task 2: Add Feishu And QQ Email Notifier Adapters Plus Alert Settings

**Files:**
- Modify: `app/runtime/worker_config.py`
- Modify: `app/runtime/alerting.py`
- Modify: `tests/test_alerting.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_alerting.py
import json

from app.runtime.alerting import EmailNotifier, FeishuNotifier
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.worker_config import AlertSettings


class FakeHttpResponse:
    def __init__(self, payload: bytes = b'{"StatusCode":0}'):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeSMTP:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.logged_in = None
        self.messages = []

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.messages.append(message)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_alert_settings_parse_email_and_thresholds():
    settings = AlertSettings(
        alerts_enabled=True,
        alert_feishu_enabled=True,
        alert_feishu_webhook="https://example.test/hook",
        alert_email_enabled=True,
        alert_email_smtp_host="smtp.qq.com",
        alert_email_smtp_port=465,
        alert_email_username="bot@qq.com",
        alert_email_password="secret",
        alert_email_to="alice@qq.com,bob@qq.com",
        alert_success_spread_bps_threshold=88.5,
        alert_dedupe_window_seconds=120,
    )

    assert settings.alert_email_to == ["alice@qq.com", "bob@qq.com"]
    assert settings.alert_success_spread_bps_threshold == 88.5
    assert settings.alert_dedupe_window_seconds == 120


def test_feishu_notifier_posts_text_message():
    captured = {}

    def fake_urlopen(request, timeout=5):
        captured["url"] = request.full_url
        captured["body"] = request.data
        return FakeHttpResponse()

    notifier = FeishuNotifier(
        webhook_url="https://example.test/hook",
        urlopen=fake_urlopen,
    )
    event = RuntimeEvent(
        event_type="worker.start_failed",
        level="CRITICAL",
        service="scanner",
        message="worker start failed",
        payload={"error": "missing credentials"},
    )

    result = notifier.send_sync(event)
    body = json.loads(captured["body"].decode("utf-8"))

    assert captured["url"] == "https://example.test/hook"
    assert body["msg_type"] == "text"
    assert "worker start failed" in body["content"]["text"]
    assert result["status"] == "ok"


def test_email_notifier_builds_and_sends_message():
    smtp_instances = []

    def fake_smtp(host, port):
        instance = FakeSMTP(host, port)
        smtp_instances.append(instance)
        return instance

    notifier = EmailNotifier(
        smtp_host="smtp.qq.com",
        smtp_port=465,
        username="bot@qq.com",
        password="secret",
        recipients=["alice@qq.com", "bob@qq.com"],
        smtp_factory=fake_smtp,
    )
    event = RuntimeEvent(
        event_type="worker.start_failed",
        level="CRITICAL",
        service="scanner",
        message="worker start failed",
        payload={"error": "missing credentials"},
    )

    result = notifier.send_sync(event)
    message = smtp_instances[0].messages[0]

    assert smtp_instances[0].logged_in == ("bot@qq.com", "secret")
    assert message["Subject"].startswith("[CRITICAL] worker.start_failed")
    assert "missing credentials" in message.get_content()
    assert result["status"] == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_alerting.py -q
```

Expected: FAIL with `ImportError` or `AttributeError` because `AlertSettings`, `FeishuNotifier`, and `EmailNotifier` are not implemented yet.

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
    spot_exchanges: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["okx", "bitget", "gate"]
    )
    scanner_poll_interval_seconds: float = 1.0
    consumer_block_ms: int = 1000
    worker_role: Literal["scanner", "consumer"] = "scanner"
    worker_region: str = "default"

    @field_validator("spot_exchanges", mode="before")
    @classmethod
    def split_exchanges(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


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

```python
# app/runtime/alerting.py
import asyncio
import json
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Callable
from urllib.request import Request, urlopen

from app.runtime.runtime_events import RuntimeEvent


class StructuredEventLogger:
    def __init__(self, sink: Callable[[str], None] = print) -> None:
        self.sink = sink

    def record(self, event: RuntimeEvent) -> None:
        self.sink(event.to_json())


class FeishuNotifier:
    def __init__(self, webhook_url: str, urlopen: Callable[..., Any] = urlopen) -> None:
        self.webhook_url = webhook_url
        self.urlopen = urlopen

    def _build_payload(self, event: RuntimeEvent) -> dict:
        return {
            "msg_type": "text",
            "content": {
                "text": f"[{event.level}] {event.event_type}\n{event.message}\n{json.dumps(event.payload, ensure_ascii=True)}"
            },
        }

    def send_sync(self, event: RuntimeEvent) -> dict[str, str]:
        body = json.dumps(self._build_payload(event)).encode("utf-8")
        request = Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with self.urlopen(request, timeout=5) as response:
            response.read()
        return {"status": "ok"}

    async def send(self, event: RuntimeEvent) -> dict[str, str]:
        return await asyncio.to_thread(self.send_sync, event)


class EmailNotifier:
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        recipients: list[str],
        smtp_factory: Callable[..., Any] = smtplib.SMTP_SSL,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.recipients = recipients
        self.smtp_factory = smtp_factory

    def send_sync(self, event: RuntimeEvent) -> dict[str, str]:
        message = EmailMessage()
        message["From"] = self.username
        message["To"] = ", ".join(self.recipients)
        message["Subject"] = f"[{event.level}] {event.event_type}"
        message.set_content(
            f"{event.message}\n\npayload={json.dumps(event.payload, ensure_ascii=True)}"
        )
        with self.smtp_factory(self.smtp_host, self.smtp_port) as smtp:
            smtp.login(self.username, self.password)
            smtp.send_message(message)
        return {"status": "ok"}

    async def send(self, event: RuntimeEvent) -> dict[str, str]:
        return await asyncio.to_thread(self.send_sync, event)


@dataclass(slots=True)
class AlertRouter:
    logger: Any
    feishu_notifier: Any | None = None
    email_notifier: Any | None = None
    alerts_enabled: bool = True
    feishu_enabled: bool = True
    email_enabled: bool = True
    success_spread_bps_threshold: float = 0.0
    dedupe_window_seconds: int = 60
    time_provider: Callable[[], float] = field(default_factory=lambda: __import__("time").time)
    _dedupe_cache: dict[str, float] = field(default_factory=dict)

    async def dispatch(self, event: RuntimeEvent) -> None:
        self.logger.record(event)
        if not self.alerts_enabled:
            return
        if event.level == "CRITICAL":
            await self._send_feishu(event)
            await self._send_email(event)
            return
        if event.level == "ERROR":
            if self._should_dedupe(event):
                return
            await self._send_feishu(event)
            return
        if event.level == "INFO" and event.event_type in {
            "opportunity.detected",
            "consumer.message.processed",
        }:
            spread_bps = float(event.payload.get("spread_bps", 0.0))
            if spread_bps >= self.success_spread_bps_threshold:
                await self._send_feishu(event)

    def _should_dedupe(self, event: RuntimeEvent) -> bool:
        key = f"{event.event_type}:{event.symbol or '-'}:{event.exchange or '-'}"
        now = self.time_provider()
        last_sent = self._dedupe_cache.get(key)
        if last_sent is not None and now - last_sent < self.dedupe_window_seconds:
            return True
        self._dedupe_cache[key] = now
        return False

    async def _send_feishu(self, event: RuntimeEvent) -> None:
        if self.feishu_enabled and self.feishu_notifier is not None:
            await self.feishu_notifier.send(event)

    async def _send_email(self, event: RuntimeEvent) -> None:
        if self.email_enabled and self.email_notifier is not None:
            await self.email_notifier.send(event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_alerting.py tests/test_worker_config.py -q
```

Expected: PASS with `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/worker_config.py app/runtime/alerting.py tests/test_alerting.py
git commit -m "feat: add feishu and email notifier adapters"
```

## Task 3: Wire Runtime Events Into WorkerApp, Scanner, And Consumer

**Files:**
- Modify: `app/runtime/live_workers.py`
- Modify: `app/runtime/worker_service.py`
- Create: `tests/test_live_worker_alerts.py`
- Modify: `tests/test_worker_service.py`
- Modify: `tests/test_live_workers.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_live_worker_alerts.py
import pytest

from app.runtime.runtime_events import RuntimeEvent
from app.runtime.live_workers import ContinuousSpotScanner, RedisSpotConsumer


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
        return {
            "symbol": kwargs["symbol"],
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
            "spread_bps": 88.0,
        }


class FakeDispatcher:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    async def dispatch(self, payload, *, credentials_by_exchange):
        if self.should_fail:
            raise RuntimeError("dispatch failed")
        return {"ok": True}


class FakeRedis:
    async def xread(self, streams, count=1, block=0):
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
                            "spread_bps": "88.0",
                        },
                    )
                ],
            )
        ]


@pytest.mark.asyncio
async def test_scanner_emits_opportunity_detected_event():
    router = FakeEventRouter()
    scanner = ContinuousSpotScanner(
        flow_service=FakeFlowService(),
        poll_interval_seconds=0.0,
        event_router=router,
        region="default",
    )

    await scanner.run(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "bitget": object(), "gate": object()},
        symbol="BTC/USDT",
        max_iterations=1,
    )

    assert [event.event_type for event in router.events] == [
        "opportunity.detected",
        "scanner.iteration.succeeded",
    ]


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
```

```python
# tests/test_worker_service.py
import pytest

from app.runtime.runtime_events import RuntimeEvent
from app.runtime.worker_config import AlertSettings, WorkerSettings
from app.runtime.worker_service import WorkerApp, parse_args


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


class FakeEventRouter:
    def __init__(self):
        self.events = []

    async def dispatch(self, event: RuntimeEvent):
        self.events.append(event)


def seed_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_API_KEY", "okx-key")
    monkeypatch.setenv("OKX_SECRET", "okx-secret")
    monkeypatch.setenv("BITGET_API_KEY", "bitget-key")
    monkeypatch.setenv("BITGET_SECRET", "bitget-secret")


@pytest.mark.asyncio
async def test_worker_app_emits_started_and_stopped_events(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    router = FakeEventRouter()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(worker_role="scanner", spot_exchanges=["okx", "bitget"]),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
        event_router=router,
    )

    await app.run()

    assert [event.event_type for event in router.events] == [
        "worker.started",
        "worker.stopped",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_live_worker_alerts.py tests/test_worker_service.py tests/test_live_workers.py -q
```

Expected: FAIL because `ContinuousSpotScanner`, `RedisSpotConsumer`, and `WorkerApp` do not accept the new alert/event dependencies yet.

- [ ] **Step 3: Write the minimal implementation**

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
        symbol: str,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
        max_iterations: int | None = None,
    ) -> None:
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            try:
                result = await self.flow_service.run_once(
                    exchanges=exchanges,
                    credentials_by_exchange=credentials_by_exchange,
                    symbol=symbol,
                    env_mode=env_mode,
                    proxies_by_exchange=proxies_by_exchange,
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
                                "buy_exchange": result.get("buy_exchange"),
                                "sell_exchange": result.get("sell_exchange"),
                                "spread_bps": result.get("spread_bps", 0.0),
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
                                    payload={"message_id": message_id, "error": str(exc)},
                                )
                            )
            iteration += 1
        return processed
```

```python
# app/runtime/worker_service.py
import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from redis.asyncio import Redis

from app.exchanges.session_manager import ExchangeClientFactory
from app.runtime.alerting import AlertRouter, EmailNotifier, FeishuNotifier, StructuredEventLogger
from app.runtime.live_spot_flow import LiveSpotFlowService
from app.runtime.live_workers import ContinuousSpotScanner, RedisSpotConsumer
from app.runtime.redis_flow import RedisOpportunityDispatcher
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.spot_arbitrage_probe import SpotArbitrageProbeService
from app.runtime.worker_config import (
    AlertSettings,
    WorkerSettings,
    get_alert_settings,
    get_worker_settings,
    load_exchange_credentials_from_env,
    load_exchange_proxies_from_env,
)


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
    event_router: Any
    session_factory: ExchangeClientFactory = field(default_factory=ExchangeClientFactory)
    spot_service: SpotArbitrageProbeService = field(default_factory=SpotArbitrageProbeService)

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
        credentials_by_exchange = load_exchange_credentials_from_env(exchanges)
        proxies_by_exchange = load_exchange_proxies_from_env(exchanges)
        missing = sorted(set(exchanges) - set(credentials_by_exchange))
        router = self.event_router or build_event_router(
            self.alert_settings or get_alert_settings()
        )
        if missing:
            await router.dispatch(
                RuntimeEvent(
                    event_type="worker.start_failed",
                    level="CRITICAL",
                    service=self.settings.worker_role,
                    region=self.settings.worker_region,
                    message="worker start failed",
                    payload={"error": f"missing credentials for exchanges: {','.join(missing)}"},
                )
            )
            raise RuntimeError(
                f"missing credentials for exchanges: {','.join(missing)}"
            )

        redis_client = self.redis_factory(self.settings.redis_url)
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
    parser.add_argument("--role", choices=["scanner", "consumer"], default=None)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_live_worker_alerts.py tests/test_worker_service.py tests/test_live_workers.py -q
```

Expected: PASS with `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/live_workers.py app/runtime/worker_service.py tests/test_live_worker_alerts.py tests/test_worker_service.py tests/test_live_workers.py
git commit -m "feat: wire runtime alerts into live workers"
```

## Task 4: Update Env And Ops Docs, Then Run Remote Feishu And Email Validation

**Files:**
- Modify: `deploy/systemd/.env.worker.example`
- Modify: `docs/ops/live-workers-systemd.md`

- [ ] **Step 1: Write the failing configuration-oriented test**

```python
# tests/test_worker_config.py
from app.runtime.worker_config import AlertSettings


def test_alert_settings_parse_recipient_list_from_env(monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_TO", "alice@qq.com,bob@qq.com")

    settings = AlertSettings()

    assert settings.alert_email_to == ["alice@qq.com", "bob@qq.com"]
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_worker_config.py -q
```

Expected: FAIL until `AlertSettings` has been added in Task 2.

- [ ] **Step 3: Update the env example and deployment doc**

```dotenv
# deploy/systemd/.env.worker.example
REDIS_URL=redis://127.0.0.1:6379/0
ENV_MODE=testnet
SPOT_SYMBOL=BTC/USDT
SPOT_EXCHANGES=okx,bitget,gate
SCANNER_POLL_INTERVAL_SECONDS=1.0
CONSUMER_BLOCK_MS=1000
WORKER_REGION=default
ALERTS_ENABLED=1
ALERT_FEISHU_ENABLED=1
ALERT_FEISHU_WEBHOOK=
ALERT_EMAIL_ENABLED=1
ALERT_EMAIL_SMTP_HOST=smtp.qq.com
ALERT_EMAIL_SMTP_PORT=465
ALERT_EMAIL_USERNAME=
ALERT_EMAIL_PASSWORD=
ALERT_EMAIL_TO=
ALERT_SUCCESS_SPREAD_BPS_THRESHOLD=50
ALERT_DEDUPE_WINDOW_SECONDS=60
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

## Alert Channels

- `ALERT_FEISHU_WEBHOOK` uses the Feishu webhook URL from `local-secrets/飞书webhook地址.txt`
- `ALERT_EMAIL_*` uses the QQ mailbox credentials from `local-secrets/qq邮箱.txt`
- `ALERT_SUCCESS_SPREAD_BPS_THRESHOLD` controls when success notifications are sent to Feishu
- `ALERT_DEDUPE_WINDOW_SECONDS` controls the error dedupe window

## Windows Sync

从 Windows 开发机同步到远端时，不要把多个不同目录的文件一次性上传到
`/home/ubuntu/furunsystemv4/current/`，否则会丢失 `app/runtime`、
`deploy/systemd`、`docs/ops` 的目录结构。先准备 SSH key，再按目录分别同步。

```powershell
$keyDir = Join-Path $env:TEMP "ssh-work"
New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
$keyPath = Join-Path $keyDir "futunsystemv3_deploy_ed25519"
Copy-Item -Force "d:\old\FuRunSystemV4\.keys\futunsystemv3_deploy_ed25519" $keyPath
& "C:\Windows\System32\OpenSSH\ssh.exe" -o StrictHostKeyChecking=no -i $keyPath ubuntu@43.165.166.57 `
  "mkdir -p /home/ubuntu/furunsystemv4/current/app/runtime /home/ubuntu/furunsystemv4/current/deploy/systemd /home/ubuntu/furunsystemv4/current/docs/ops"

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\runtime\worker_config.py" `
  "d:\old\FuRunSystemV4\app\runtime\worker_service.py" `
  "d:\old\FuRunSystemV4\app\runtime\runtime_events.py" `
  "d:\old\FuRunSystemV4\app\runtime\alerting.py" `
  "d:\old\FuRunSystemV4\app\runtime\live_workers.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/runtime/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/deploy/systemd/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/docs/ops/
```

## Remote Setup

1. Upload a filled `.env.worker` that includes Feishu and QQ credentials.
2. Restart both services:

```bash
cd /home/ubuntu/furunsystemv4/current
chmod 600 .env.worker
sudo systemctl daemon-reload
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-consumer.service
```

3. Validate structured logs and channels:

```bash
sudo journalctl -u furun-spot-scanner.service -n 50 --no-pager
sudo journalctl -u furun-spot-consumer.service -n 50 --no-pager
redis-cli ZCARD arb:zset:spot
redis-cli XLEN stream:spot_opps
```

4. Trigger a failure alert by stopping Redis briefly or starting a worker with a missing credential.
5. Confirm:
   - Feishu receives one success notification and one failure notification
   - QQ email receives the critical failure notification
   - `journalctl` contains JSON event lines
```

- [ ] **Step 4: Run full local regression**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests -q
```

Expected: PASS with the existing suite plus the new alerting tests.

- [ ] **Step 5: Sync to remote and validate real alert channels**

Run:

```powershell
$keyDir = Join-Path $env:TEMP "ssh-work"
New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
$keyPath = Join-Path $keyDir "futunsystemv3_deploy_ed25519"
Copy-Item -Force "d:\old\FuRunSystemV4\.keys\futunsystemv3_deploy_ed25519" $keyPath

& "C:\Windows\System32\OpenSSH\ssh.exe" -o StrictHostKeyChecking=no -i $keyPath ubuntu@43.165.166.57 `
  "mkdir -p /home/ubuntu/furunsystemv4/current/app/runtime /home/ubuntu/furunsystemv4/current/deploy/systemd /home/ubuntu/furunsystemv4/current/docs/ops"

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\runtime\worker_config.py" `
  "d:\old\FuRunSystemV4\app\runtime\worker_service.py" `
  "d:\old\FuRunSystemV4\app\runtime\runtime_events.py" `
  "d:\old\FuRunSystemV4\app\runtime\alerting.py" `
  "d:\old\FuRunSystemV4\app\runtime\live_workers.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/runtime/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example" `
  "d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/
```

Then generate a local `.env.worker` with:
- exchange credentials from `d:\old\FuRunSystemV4\local-secrets\五大交易所模拟盘apikey.txt`
- Feishu webhook from `d:\old\FuRunSystemV4\local-secrets\飞书webhook地址.txt`
- QQ email credentials from `d:\old\FuRunSystemV4\local-secrets\qq邮箱.txt`

Upload it:

```powershell
& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "$env:TEMP\furun.alerts.env.worker" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/.env.worker
```

Restart and validate:

```powershell
& "C:\Windows\System32\OpenSSH\ssh.exe" -o StrictHostKeyChecking=no -i $keyPath ubuntu@43.165.166.57 @"
set -e
cd /home/ubuntu/furunsystemv4/current
chmod 600 .env.worker
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-consumer.service
sleep 5
sudo journalctl -u furun-spot-scanner.service -n 30 --no-pager
sudo journalctl -u furun-spot-consumer.service -n 30 --no-pager
redis-cli ZCARD arb:zset:spot
redis-cli XLEN stream:spot_opps
"@
```

Expected:
- both workers stay `active`
- `journalctl` shows JSON event lines
- Feishu receives at least one success event

Finally validate a critical path by temporarily writing an `.env.worker` without one required exchange key, restarting one worker, and confirming:
- Feishu receives a `worker.start_failed`
- QQ email receives the same critical event

- [ ] **Step 6: Commit**

```bash
git add deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md
git commit -m "docs: add live worker alerting deployment guide"
```

## Coverage Check

- Runtime event model and JSON logging: Task 1
- Alert router, severity routing, and dedupe rules: Task 1
- Feishu webhook and QQ email notifier adapters: Task 2
- Alert settings and env parsing: Task 2
- `scanner/consumer/worker_service` event hooks: Task 3
- Env example, ops docs, and real remote alert validation: Task 4

## Self-Review

- Spec coverage: the plan covers event modeling, JSON logs, router rules, notifier channels, thresholded success notifications, dedupe, runtime wiring, env vars, and remote validation.
- Placeholder scan: the plan uses concrete file paths, concrete test code, concrete commands, and only refers to real local secret files that already exist in the workspace.
- Type consistency: `RuntimeEvent`, `AlertRouter`, `AlertSettings`, `FeishuNotifier`, and `EmailNotifier` are defined before later tasks use them.
