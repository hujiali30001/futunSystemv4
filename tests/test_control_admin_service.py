import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.admin.control_store import ControlPlaneStore, LimitRuleRecord
from app.runtime.control_admin_service import build_control_admin_app


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
        self.values.pop(key, None)
        return 1

    async def sadd(self, key, *values):
        self.set_members.setdefault(key, set()).update(values)
        return len(values)

    async def srem(self, key, *values):
        members = self.set_members.setdefault(key, set())
        for value in values:
            members.discard(value)
        return len(values)

    async def smembers(self, key):
        return set(self.set_members.get(key, set()))


async def make_client(*, store: ControlPlaneStore | None = None) -> TestClient:
    app = build_control_admin_app(
        store=store or ControlPlaneStore(FakeRedis()),
        admin_token="secret",
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_control_admin_healthz_is_public():
    client = await make_client()
    try:
        response = await client.get("/healthz")

        assert response.status == 200
        assert await response.json() == {"ok": True}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_control_admin_lists_limits_with_valid_token():
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
    client = await make_client(store=store)
    try:
        response = await client.get(
            "/control/limits",
            headers={"Authorization": "Bearer secret"},
        )

        assert response.status == 200
        assert (await response.json())["limits"][0]["rule_id"] == "user-42-cap"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_control_admin_rejects_missing_token():
    client = await make_client()
    try:
        response = await client.get("/control/limits")

        assert response.status == 401
        assert await response.json() == {"error": "unauthorized"}
    finally:
        await client.close()
