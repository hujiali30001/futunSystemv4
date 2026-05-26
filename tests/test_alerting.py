import json

import pytest

from app.runtime.alerting import (
    AlertRouter,
    EmailNotifier,
    FeishuNotifier,
    StructuredEventLogger,
)
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.worker_config import AlertSettings


class FakeLogger:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


class FakeStream:
    def __init__(self):
        self.chunks = []
        self.flush_calls = 0

    def write(self, text):
        self.chunks.append(text)

    def flush(self):
        self.flush_calls += 1


class FakeNotifier:
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)
        return {"status": "ok"}


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
        opportunity_feishu_enabled=True,
        time_provider=lambda: 100.0,
    )


def test_structured_event_logger_records_json_lines():
    lines = []
    logger = StructuredEventLogger(sink=lines.append)
    event = RuntimeEvent(
        event_type="worker.started",
        level="INFO",
        service="scanner",
        message="worker started",
    )

    logger.record(event)

    assert len(lines) == 1
    assert '"event_type":"worker.started"' in lines[0]


def test_structured_event_logger_flushes_stream_output():
    stream = FakeStream()
    logger = StructuredEventLogger(stream=stream)
    event = RuntimeEvent(
        event_type="worker.started",
        level="INFO",
        service="scanner",
        message="worker started",
    )

    logger.record(event)

    assert stream.chunks == [event.to_json() + "\n"]
    assert stream.flush_calls == 1


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
    assert "服务启动失败" in body["content"]["text"]
    assert "原因：missing credentials" in body["content"]["text"]
    assert result["status"] == "ok"


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


def test_feishu_notifier_renders_arbitrage_failure_message():
    captured = {}

    def fake_urlopen(request, timeout=5):
        captured["body"] = request.data
        return FakeHttpResponse()

    notifier = FeishuNotifier(
        webhook_url="https://example.test/hook",
        urlopen=fake_urlopen,
    )
    event = RuntimeEvent(
        event_type="arb.executor.task_failed",
        level="ERROR",
        service="arb_executor",
        message="arbitrage executor task failed",
        symbol="BTC/USDT",
        payload={
            "task_uuid": "arb-close-1",
            "task_type": "close",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "failed_exchanges": ["binance", "okx"],
            "error": "FAILED",
        },
    )

    notifier.send_sync(event)
    body = json.loads(captured["body"].decode("utf-8"))

    assert "套利任务失败" in body["content"]["text"]
    assert "交易对：BTC/USDT" in body["content"]["text"]
    assert "任务类型：close" in body["content"]["text"]
    assert "现货交易所：binance" in body["content"]["text"]
    assert "衍生品交易所：okx" in body["content"]["text"]
    assert "原因：FAILED" in body["content"]["text"]


