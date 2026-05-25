# Live Workers Alerting Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce live worker notification volume and switch Feishu/QQ notification content to Chinese without changing internal event models or structured journal logs.

**Architecture:** Keep `RuntimeEvent`, `event_type`, and JSON logging unchanged, and only tune the alerting presentation and routing layer. The implementation adds Chinese renderers to the Feishu and email notifiers, tightens success and error routing in `AlertRouter`, updates default env thresholds, and validates the behavior both locally and on the remote server.

**Tech Stack:** Python 3.10+, asyncio, json, smtplib, email.message, urllib.request, pytest, pytest-asyncio, systemd

---

## Planned File Structure

**Modify**
- `app/runtime/alerting.py`
- `tests/test_alerting.py`
- `deploy/systemd/.env.worker.example`
- `docs/ops/live-workers-systemd.md`

## Task 1: Render Feishu And QQ Notifications In Chinese

**Files:**
- Modify: `app/runtime/alerting.py`
- Modify: `tests/test_alerting.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_alerting.py
import json

from app.runtime.alerting import EmailNotifier, FeishuNotifier
from app.runtime.runtime_events import RuntimeEvent


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


def test_feishu_notifier_renders_chinese_success_message():
    captured = {}

    def fake_urlopen(request, timeout=5):
        captured["body"] = request.data
        return FakeHttpResponse()

    notifier = FeishuNotifier(
        webhook_url="https://example.test/hook",
        urlopen=fake_urlopen,
    )
    event = RuntimeEvent(
        event_type="opportunity.detected",
        level="INFO",
        service="scanner",
        message="opportunity detected",
        symbol="BTC/USDT",
        payload={
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
            "spread_bps": 88.0,
        },
    )

    notifier.send_sync(event)
    body = json.loads(captured["body"].decode("utf-8"))

    assert "检测到套利机会" in body["content"]["text"]
    assert "交易对：BTC/USDT" in body["content"]["text"]
    assert "买入交易所：bitget" in body["content"]["text"]
    assert "卖出交易所：gate" in body["content"]["text"]


def test_email_notifier_renders_chinese_critical_subject_and_body():
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
        recipients=["alice@qq.com"],
        smtp_factory=fake_smtp,
    )
    event = RuntimeEvent(
        event_type="worker.start_failed",
        level="CRITICAL",
        service="scanner",
        region="default",
        message="worker start failed",
        payload={"error": "missing credentials for exchanges: okx"},
    )

    notifier.send_sync(event)
    message = smtp_instances[0].messages[0]

    assert message["Subject"] == "[严重告警] 服务启动失败"
    assert "服务：scanner" in message.get_content()
    assert "区域：default" in message.get_content()
    assert "原因：missing credentials for exchanges: okx" in message.get_content()
    assert "原始事件：worker.start_failed" in message.get_content()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_alerting.py -q
```

Expected: FAIL because the current notifier templates still render English subjects and bodies.

- [ ] **Step 3: Write the minimal implementation**

