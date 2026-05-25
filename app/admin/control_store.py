from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw_value: str | bytes | None) -> dict[str, Any] | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8")
    return json.loads(raw_value)


@dataclass(slots=True)
class LimitRuleRecord:
    rule_id: str
    scope_type: str
    scope_id: str
    limit_type: str
    limit_value: float
    enabled: bool
    priority: int
    symbol: str | None = None
    exchange: str | None = None
    strategy_id: int | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class PlatformSwitchRecord:
    switch_key: str
    scope_type: str
    scope_id: str
    enabled: bool
    updated_at: str | None = None


@dataclass(slots=True)
class AnnouncementRecord:
    announcement_id: str
    title: str
    content: str
    priority: int
    is_pinned: bool
    audience_type: str
    audience_filter: dict[str, Any]
    channels: list[str]
    status: str
    updated_at: str | None = None


class ControlPlaneStore:
    LIMIT_INDEX_KEY = "control:limits:index"
    SWITCH_INDEX_KEY = "control:switches:index"
    ANNOUNCEMENT_INDEX_KEY = "control:announcements:index"

    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    @staticmethod
    def _limit_key(rule_id: str) -> str:
        return f"control:limits:{rule_id}"

    @staticmethod
    def _switch_id(switch_key: str, *, scope_type: str, scope_id: str) -> str:
        return f"{switch_key}:{scope_type}:{scope_id}"

    @classmethod
    def _switch_key(cls, switch_key: str, *, scope_type: str, scope_id: str) -> str:
        switch_id = cls._switch_id(switch_key, scope_type=scope_type, scope_id=scope_id)
        return f"control:switches:{switch_id}"

    @staticmethod
    def _announcement_key(announcement_id: str) -> str:
        return f"control:announcements:{announcement_id}"

    async def put_limit_rule(self, record: LimitRuleRecord) -> None:
        payload = asdict(record)
        payload["updated_at"] = payload["updated_at"] or _utc_now_iso()
        await self.redis_client.set(
            self._limit_key(record.rule_id),
            json.dumps(payload),
        )
        await self.redis_client.sadd(self.LIMIT_INDEX_KEY, record.rule_id)

    async def list_limit_rules(self) -> list[LimitRuleRecord]:
        results: list[LimitRuleRecord] = []
        for rule_id in sorted(await self.redis_client.smembers(self.LIMIT_INDEX_KEY)):
            payload = _decode_json(await self.redis_client.get(self._limit_key(rule_id)))
            if payload is not None:
                results.append(LimitRuleRecord(**payload))
        return results

    async def delete_limit_rule(self, rule_id: str) -> None:
        await self.redis_client.delete(self._limit_key(rule_id))
        await self.redis_client.srem(self.LIMIT_INDEX_KEY, rule_id)

    async def put_switch(self, record: PlatformSwitchRecord) -> None:
        payload = asdict(record)
        payload["updated_at"] = payload["updated_at"] or _utc_now_iso()
        switch_id = self._switch_id(
            record.switch_key,
            scope_type=record.scope_type,
            scope_id=record.scope_id,
        )
        await self.redis_client.set(
            self._switch_key(
                record.switch_key,
                scope_type=record.scope_type,
                scope_id=record.scope_id,
            ),
            json.dumps(payload),
        )
        await self.redis_client.sadd(self.SWITCH_INDEX_KEY, switch_id)

    async def list_switches(self) -> list[PlatformSwitchRecord]:
        results: list[PlatformSwitchRecord] = []
        for switch_id in sorted(await self.redis_client.smembers(self.SWITCH_INDEX_KEY)):
            switch_key, scope_type, scope_id = str(switch_id).split(":", 2)
            payload = _decode_json(
                await self.redis_client.get(
                    self._switch_key(
                        switch_key,
                        scope_type=scope_type,
                        scope_id=scope_id,
                    )
                )
            )
            if payload is not None:
                results.append(PlatformSwitchRecord(**payload))
        return results

    async def delete_switch(self, switch_key: str, *, scope_type: str, scope_id: str) -> None:
        switch_id = self._switch_id(switch_key, scope_type=scope_type, scope_id=scope_id)
        await self.redis_client.delete(
            self._switch_key(switch_key, scope_type=scope_type, scope_id=scope_id)
        )
        await self.redis_client.srem(self.SWITCH_INDEX_KEY, switch_id)

    async def put_announcement(self, record: AnnouncementRecord) -> None:
        payload = asdict(record)
        payload["updated_at"] = payload["updated_at"] or _utc_now_iso()
        await self.redis_client.set(
            self._announcement_key(record.announcement_id),
            json.dumps(payload),
        )
        await self.redis_client.sadd(self.ANNOUNCEMENT_INDEX_KEY, record.announcement_id)

    async def list_announcements(self) -> list[AnnouncementRecord]:
        results: list[AnnouncementRecord] = []
        for announcement_id in sorted(
            await self.redis_client.smembers(self.ANNOUNCEMENT_INDEX_KEY)
        ):
            payload = _decode_json(
                await self.redis_client.get(self._announcement_key(announcement_id))
            )
            if payload is not None:
                results.append(AnnouncementRecord(**payload))
        return results

    async def delete_announcement(self, announcement_id: str) -> None:
        await self.redis_client.delete(self._announcement_key(announcement_id))
        await self.redis_client.srem(self.ANNOUNCEMENT_INDEX_KEY, announcement_id)
