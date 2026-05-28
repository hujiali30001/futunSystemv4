import asyncio
import os
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Any

import aiohttp


@dataclass(slots=True)
class AnnouncementMessage:
    title: str
    content: str
    channels: list[str]


class AnnouncementNotifier:
    def __init__(self, *, redis_client=None) -> None:
        self._redis = redis_client
        self._feishu_webhook = os.getenv("ANNOUNCEMENT_FEISHU_WEBHOOK", "")
        self._smtp_host = os.getenv("ANNOUNCEMENT_SMTP_HOST", "")
        self._smtp_port = int(os.getenv("ANNOUNCEMENT_SMTP_PORT", "465"))
        self._smtp_user = os.getenv("ANNOUNCEMENT_SMTP_USER", "")
        self._smtp_pass = os.getenv("ANNOUNCEMENT_SMTP_PASS", "")
        self._email_from = os.getenv("ANNOUNCEMENT_EMAIL_FROM", self._smtp_user)

    async def publish(self, message: AnnouncementMessage) -> dict[str, str]:
        results: dict[str, str] = {}
        tasks: list[tuple[str, Any]] = []

        for channel in message.channels:
            if channel == "feishu" and self._feishu_webhook:
                tasks.append((channel, asyncio.create_task(self._send_feishu(message))))
            elif channel == "email" and self._smtp_host:
                tasks.append((channel, asyncio.create_task(self._send_email(message))))
            else:
                results[channel] = "skipped"

        for channel, task in tasks:
            try:
                await task
                results[channel] = "sent"
            except Exception as exc:
                results[channel] = f"failed: {exc}"

        return results

    async def publish_to_all_users(
        self, message: AnnouncementMessage, user_ids: list[int]
    ) -> dict[str, str]:
        results: dict[str, str] = {}
        for channel in message.channels:
            if channel in ("feishu", "email"):
                user_contacts = await self._load_user_contacts(user_ids, channel)
                results[channel] = f"{len(user_contacts)} users"
                if channel == "feishu":
                    await self._send_feishu_batch(message, user_contacts)
                elif channel == "email":
                    await self._send_email_batch(message, user_contacts)
        return results

    async def _load_user_contacts(
        self, user_ids: list[int], channel: str
    ) -> list[str]:
        if self._redis is None:
            return []
        contacts: list[str] = []
        for uid in user_ids:
            if channel == "email":
                email = await self._redis.hget(f"user:{uid}", "email")
                if email:
                    contacts.append(email)
            elif channel == "feishu":
                webhook = await self._redis.hget(f"user:{uid}", "feishu_webhook_url")
                if webhook:
                    contacts.append(webhook)
        return contacts

    async def _send_feishu(self, message: AnnouncementMessage) -> None:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": message.title},
                    "template": "red",
                },
                "elements": [
                    {"tag": "markdown", "content": message.content},
                ],
            },
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self._feishu_webhook, json=payload, timeout=10) as resp:
                resp.raise_for_status()

    async def _send_email(self, message: AnnouncementMessage) -> None:
        msg = MIMEText(message.content, "plain", "utf-8")
        msg["Subject"] = message.title
        msg["From"] = self._email_from
        msg["To"] = self._email_from
        await asyncio.to_thread(self._smtp_send, msg.as_string(), [self._email_from])

    async def _send_feishu_batch(
        self, message: AnnouncementMessage, webhooks: list[str]
    ) -> None:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": message.title},
                    "template": "red",
                },
                "elements": [
                    {"tag": "markdown", "content": message.content},
                ],
            },
        }
        async with aiohttp.ClientSession() as session:
            tasks = []
            for wh in webhooks:
                tasks.append(session.post(wh, json=payload, timeout=10))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for wh, result in zip(webhooks, results):
                if isinstance(result, Exception):
                    print(f"[notifier] feishu send failed {wh[:40]}: {result}")

    async def _send_email_batch(
        self, message: AnnouncementMessage, recipients: list[str]
    ) -> None:
        msg = MIMEText(message.content, "plain", "utf-8")
        msg["Subject"] = message.title
        msg["From"] = self._email_from
        await asyncio.to_thread(self._smtp_send, msg.as_string(), recipients)

    def _smtp_send(self, message_str: str, recipients: list[str]) -> None:
        with smtplib.SMTP_SSL(self._smtp_host, self._smtp_port) as server:
            server.login(self._smtp_user, self._smtp_pass)
            server.sendmail(self._email_from, recipients, message_str)