```python
# app/runtime/alerting.py
import asyncio
import json
import smtplib
import sys
from dataclasses import dataclass, field
from email.message import EmailMessage
from time import time
from typing import Any, Callable, TextIO
from urllib.request import Request, urlopen

from app.runtime.runtime_events import RuntimeEvent


def _event_title_zh(event: RuntimeEvent) -> str:
    mapping = {
        "worker.start_failed": "服务启动失败",
        "worker.started": "服务已启动",
        "worker.stopped": "服务已停止",
        "scanner.iteration.failed": "扫描任务异常",
        "consumer.message.failed": "机会消费异常",
        "opportunity.detected": "检测到套利机会",
    }
    return mapping.get(event.event_type, event.event_type)


class StructuredEventLogger:
    def __init__(
        self,
        sink: Callable[[str], None] | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self.sink = sink
        self.stream = stream

    def record(self, event: RuntimeEvent) -> None:
        line = event.to_json()
        if self.sink is not None:
            self.sink(line)
            return
        output = self.stream or sys.stdout
        output.write(line + "\n")
        output.flush()


class FeishuNotifier:
    def __init__(
        self,
        webhook_url: str,
        urlopen: Callable[..., Any] = urlopen,
    ) -> None:
        self.webhook_url = webhook_url
        self.urlopen = urlopen

    def _render_text(self, event: RuntimeEvent) -> str:
        title = _event_title_zh(event)
        if event.event_type == "opportunity.detected":
            return "\n".join(
                [
                    title,
                    f"服务：{event.service}",
                    f"交易对：{event.symbol or '-'}",
                    f"买入交易所：{event.payload.get('buy_exchange', '-')}",
                    f"卖出交易所：{event.payload.get('sell_exchange', '-')}",
                    f"价差：{event.payload.get('spread_bps', '-')}" + " bps",
                ]
            )
        if event.level == "CRITICAL":
            return "\n".join(
                [
                    title,
                    f"服务：{event.service}",
                    f"区域：{event.region or '-'}",
                    f"原因：{event.payload.get('error', event.message)}",
                ]
            )
        return "\n".join(
            [
                title,
                f"服务：{event.service}",
                f"交易对：{event.symbol or '-'}",
                f"交易所：{event.exchange or '-'}",
                f"原因：{event.payload.get('error', event.message)}",
            ]
        )

    def _build_payload(self, event: RuntimeEvent) -> dict[str, Any]:
        return {
            "msg_type": "text",
            "content": {
                "text": self._render_text(event)
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

    def _subject(self, event: RuntimeEvent) -> str:
        if event.level == "CRITICAL":
            return f"[严重告警] {_event_title_zh(event)}"
        return f"[通知] {_event_title_zh(event)}"

    def _body(self, event: RuntimeEvent) -> str:
        return "\n".join(
            [
                _event_title_zh(event),
                f"服务：{event.service}",
                f"区域：{event.region or '-'}",
                f"时间：{event.created_at}",
                f"原因：{event.payload.get('error', event.message)}",
                f"原始事件：{event.event_type}",
            ]
        )

    def send_sync(self, event: RuntimeEvent) -> dict[str, str]:
        message = EmailMessage()
        message["From"] = self.username
        message["To"] = ", ".join(self.recipients)
        message["Subject"] = self._subject(event)
        message.set_content(self._body(event))
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
    time_provider: Callable[[], float] = field(default_factory=lambda: time)
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
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_alerting.py -q
```

Expected: PASS with the notifier rendering tests green.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/alerting.py tests/test_alerting.py
git commit -m "feat: render live worker notifications in chinese"
```

## Task 2: Reduce Success Notifications And Lengthen Error Dedupe

**Files:**
- Modify: `app/runtime/alerting.py`
- Modify: `tests/test_alerting.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_alerting.py
import pytest

from app.runtime.alerting import AlertRouter
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


@pytest.mark.asyncio
async def test_alert_router_does_not_send_consumer_processed_notifications():
    feishu = FakeNotifier()
    router = AlertRouter(
        logger=FakeLogger(),
        feishu_notifier=feishu,
        alerts_enabled=True,
        feishu_enabled=True,
        success_spread_bps_threshold=20.0,
        dedupe_window_seconds=300,
    )
    event = RuntimeEvent(
        event_type="consumer.message.processed",
        level="INFO",
        service="consumer",
        message="consumer message processed",
        symbol="BTC/USDT",
        payload={"spread_bps": 88.0},
    )

    await router.dispatch(event)

    assert len(feishu.events) == 0


@pytest.mark.asyncio
async def test_alert_router_sends_only_high_value_opportunity_notifications():
    feishu = FakeNotifier()
    router = AlertRouter(
        logger=FakeLogger(),
        feishu_notifier=feishu,
        alerts_enabled=True,
        feishu_enabled=True,
        success_spread_bps_threshold=20.0,
        dedupe_window_seconds=300,
    )
    low_event = RuntimeEvent(
        event_type="opportunity.detected",
        level="INFO",
        service="scanner",
        message="opportunity detected",
        symbol="BTC/USDT",
        payload={"spread_bps": 10.0},
    )
    high_event = RuntimeEvent(
        event_type="opportunity.detected",
        level="INFO",
        service="scanner",
        message="opportunity detected",
        symbol="BTC/USDT",
        payload={"spread_bps": 25.0},
    )

    await router.dispatch(low_event)
    await router.dispatch(high_event)

    assert len(feishu.events) == 1
    assert feishu.events[0].payload["spread_bps"] == 25.0


@pytest.mark.asyncio
async def test_alert_router_uses_longer_error_dedupe_window():
    timestamps = iter([100.0, 150.0, 401.0])
    feishu = FakeNotifier()
    router = AlertRouter(
        logger=FakeLogger(),
        feishu_notifier=feishu,
        alerts_enabled=True,
        feishu_enabled=True,
        success_spread_bps_threshold=20.0,
        dedupe_window_seconds=300,
        time_provider=lambda: next(timestamps),
    )
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
    await router.dispatch(event)

    assert len(feishu.events) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_alerting.py -q
