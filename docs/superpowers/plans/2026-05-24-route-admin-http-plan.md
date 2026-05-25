# Route Admin HTTP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为主服务器增加一个轻量 HTTP 路由管理接口，支持在线查看、设置、删除用户到执行节点的路由，并保证 `USER_NODE_ROUTES` 只作为冷启动默认值。

**Architecture:** 保持 Redis `route:user_node:{user_id}` 作为运行时真值，新增 `route:user_node:index` 作为路由索引集合。实现一个独立的 `route_admin_service` 进程，仅运行在主服务器，使用 Bearer Token 鉴权，通过 HTTP 读写 Redis 路由；同时把 `dispatcher` 启动时的默认路由同步改为“仅补缺失、不覆盖”。

**Tech Stack:** Python 3.10+, asyncio, redis.asyncio, aiohttp, pydantic-settings, pytest, pytest-asyncio, systemd

---

## 文件结构与职责

- `requirements.txt`
  - 显式声明 `aiohttp` 依赖
- `app/runtime/worker_config.py`
  - 增加 route-admin 服务配置项
- `app/runtime/redis_flow.py`
  - 扩展 `UserNodeRouteStore`，支持索引集合、列出全部路由、删除路由、仅补缺失默认值
- `app/runtime/worker_service.py`
  - 把 `dispatcher` 启动时的默认路由同步改为“只在 Redis 缺失时补默认值”
- `app/runtime/route_admin_service.py`
  - 新增独立 HTTP 服务、鉴权、中间件、路由处理、结构化事件
- `app/runtime/systemd_assets.py`
  - 增加 route-admin unit 渲染与环境示例字段
- `deploy/systemd/furun-route-admin.service`
  - 主服务器 route-admin 服务 unit
- `deploy/systemd/.env.worker.example`
  - 增加 `ROUTE_ADMIN_*` 配置
- `docs/ops/live-workers-systemd.md`
  - 增加 route-admin 的启停、SSH 隧道访问、curl 示例
- `tests/test_redis_opportunity_flow.py`
  - 覆盖路由索引、列出、删除、默认值补全语义
- `tests/test_worker_service.py`
  - 覆盖 dispatcher 默认路由“只补缺失”的新语义
- `tests/test_route_admin_service.py`
  - 覆盖 HTTP 接口、鉴权、状态码、Redis 异常
- `tests/test_systemd_assets.py`
  - 覆盖 route-admin unit 和新环境变量

### Task 1: 收敛 Redis 路由存储语义

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Test: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- Test: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: 先写失败测试，锁定“索引集合 + 默认值不覆盖”的新行为**

```python
import pytest

from app.runtime.redis_flow import UserNodeRouteStore


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.set_calls = []
        self.deleted = []
        self.set_members = set()

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value
        self.set_calls.append((key, value))
        return True

    async def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)
        return 1

    async def sadd(self, key, *values):
        self.set_members.update(values)
        return len(values)

    async def srem(self, key, *values):
        for value in values:
            self.set_members.discard(value)
        return len(values)

    async def smembers(self, key):
        return set(self.set_members)


@pytest.mark.asyncio
async def test_route_store_lists_routes_from_index_and_single_keys():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)

    await store.set_user_node("42", "node-a")
    await store.set_user_node("99", "main")

    routes = await store.list_routes()

    assert routes == {"42": "node-a", "99": "main"}


@pytest.mark.asyncio
async def test_route_store_delete_removes_single_key_and_index_member():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)

    await store.set_user_node("42", "node-a")
    await store.delete_user_node("42")

    assert await store.get_user_node("42") is None
    assert "42" not in redis_client.set_members


@pytest.mark.asyncio
async def test_route_store_sync_defaults_only_fills_missing_routes():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)
    await store.set_user_node("42", "node-a")

    synced = await store.sync_default_routes({"42": "main", "99": "node-b"})

    assert synced == 1
    assert await store.get_user_node("42") == "node-a"
    assert await store.get_user_node("99") == "node-b"
```

