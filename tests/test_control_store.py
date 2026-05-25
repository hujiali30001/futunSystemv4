import pytest

from app.admin.control_store import (
    AnnouncementRecord,
    ControlPlaneStore,
    LimitRuleRecord,
    PlatformSwitchRecord,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.set_members = {}

    async def set(self, key, value):
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        existed = key in self.values
        self.values.pop(key, None)
        return 1 if existed else 0

    async def sadd(self, key, *values):
        self.set_members.setdefault(key, set()).update(values)
        return len(values)

    async def srem(self, key, *values):
        members = self.set_members.setdefault(key, set())
        removed = 0
        for value in values:
            if value in members:
                removed += 1
            members.discard(value)
        return removed

    async def smembers(self, key):
        return set(self.set_members.get(key, set()))


@pytest.mark.asyncio
async def test_control_store_round_trips_limit_rule_switch_and_announcement():
    store = ControlPlaneStore(FakeRedis())

    await store.put_limit_rule(
        LimitRuleRecord(
            rule_id="user-42-cap",
            scope_type="user",
            scope_id="42",
            limit_type="max_notional",
            limit_value=800.0,
            enabled=True,
            priority=100,
        )
    )
    await store.put_switch(
        PlatformSwitchRecord(
            switch_key="platform.reduce_only",
            scope_type="platform",
            scope_id="global",
            enabled=True,
        )
    )
    await store.put_announcement(
        AnnouncementRecord(
            announcement_id="maint-1",
            title="maintenance",
            content="tonight",
            priority=100,
            is_pinned=False,
            audience_type="all",
            audience_filter={},
            channels=["site"],
            status="active",
        )
    )

    rules = await store.list_limit_rules()
    switches = await store.list_switches()
    announcements = await store.list_announcements()

    assert rules[0].rule_id == "user-42-cap"
    assert switches[0].switch_key == "platform.reduce_only"
    assert announcements[0].announcement_id == "maint-1"
    assert rules[0].updated_at is not None
    assert switches[0].updated_at is not None
    assert announcements[0].updated_at is not None


@pytest.mark.asyncio
async def test_control_store_deletes_records_and_updates_indexes():
    store = ControlPlaneStore(FakeRedis())
    await store.put_limit_rule(
        LimitRuleRecord(
            rule_id="user-42-cap",
            scope_type="user",
            scope_id="42",
            limit_type="max_notional",
            limit_value=800.0,
            enabled=True,
            priority=100,
        )
    )
    await store.put_switch(
        PlatformSwitchRecord(
            switch_key="platform.reduce_only",
            scope_type="platform",
            scope_id="global",
            enabled=True,
        )
    )
    await store.put_announcement(
        AnnouncementRecord(
            announcement_id="maint-1",
            title="maintenance",
            content="tonight",
            priority=100,
            is_pinned=False,
            audience_type="all",
            audience_filter={},
            channels=["site"],
            status="active",
        )
    )

    await store.delete_limit_rule("user-42-cap")
    await store.delete_switch("platform.reduce_only", scope_type="platform", scope_id="global")
    await store.delete_announcement("maint-1")

    assert await store.list_limit_rules() == []
    assert await store.list_switches() == []
    assert await store.list_announcements() == []