```

Expected: FAIL because the current router still sends `consumer.message.processed` when spread passes threshold and still uses the old lower default tuning assumptions.

- [ ] **Step 3: Write the minimal implementation**

```python
# app/runtime/alerting.py
import asyncio
import json
import smtplib
import sys
from dataclasses import dataclass, field
from email.message import EmailMessage
from time import time
from typing import Any, Callable, TextIO
from urllib.request import Request, urlopen

from app.runtime.runtime_events import RuntimeEvent


def _event_title_zh(event: RuntimeEvent) -> str:
    mapping = {
        "worker.start_failed": "服务启动失败",
        "worker.started": "服务已启动",
        "worker.stopped": "服务已停止",
        "scanner.iteration.failed": "扫描任务异常",
        "consumer.message.failed": "机会消费异常",
        "opportunity.detected": "检测到套利机会",
    }
    return mapping.get(event.event_type, event.event_type)


class StructuredEventLogger:
    def __init__(
        self,
        sink: Callable[[str], None] | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self.sink = sink
        self.stream = stream

    def record(self, event: RuntimeEvent) -> None:
        line = event.to_json()
        if self.sink is not None:
            self.sink(line)
            return
        output = self.stream or sys.stdout
        output.write(line + "\n")
        output.flush()


class FeishuNotifier:
    def __init__(
        self,
        webhook_url: str,
        urlopen: Callable[..., Any] = urlopen,
    ) -> None:
        self.webhook_url = webhook_url
        self.urlopen = urlopen

    def _render_text(self, event: RuntimeEvent) -> str:
        title = _event_title_zh(event)
        if event.event_type == "opportunity.detected":
            return "\n".join(
                [
                    title,
                    f"服务：{event.service}",
                    f"交易对：{event.symbol or '-'}",
                    f"买入交易所：{event.payload.get('buy_exchange', '-')}",
                    f"卖出交易所：{event.payload.get('sell_exchange', '-')}",
                    f"价差：{event.payload.get('spread_bps', '-')}" + " bps",
                ]
            )
        if event.level == "CRITICAL":
            return "\n".join(
                [
                    title,
                    f"服务：{event.service}",
                    f"区域：{event.region or '-'}",
                    f"原因：{event.payload.get('error', event.message)}",
                ]
            )
        return "\n".join(
            [
                title,
                f"服务：{event.service}",
                f"交易对：{event.symbol or '-'}",
                f"交易所：{event.exchange or '-'}",
                f"原因：{event.payload.get('error', event.message)}",
            ]
        )

    def _build_payload(self, event: RuntimeEvent) -> dict[str, Any]:
        return {
            "msg_type": "text",
            "content": {
                "text": self._render_text(event)
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

    def _subject(self, event: RuntimeEvent) -> str:
        if event.level == "CRITICAL":
            return f"[严重告警] {_event_title_zh(event)}"
        return f"[通知] {_event_title_zh(event)}"

    def _body(self, event: RuntimeEvent) -> str:
        return "\n".join(
            [
                _event_title_zh(event),
                f"服务：{event.service}",
                f"区域：{event.region or '-'}",
                f"时间：{event.created_at}",
                f"原因：{event.payload.get('error', event.message)}",
                f"原始事件：{event.event_type}",
            ]
        )

    def send_sync(self, event: RuntimeEvent) -> dict[str, str]:
        message = EmailMessage()
        message["From"] = self.username
        message["To"] = ", ".join(self.recipients)
        message["Subject"] = self._subject(event)
        message.set_content(self._body(event))
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
    success_spread_bps_threshold: float = 20.0
    dedupe_window_seconds: int = 300
    time_provider: Callable[[], float] = field(default_factory=lambda: time)
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
        if event.level == "INFO" and event.event_type == "opportunity.detected":
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
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_alerting.py -q
```

Expected: PASS with the new routing tests green and the notifier rendering tests still green.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/alerting.py tests/test_alerting.py
git commit -m "feat: tune live worker alert routing"
```

## Task 3: Update Default Alert Thresholds, Docs, And Validate On The Remote Host

**Files:**
- Modify: `deploy/systemd/.env.worker.example`
- Modify: `docs/ops/live-workers-systemd.md`

- [ ] **Step 1: Write the failing configuration test**

```python
# tests/test_worker_config.py
from app.runtime.worker_config import AlertSettings


def test_alert_settings_parse_recipient_list_from_env(monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_TO", "alice@qq.com,bob@qq.com")

    settings = AlertSettings()

    assert settings.alert_email_to == ["alice@qq.com", "bob@qq.com"]
```

- [ ] **Step 2: Run the focused test to verify it fails only if missing**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_worker_config.py -q
```

Expected: if the test does not exist yet, add it and watch it fail first; if it already exists and passes, leave the test file unchanged and continue.

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
# Alert routing
# Feishu notifications are rendered in Chinese.
# QQ email notifications are rendered in Chinese.
# Success notifications are only sent for high-value opportunities.
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

## Alert Config

- 飞书通知现在使用中文文案。
- QQ 邮件通知现在使用中文文案。
- `consumer.message.processed` 不再发送成功通知。
- 成功通知只保留 `opportunity.detected`，且默认需要 `spread_bps >= 20`。
- 一般异常的默认去重窗口调整为 `300` 秒。
- `CRITICAL` 仍然立即发送飞书和 QQ 邮件。

## Remote Validation

1. 上传更新后的 `app/runtime/alerting.py`、`.env.worker.example`、`docs/ops/live-workers-systemd.md`
2. 将远端 `.env.worker` 中的以下默认值同步更新：

```dotenv
ALERT_SUCCESS_SPREAD_BPS_THRESHOLD=20
ALERT_DEDUPE_WINDOW_SECONDS=300
```

3. 重启服务：

```bash
cd /home/ubuntu/furunsystemv4/current
chmod 600 .env.worker
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-consumer.service
```

4. 验证成功通知变少：

```bash
sudo journalctl -u furun-spot-consumer.service -n 50 --no-pager | grep '"event_type"'
sudo journalctl -u furun-spot-scanner.service -n 50 --no-pager | grep '"event_type"'
```

确认点：
- `journalctl` 仍然保留英文 JSON 字段
- 飞书 success 通知文案为中文
- 飞书 success 通知数量明显减少

5. 触发一条 `CRITICAL` 告警并确认：
- 飞书文案为中文
- QQ 邮件标题和正文为中文
```

- [ ] **Step 4: Run full local regression**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests -q
```

Expected: PASS with the tuning tests and the existing alerting/worker tests all green.

- [ ] **Step 5: Sync to the remote host and validate the tuned behavior**

Run:

```powershell
$keyDir = "d:\old\FuRunSystemV4\.tmp-ssh"
New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
$keyPath = Join-Path $keyDir "futunsystemv3_deploy_ed25519"
Copy-Item -Force "d:\old\FuRunSystemV4\.keys\futunsystemv3_deploy_ed25519" $keyPath
& "C:\Windows\System32\icacls.exe" $keyPath /inheritance:r | Out-Null
& "C:\Windows\System32\icacls.exe" $keyPath /grant:r "${env:USERNAME}:(R)" | Out-Null

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\runtime\alerting.py" `
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

env_path = Path(".env.worker")
content = env_path.read_text(encoding="utf-8")
content = content.replace("ALERT_SUCCESS_SPREAD_BPS_THRESHOLD=0", "ALERT_SUCCESS_SPREAD_BPS_THRESHOLD=20")
content = content.replace("ALERT_DEDUPE_WINDOW_SECONDS=60", "ALERT_DEDUPE_WINDOW_SECONDS=300")
env_path.write_text(content, encoding="utf-8")
PY
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-consumer.service
sleep 5
sudo journalctl -u furun-spot-scanner.service -n 30 --no-pager | grep '"event_type"'
sudo journalctl -u furun-spot-consumer.service -n 30 --no-pager | grep '"event_type"'
"@
```

Expected:
- workers stay `active`
- `journalctl` still shows English JSON lines
- Feishu success notifications are fewer than before and use Chinese wording

Finally trigger one `CRITICAL` path again by temporarily removing `OKX_API_KEY`, then confirm:
- Feishu receives a Chinese `服务启动失败`
- QQ email receives a Chinese subject `[严重告警] 服务启动失败`

- [ ] **Step 6: Commit**

```bash
git add deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md
git commit -m "docs: tune live worker alert defaults"
```

## Coverage Check

- Chinese Feishu rendering: Task 1
- Chinese QQ email rendering: Task 1
- Reduced success notification volume: Task 2
- Longer error dedupe window: Task 2
- Env default tuning and docs updates: Task 3
- Remote validation of the tuned behavior: Task 3

## Self-Review

- Spec coverage: the plan only changes the notification layer, keeps internal English events and journal JSON intact, tunes the success threshold, lengthens error dedupe, and validates the new Chinese delivery on the remote server.
- Placeholder scan: the plan contains exact file paths, concrete tests, concrete commands, and explicit env values.
- Type consistency: `AlertRouter`, `FeishuNotifier`, `EmailNotifier`, and `AlertSettings` keep the same names and responsibilities as the current codebase.