```python
@pytest.mark.asyncio
async def test_worker_app_syncs_default_routes_without_overwriting_existing_redis(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    redis_client.values["route:user_node:42"] = "node-a"
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="dispatcher",
            spot_exchanges=["okx", "bitget"],
            dispatch_user_ids=["42", "99"],
            user_node_routes={"42": "main", "99": "node-b"},
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert redis_client.values["route:user_node:42"] == "node-a"
    assert redis_client.values["route:user_node:99"] == "node-b"
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `pytest tests/test_redis_opportunity_flow.py tests/test_worker_service.py -v`
Expected: FAIL，提示 `list_routes()`、`delete_user_node()`、`sync_default_routes()` 不存在，或 `WorkerApp` 仍在覆盖已有 Redis 路由

- [ ] **Step 3: 实现最小 Redis 路由存储与默认值同步逻辑**

```python
class UserNodeRouteStore:
    ROUTE_INDEX_KEY = "route:user_node:index"

    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    @staticmethod
    def route_key(user_id: str) -> str:
        return f"route:user_node:{user_id}"

    async def get_user_node(self, user_id: str) -> str | None:
        return await self.redis_client.get(self.route_key(user_id))

    async def set_user_node(self, user_id: str, node_id: str) -> bool:
        await self.redis_client.set(self.route_key(user_id), node_id)
        await self.redis_client.sadd(self.ROUTE_INDEX_KEY, user_id)
        return True

    async def delete_user_node(self, user_id: str) -> int:
        await self.redis_client.srem(self.ROUTE_INDEX_KEY, user_id)
        return await self.redis_client.delete(self.route_key(user_id))

    async def list_routes(self) -> dict[str, str]:
        routes: dict[str, str] = {}
        for user_id in sorted(await self.redis_client.smembers(self.ROUTE_INDEX_KEY)):
            node_id = await self.get_user_node(str(user_id))
            if node_id is not None:
                routes[str(user_id)] = node_id
        return routes

    async def sync_default_routes(self, routes: dict[str, str]) -> int:
        synced = 0
        for user_id, node_id in routes.items():
            if await self.get_user_node(user_id) is None:
                await self.set_user_node(user_id, node_id)
                synced += 1
        return synced
```

```python
if self.settings.worker_role == "dispatcher" and self.settings.user_node_routes:
    route_store = UserNodeRouteStore(redis_client)
    await route_store.sync_default_routes(self.settings.user_node_routes)
```

- [ ] **Step 4: 重新运行定向测试**

Run: `pytest tests/test_redis_opportunity_flow.py tests/test_worker_service.py -v`
Expected: PASS，Redis 路由索引、删除和默认值补全语义全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/redis_flow.py app/runtime/worker_service.py tests/test_redis_opportunity_flow.py tests/test_worker_service.py
git commit -m "feat: add indexed route store and default route sync"
```

### Task 2: 新增 route-admin HTTP 服务

**Files:**
- Modify: `d:\old\FuRunSystemV4\requirements.txt`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_config.py`
- Create: `d:\old\FuRunSystemV4\app\runtime\route_admin_service.py`
- Test: `d:\old\FuRunSystemV4\tests\test_route_admin_service.py`

- [ ] **Step 1: 先写失败测试，锁定 HTTP 接口与鉴权行为**

```python
import pytest
from aiohttp import web
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


