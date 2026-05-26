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
        "arb.dispatcher.user_discovered": "套利用户命中",
        "arb.dispatcher.task_created": "套利任务已创建",
        "arb.dispatcher.task_skipped": "套利任务已跳过",
        "arb.executor.execution_result": "套利执行结果",
        "arb.executor.repair_planned": "套利修复已计划",
        "arb.executor.task_failed": "套利任务失败",
        "arb.repair.finished": "套利修复完成",
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
        if event.event_type.startswith("arb."):
            payload = event.payload or {}
            failed_exchanges = ",".join(payload.get("failed_exchanges", []) or []) or "-"
            remaining_failed = (
                ",".join(payload.get("remaining_failed_exchanges", []) or []) or "-"
            )
            return "\n".join(
                [
                    title,
                    f"服务：{event.service}",
                    f"交易对：{event.symbol or '-'}",
                    f"任务类型：{payload.get('task_type', '-')}",
                    f"现货交易所：{payload.get('spot_exchange', '-')}",
                    f"衍生品交易所：{payload.get('derivative_exchange', '-')}",
                    f"失败交易所：{failed_exchanges}",
                    f"剩余失败交易所：{remaining_failed}",
                    f"原因：{payload.get('error', payload.get('reason', event.message))}",
                ]
            )
        if event.event_type == "opportunity.detected":
            return "\n".join(
                [
                    title,
                    f"服务：{event.service}",
                    f"交易对：{event.symbol or '-'}",
                    f"买入交易所：{event.payload.get('buy_exchange', '-')}",
                    f"卖出交易所：{event.payload.get('sell_exchange', '-')}",
                    f"价差：{event.payload.get('spread_bps', '-')} bps",
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
            "content": {"text": self._render_text(event)},
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
        if event.level == "INFO" and event.event_type == "opportunity.detected":
            spread_bps = float(event.payload.get("spread_bps", 0.0))
            if spread_bps > self.success_spread_bps_threshold:
                await self._send_feishu(event)

    def _should_dedupe(self, event: RuntimeEvent) -> bool:
        key = f"{event.event_type}:{event.symbol or '-'}:{event.exchange or '-'}"
        now = self.time_provider()
        last_sent = self._dedupe_cache.get(key)
        dedupe_window_seconds = max(self.dedupe_window_seconds, 300)
        if last_sent is not None and now - last_sent < dedupe_window_seconds:
            return True
        self._dedupe_cache[key] = now
        return False

    async def _send_feishu(self, event: RuntimeEvent) -> None:
        if self.feishu_enabled and self.feishu_notifier is not None:
            await self.feishu_notifier.send(event)

    async def _send_email(self, event: RuntimeEvent) -> None:
        if self.email_enabled and self.email_notifier is not None:
            await self.email_notifier.send(event)