def test_feishu_notifier_renders_arbitrage_recovery_exhausted_message():
    captured = {}

    def fake_urlopen(request, timeout=5):
        captured["body"] = request.data
        return FakeHttpResponse()

    notifier = FeishuNotifier(
        webhook_url="https://example.test/hook",
        urlopen=fake_urlopen,
    )
    event = RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        message="arbitrage recovery exhausted",
        symbol="BTC/USDT",
        payload={
            "task_uuid": "arb-close-exhausted-evt",
            "task_type": "close",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "failure_reason": "execution_failed_non_repairable",
            "retry_count": 2,
            "max_retry_count": 2,
            "auto_recovery_status": "EXHAUSTED",
            "next_action": "EXHAUSTED",
        },
    )

    notifier.send_sync(event)
    body = json.loads(captured["body"].decode("utf-8"))

    assert "套利自动恢复已耗尽" in body["content"]["text"]
    assert "交易对：BTC/USDT" in body["content"]["text"]
    assert "任务类型：close" in body["content"]["text"]
    assert "现货交易所：binance" in body["content"]["text"]
    assert "衍生品交易所：okx" in body["content"]["text"]
    assert "恢复状态：EXHAUSTED" in body["content"]["text"]
    assert "重试次数：2/2" in body["content"]["text"]


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
    assert message["Subject"] == "[严重告警] 服务启动失败"
    assert message["To"] == "alice@qq.com, bob@qq.com"
    assert "missing credentials" in message.get_content()
    assert result["status"] == "ok"


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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_alert_router_does_not_send_executor_processed_notifications():
    router = build_router()
    event = RuntimeEvent(
        event_type="executor.task.processed",
        level="INFO",
        service="executor",
        message="executor task processed",
        symbol="BTC/USDT",
        payload={"spread_bps": 80.0},
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert len(router.feishu_notifier.events) == 0
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
async def test_alert_router_does_not_send_external_notifications_for_control_rule_events():
    router = build_router()
    event = RuntimeEvent(
        event_type="control.rule.blocked",
        level="INFO",
        service="dispatcher",
        region="main",
        symbol="BTC/USDT",
        exchange="okx",
        message="control rule blocked request",
        payload={
            "user_id": "42",
            "source_message_id": "1-0",
            "requested_notional": 100.0,
            "approved_notional": 0.0,
            "reason": "reduce_only",
        },
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert router.logger.events[0].event_type == "control.rule.blocked"
    assert len(router.feishu_notifier.events) == 0
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
async def test_alert_router_does_not_send_feishu_for_info_arbitrage_events():
    router = build_router()
    event = RuntimeEvent(
        event_type="arb.dispatcher.task_created",
        level="INFO",
        service="arb_dispatcher",
        message="arbitrage dispatcher task created",
        symbol="BTC/USDT",
        payload={"task_uuid": "arb-open-1"},
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert len(router.feishu_notifier.events) == 0
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "message"),
    [
        ("arb.recovery.retry_scheduled", "arbitrage recovery retry scheduled"),
        ("arb.recovery.cooldown_started", "arbitrage recovery cooldown started"),
    ],
)
async def test_alert_router_does_not_send_feishu_for_info_recovery_events(
    event_type, message
):
    router = build_router()
    event = RuntimeEvent(
        event_type=event_type,
        level="INFO",
        service="arb_executor",
        message=message,
        symbol="BTC/USDT",
        payload={
            "task_uuid": "arb-recovery-1",
            "task_type": "close",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "retry_count": 1,
            "max_retry_count": 2,
            "auto_recovery_status": "RETRY_PENDING",
        },
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert len(router.feishu_notifier.events) == 0
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
async def test_alert_router_sends_feishu_for_error_arbitrage_repair_event():
    router = build_router()
    event = RuntimeEvent(
        event_type="arb.repair.finished",
        level="ERROR",
        service="arb_repair",
        message="arbitrage repair finished",
        symbol="BTC/USDT",
        payload={
            "task_uuid": "arb-open-2",
            "task_type": "open",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "status": "MANUAL_REQUIRED",
            "remaining_failed_exchanges": ["okx"],
            "reason": "repair order failed",
            "error": "repair order failed",
        },
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert len(router.feishu_notifier.events) == 1
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
async def test_alert_router_sends_feishu_for_error_recovery_exhausted_event():
    router = build_router()
    event = RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        message="arbitrage recovery exhausted",
        symbol="BTC/USDT",
        payload={
            "task_uuid": "arb-recovery-2",
            "task_type": "close",
            "spot_exchange": "binance",
            "derivative_exchange": "okx",
            "failure_reason": "execution_failed_non_repairable",
            "retry_count": 2,
            "max_retry_count": 2,
            "auto_recovery_status": "EXHAUSTED",
            "next_action": "EXHAUSTED",
        },
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert len(router.feishu_notifier.events) == 1
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
async def test_alert_router_routes_opportunity_only_above_threshold():
    router = build_router()
    threshold_event = RuntimeEvent(
        event_type="opportunity.detected",
        level="INFO",
        service="scanner",
        message="opportunity detected",
        symbol="BTC/USDT",
        payload={"spread_bps": 50.0},
    )
    above_threshold_event = RuntimeEvent(
        event_type="opportunity.detected",
        level="INFO",
        service="scanner",
        message="opportunity detected",
        symbol="BTC/USDT",
        payload={"spread_bps": 50.1},
    )

    await router.dispatch(threshold_event)
    await router.dispatch(above_threshold_event)

    assert len(router.logger.events) == 2
    assert len(router.feishu_notifier.events) == 1
    assert router.feishu_notifier.events[0].payload["spread_bps"] == 50.1
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
async def test_alert_router_suppresses_opportunity_feishu_when_disabled():
    router = AlertRouter(
        logger=FakeLogger(),
        feishu_notifier=FakeNotifier(),
        email_notifier=FakeNotifier(),
        alerts_enabled=True,
        feishu_enabled=True,
        email_enabled=True,
        success_spread_bps_threshold=0.0,
        dedupe_window_seconds=60,
        opportunity_feishu_enabled=False,
        time_provider=lambda: 100.0,
    )
    event = RuntimeEvent(
        event_type="opportunity.detected",
        level="INFO",
        service="scanner",
        message="opportunity detected",
        symbol="BTC/USDT",
        payload={"spread_bps": 100.0},
    )

    await router.dispatch(event)

    assert len(router.logger.events) == 1
    assert len(router.feishu_notifier.events) == 0
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
async def test_alert_router_uses_300_second_window_for_error_deduplication():
    timestamps = iter([100.0, 250.0, 401.0])
    router = AlertRouter(
        logger=FakeLogger(),
        feishu_notifier=FakeNotifier(),
        email_notifier=FakeNotifier(),
        alerts_enabled=True,
        feishu_enabled=True,
        email_enabled=True,
        success_spread_bps_threshold=50.0,
        dedupe_window_seconds=60,
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

    assert len(router.logger.events) == 3
    assert len(router.feishu_notifier.events) == 2
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_alert_router_dedupes_recovery_exhausted_by_task_uuid():
    timestamps = iter([100.0, 120.0, 121.0])
    router = AlertRouter(
        logger=FakeLogger(),
        feishu_notifier=FakeNotifier(),
        email_notifier=FakeNotifier(),
        alerts_enabled=True,
        feishu_enabled=True,
        email_enabled=True,
        success_spread_bps_threshold=50.0,
        dedupe_window_seconds=60,
        time_provider=lambda: next(timestamps),
    )
    first = RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        message="arbitrage recovery exhausted",
        symbol="BTC/USDT",
        exchange="binance",
        payload={"task_uuid": "arb-exhausted-1", "error": "TRANSIENT_NETWORK"},
    )
    duplicate = RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        message="arbitrage recovery exhausted",
        symbol="BTC/USDT",
        exchange="okx",
        payload={"task_uuid": "arb-exhausted-1", "error": "TRANSIENT_NETWORK"},
    )
    different_task = RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        message="arbitrage recovery exhausted",
        symbol="BTC/USDT",
        exchange="okx",
        payload={"task_uuid": "arb-exhausted-2", "error": "TRANSIENT_NETWORK"},
    )

    await router.dispatch(first)
    await router.dispatch(duplicate)
    await router.dispatch(different_task)

    assert len(router.logger.events) == 3
    assert len(router.feishu_notifier.events) == 2
    assert router.feishu_notifier.events[0].payload["task_uuid"] == "arb-exhausted-1"
    assert router.feishu_notifier.events[1].payload["task_uuid"] == "arb-exhausted-2"
    assert len(router.email_notifier.events) == 0


@pytest.mark.asyncio
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