@pytest.mark.asyncio
async def test_route_admin_lists_routes_with_valid_token():
    app = build_route_admin_app(route_store=FakeRouteStore(), admin_token="secret")
    client = TestClient(TestServer(app))
    await client.start_server()

    response = await client.get(
        "/routes",
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status == 200
    assert await response.json() == {"routes": {"42": "node-a"}}
    await client.close()


@pytest.mark.asyncio
async def test_route_admin_rejects_missing_token():
    app = build_route_admin_app(route_store=FakeRouteStore(), admin_token="secret")
    client = TestClient(TestServer(app))
    await client.start_server()

    response = await client.get("/routes")

    assert response.status == 401
    await client.close()


@pytest.mark.asyncio
async def test_route_admin_put_updates_single_route():
    app = build_route_admin_app(route_store=FakeRouteStore(), admin_token="secret")
    client = TestClient(TestServer(app))
    await client.start_server()

    response = await client.put(
        "/routes/99",
        headers={"Authorization": "Bearer secret"},
        json={"node_id": "main"},
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "user_id": "99", "node_id": "main"}
    await client.close()
```

- [ ] **Step 2: 运行 route-admin 定向测试并确认失败**

Run: `pytest tests/test_route_admin_service.py -v`
Expected: FAIL，提示 `route_admin_service.py` 或 `build_route_admin_app()` 尚不存在

- [ ] **Step 3: 实现最小 route-admin 服务与配置项**

```python
class RouteAdminSettings(BaseSettings):
    route_admin_enabled: bool = False
    route_admin_bind_host: str = "127.0.0.1"
    route_admin_port: int = 8787
    route_admin_token: str = ""
```

```python
def _check_bearer(request: web.Request, token: str) -> bool:
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {token}"


def build_route_admin_app(*, route_store, admin_token: str) -> web.Application:
    app = web.Application()

    async def healthz(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def list_routes(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"routes": await route_store.list_routes()})

    async def get_route(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            return web.json_response({"error": "unauthorized"}, status=401)
        user_id = request.match_info["user_id"]
        node_id = await route_store.get_user_node(user_id)
        if node_id is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"user_id": user_id, "node_id": node_id})

    async def put_route(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            return web.json_response({"error": "unauthorized"}, status=401)
        user_id = request.match_info["user_id"]
        body = await request.json()
        node_id = str(body["node_id"]).strip()
        if not node_id:
            return web.json_response({"error": "node_id required"}, status=400)
        await route_store.set_user_node(user_id, node_id)
        return web.json_response({"ok": True, "user_id": user_id, "node_id": node_id})

    async def delete_route(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            return web.json_response({"error": "unauthorized"}, status=401)
        user_id = request.match_info["user_id"]
        await route_store.delete_user_node(user_id)
        return web.json_response({"ok": True, "user_id": user_id})

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/routes", list_routes)
    app.router.add_get("/routes/{user_id}", get_route)
    app.router.add_put("/routes/{user_id}", put_route)
    app.router.add_delete("/routes/{user_id}", delete_route)
    return app
```

```text
aiohttp>=3.9
```

- [ ] **Step 4: 重新运行 route-admin 定向测试**

Run: `pytest tests/test_route_admin_service.py -v`
Expected: PASS，健康检查、鉴权、列表、单条读写、删除接口全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add requirements.txt app/runtime/worker_config.py app/runtime/route_admin_service.py tests/test_route_admin_service.py
git commit -m "feat: add route admin http service"
```

### Task 3: 补结构化事件、systemd 与运维文档

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\route_admin_service.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\systemd_assets.py`
- Create: `d:\old\FuRunSystemV4\deploy\systemd\furun-route-admin.service`
- Modify: `d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
- Test: `d:\old\FuRunSystemV4\tests\test_route_admin_service.py`
- Test: `d:\old\FuRunSystemV4\tests\test_systemd_assets.py`

- [ ] **Step 1: 先写失败测试，锁定事件与部署资产**

```python
@pytest.mark.asyncio
async def test_route_admin_emits_runtime_event_on_update():
    events = []

    class FakeEventRouter:
        async def dispatch(self, event):
            events.append(event)

    app = build_route_admin_app(
        route_store=FakeRouteStore(),
        admin_token="secret",
        event_router=FakeEventRouter(),
    )
    client = TestClient(TestServer(app))
    await client.start_server()

    response = await client.put(
        "/routes/42",
        headers={"Authorization": "Bearer secret"},
        json={"node_id": "main"},
    )

    assert response.status == 200
    assert events[0].event_type == "route.admin.updated"
    assert events[0].payload["user_id"] == "42"
    assert events[0].payload["source"] == "http_api"
    await client.close()
```

```python
def test_render_systemd_unit_contains_route_admin_execstart():
    content = render_systemd_unit(role="route-admin")

    assert "Description=FuRun route admin service" in content
    assert (
        "ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python "
        "-m app.runtime.route_admin_service"
    ) in content


def test_render_worker_env_example_contains_route_admin_fields():
    content = render_worker_env_example()

    assert "ROUTE_ADMIN_ENABLED=0" in content
    assert "ROUTE_ADMIN_BIND_HOST=127.0.0.1" in content
    assert "ROUTE_ADMIN_PORT=8787" in content
    assert "ROUTE_ADMIN_TOKEN=" in content
```

- [ ] **Step 2: 运行文档与部署相关测试并确认失败**

Run: `pytest tests/test_route_admin_service.py tests/test_systemd_assets.py -v`
Expected: FAIL，提示 route-admin 事件未发出，或缺少新的 unit 与环境字段

- [ ] **Step 3: 最小化实现事件、unit 与文档**

```python
if event_router is not None:
    await event_router.dispatch(
        RuntimeEvent(
            event_type="route.admin.updated",
            level="INFO",
            service="route-admin",
            region="main",
            message="route updated",
            payload={"user_id": user_id, "node_id": node_id, "source": "http_api"},
        )
    )
```

```ini
[Unit]
Description=FuRun route admin service
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.route_admin_service
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```dotenv
ROUTE_ADMIN_ENABLED=0
ROUTE_ADMIN_BIND_HOST=127.0.0.1
ROUTE_ADMIN_PORT=8787
ROUTE_ADMIN_TOKEN=
```

- [ ] **Step 4: 重新运行相关测试**

Run: `pytest tests/test_route_admin_service.py tests/test_systemd_assets.py -v`
Expected: PASS，route-admin 事件、systemd unit 和环境字段全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/route_admin_service.py app/runtime/systemd_assets.py deploy/systemd/furun-route-admin.service deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md tests/test_route_admin_service.py tests/test_systemd_assets.py
git commit -m "docs: add route admin deployment assets"
```

### Task 4: 总回归与主服务器本地验证

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
- Test: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- Test: `d:\old\FuRunSystemV4\tests\test_worker_service.py`
- Test: `d:\old\FuRunSystemV4\tests\test_route_admin_service.py`
- Test: `d:\old\FuRunSystemV4\tests\test_systemd_assets.py`

- [ ] **Step 1: 运行本地总回归**

Run: `pytest tests/test_redis_opportunity_flow.py tests/test_worker_service.py tests/test_route_admin_service.py tests/test_systemd_assets.py -v`
Expected: PASS，默认路由补全、HTTP 接口、事件与部署资产全部通过

- [ ] **Step 2: 在主服务器启用 route-admin，并只监听本机**

Run:

```bash
cd /home/ubuntu/furunsystemv4/current
grep -E '^(ROUTE_ADMIN_ENABLED|ROUTE_ADMIN_BIND_HOST|ROUTE_ADMIN_PORT|ROUTE_ADMIN_TOKEN)=' .env.worker
sudo cp deploy/systemd/furun-route-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable furun-route-admin.service
sudo systemctl restart furun-route-admin.service
sudo systemctl is-active furun-route-admin.service
```

Expected: `active`

- [ ] **Step 3: 通过本机 curl 验证接口**

Run:

```bash
curl -s http://127.0.0.1:8787/healthz
curl -s -H "Authorization: Bearer $ROUTE_ADMIN_TOKEN" http://127.0.0.1:8787/routes
curl -s -X PUT -H "Authorization: Bearer $ROUTE_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"node_id":"node-a"}' http://127.0.0.1:8787/routes/42
curl -s -H "Authorization: Bearer $ROUTE_ADMIN_TOKEN" http://127.0.0.1:8787/routes/42
```

Expected:
- `/healthz` 返回 `{"ok": true}`
- `/routes` 返回路由字典
- `PUT /routes/42` 返回 `{"ok": true, "user_id": "42", "node_id": "node-a"}`
- 再查单条路由返回更新后的 `node_id`

- [ ] **Step 4: 验证重启 dispatcher 后动态路由不被覆盖**

Run:

```bash
redis-cli GET route:user_node:42
sudo systemctl restart furun-spot-dispatcher.service
sleep 3
redis-cli GET route:user_node:42
sudo journalctl -u furun-route-admin.service -n 30 --no-pager | grep 'route.admin'
```

Expected:
- 重启前后 `route:user_node:42` 保持一致
- `journalctl` 中可见 `route.admin.updated` 或 `route.admin.sync_default_applied`

- [ ] **Step 5: 完成最终提交**

```bash
git add requirements.txt app/runtime/worker_config.py app/runtime/redis_flow.py app/runtime/worker_service.py app/runtime/route_admin_service.py app/runtime/systemd_assets.py deploy/systemd/furun-route-admin.service deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md tests/test_redis_opportunity_flow.py tests/test_worker_service.py tests/test_route_admin_service.py tests/test_systemd_assets.py
git commit -m "feat: add route admin http interface"
```

## 自检结果

- Spec coverage:
  - Redis 真值与默认值语义：`Task 1`
  - 独立 route-admin HTTP 服务：`Task 2`
  - 鉴权、结构化事件、systemd、文档：`Task 3`
  - 本地回归与主服务器验证：`Task 4`
- Placeholder scan:
  - 未保留 `TODO`、`TBD`、"后续补"、"类似上一步" 等占位语句
- Type consistency:
  - 统一使用 `USER_NODE_ROUTES`、`route:user_node:{user_id}`、`route:user_node:index`
  - route-admin 配置统一使用 `ROUTE_ADMIN_ENABLED`、`ROUTE_ADMIN_BIND_HOST`、`ROUTE_ADMIN_PORT`、`ROUTE_ADMIN_TOKEN`
