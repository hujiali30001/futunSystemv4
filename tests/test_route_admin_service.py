import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.runtime.route_admin_service import build_route_admin_app


class FakeRouteStore:
    def __init__(self):
        self.routes = {"42": "node-a"}

    async def list_routes(self):
        return dict(self.routes)

    async def get_user_node(self, user_id: str):
        return self.routes.get(user_id)

    async def set_user_node(self, user_id: str, node_id: str):
        self.routes[user_id] = node_id
        return True

    async def delete_user_node(self, user_id: str):
        self.routes.pop(user_id, None)
        return 1


class FakeEventRouter:
    def __init__(self):
        self.events = []

    async def dispatch(self, event):
        self.events.append(event)


async def make_client(
    *,
    route_store: FakeRouteStore | None = None,
    event_router: FakeEventRouter | None = None,
) -> TestClient:
    app = build_route_admin_app(
        route_store=route_store or FakeRouteStore(),
        admin_token="secret",
        event_router=event_router,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_route_admin_healthz_is_public():
    client = await make_client()
    try:
        response = await client.get("/healthz")

        assert response.status == 200
        assert await response.json() == {"ok": True}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_route_admin_lists_routes_with_valid_token():
    client = await make_client()
    try:
        response = await client.get(
            "/routes",
            headers={"Authorization": "Bearer secret"},
        )

        assert response.status == 200
        assert await response.json() == {"routes": {"42": "node-a"}}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_route_admin_rejects_missing_token():
    event_router = FakeEventRouter()
    client = await make_client(event_router=event_router)
    try:
        response = await client.get("/routes")

        assert response.status == 401
        assert event_router.events[0].event_type == "route.admin.unauthorized"
        assert event_router.events[0].payload["path"] == "/routes"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_route_admin_gets_single_route_and_handles_not_found():
    client = await make_client()
    try:
        found = await client.get(
            "/routes/42",
            headers={"Authorization": "Bearer secret"},
        )
        missing = await client.get(
            "/routes/99",
            headers={"Authorization": "Bearer secret"},
        )

        assert found.status == 200
        assert await found.json() == {"user_id": "42", "node_id": "node-a"}
        assert missing.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_route_admin_put_updates_single_route():
    event_router = FakeEventRouter()
    client = await make_client(event_router=event_router)
    try:
        response = await client.put(
            "/routes/99",
            headers={"Authorization": "Bearer secret"},
            json={"node_id": "main"},
        )

        assert response.status == 200
        assert await response.json() == {
            "ok": True,
            "user_id": "99",
            "node_id": "main",
        }
        assert event_router.events[0].event_type == "route.admin.updated"
        assert event_router.events[0].payload == {
            "user_id": "99",
            "node_id": "main",
            "source": "http_api",
        }
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_route_admin_delete_is_idempotent():
    event_router = FakeEventRouter()
    client = await make_client(event_router=event_router)
    try:
        response = await client.delete(
            "/routes/42",
            headers={"Authorization": "Bearer secret"},
        )

        assert response.status == 200
        assert await response.json() == {"ok": True, "user_id": "42"}
        assert event_router.events[0].event_type == "route.admin.deleted"
        assert event_router.events[0].payload == {
            "user_id": "42",
            "node_id": None,
            "source": "http_api",
        }
    finally:
        await client.close()
