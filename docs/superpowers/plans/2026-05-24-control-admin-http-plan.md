# Control Admin HTTP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为当前主服务器 `scanner + dispatcher` 与执行节点 `executor` 链路增加一个最小可部署的管理控制面，支持管理员在线维护额度规则、平台开关与公告，并让 `dispatcher` / `executor` 双层执行同一套控制规则。

**Architecture:** 新增独立 `control-admin` HTTP 服务，使用 Redis 作为控制面运行时真值，复用现有 `route-admin` 的 Bearer Token、`aiohttp.web` 和 `systemd` 风格。控制面数据通过 Redis Store 统一读写，`dispatcher` 在分发用户级节点任务前先做首次控制校验，`executor` 在真正执行前再次校验，并把缩量后的 `target_quote_amount` 继续传给执行探针。

**Tech Stack:** Python 3.10+, asyncio, redis.asyncio, aiohttp, pydantic-settings, pytest, pytest-asyncio, systemd

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\app\admin\control_plane.py`
  - 继续承载纯规则求值逻辑
  - 增加从 Redis 记录对象构建 `ControlPlane` 的适配函数
- `d:\old\FuRunSystemV4\app\admin\control_store.py`
  - 新增 Redis 控制面存储层
  - 负责额度规则、平台开关、公告的读写、删除、列举
- `d:\old\FuRunSystemV4\app\runtime\control_admin_service.py`
  - 新增独立 HTTP 管理服务
  - 负责鉴权、请求校验、JSON 响应、结构化事件
- `d:\old\FuRunSystemV4\app\runtime\worker_config.py`
  - 增加 `CONTROL_ADMIN_*` 配置项
- `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
  - 继续负责机会流与节点任务流
  - 让任务分发把缩量后的 `target_quote_amount` 继续带到执行层
  - 让机会分发器把 `target_quote_amount` 传递给执行探针
- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - 给 `RedisNodeTaskDispatcher` 增加控制面校验
  - 给 `RedisExecutionTaskConsumer` 增加执行前控制面校验
- `d:\old\FuRunSystemV4\app\runtime\spot_arbitrage_probe.py`
  - 支持接收 `target_quote_amount`
  - 用缩量后的金额计算更小下单数量
- `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
  - 装配 Redis Control Store 与控制面依赖
- `d:\old\FuRunSystemV4\app\runtime\systemd_assets.py`
  - 增加 `control-admin` unit 渲染和环境变量样例
- `d:\old\FuRunSystemV4\deploy\systemd\furun-control-admin.service`
  - 新增控制面服务 unit
- `d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example`
  - 增加 `CONTROL_ADMIN_*` 样例配置
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - 增加 `control-admin` 的部署、启动和 `curl` 示例
- `d:\old\FuRunSystemV4\tests\test_control_plane.py`
  - 扩展规则装配与求值测试
- `d:\old\FuRunSystemV4\tests\test_control_store.py`
  - 新增 Redis 控制面存储测试
- `d:\old\FuRunSystemV4\tests\test_control_admin_service.py`
  - 新增控制面 HTTP 接口测试
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 覆盖 `dispatcher` / `executor` 双层拦截与缩量
- `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
  - 覆盖 `target_quote_amount` 在任务流中的继续传递
- `d:\old\FuRunSystemV4\tests\test_spot_arbitrage_probe.py`
  - 覆盖执行探针使用缩量后金额下单
- `d:\old\FuRunSystemV4\tests\test_worker_config.py`
  - 覆盖 `CONTROL_ADMIN_*` 配置解析
- `d:\old\FuRunSystemV4\tests\test_systemd_assets.py`
  - 覆盖 `control-admin` unit 与环境变量样例

### Task 1: 新增 Redis 控制面存储层

**Files:**
- Create: `d:\old\FuRunSystemV4\app\admin\control_store.py`
- Modify: `d:\old\FuRunSystemV4\app\admin\control_plane.py`
- Create: `d:\old\FuRunSystemV4\tests\test_control_store.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_control_plane.py`

- [ ] **Step 1: 先写失败测试，锁定控制面对象的存取和装配语义**

```python
import pytest

from app.admin.control_plane import ControlPlane, build_control_plane
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
            title="维护通知",
            content="今晚演练",
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


def test_build_control_plane_converts_store_records_into_runtime_rules():
    plane = build_control_plane(
        limit_rules=[
            LimitRuleRecord(
                rule_id="strategy-7-cap",
                scope_type="strategy",
                scope_id="7",
                limit_type="max_notional",
                limit_value=500.0,
                enabled=True,
                priority=100,
            )
        ],
        switches=[
            PlatformSwitchRecord(
                switch_key="platform.reduce_only",
                scope_type="platform",
                scope_id="global",
                enabled=False,
            )
        ],
    )

    decision = plane.evaluate_open_request(
        user_id=42,
        strategy_id=7,
        symbol="BTC/USDT",
        exchange="okx",
        requested_notional=1000.0,
    )

    assert isinstance(plane, ControlPlane)
    assert decision.allowed is True
    assert decision.approved_notional == 500.0
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_control_store.py tests/test_control_plane.py -v`
Expected: FAIL，提示 `app.admin.control_store` 不存在，或 `build_control_plane()` 未定义

- [ ] **Step 3: 实现最小 Redis Store 与规则装配函数**

```python
from dataclasses import asdict, dataclass
import json
from datetime import datetime, UTC


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
    audience_filter: dict
    channels: list[str]
    status: str
    updated_at: str | None = None


class ControlPlaneStore:
    LIMIT_INDEX_KEY = "control:limits:index"
    SWITCH_INDEX_KEY = "control:switches:index"
    ANNOUNCEMENT_INDEX_KEY = "control:announcements:index"

    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    async def put_limit_rule(self, record: LimitRuleRecord) -> None:
        payload = asdict(record)
        payload["updated_at"] = payload["updated_at"] or datetime.now(UTC).isoformat()
        await self.redis_client.set(f"control:limits:{record.rule_id}", json.dumps(payload))
        await self.redis_client.sadd(self.LIMIT_INDEX_KEY, record.rule_id)

    async def list_limit_rules(self) -> list[LimitRuleRecord]:
        results = []
        for rule_id in sorted(await self.redis_client.smembers(self.LIMIT_INDEX_KEY)):
            raw = await self.redis_client.get(f"control:limits:{rule_id}")
            if raw:
                results.append(LimitRuleRecord(**json.loads(raw)))
        return results
```

```python
def build_control_plane(
    *,
    limit_rules: list[LimitRuleRecord],
    switches: list[PlatformSwitchRecord],
) -> ControlPlane:
    return ControlPlane(
        switches=[
            PlatformSwitch(
                key=record.switch_key,
                enabled=record.enabled,
                scope=record.scope_type,
                scope_id=record.scope_id,
            )
            for record in switches
            if record.enabled
        ],
        limit_rules=[
            LimitRule(
                scope=record.scope_type,
                scope_id=record.scope_id,
                limit_value=record.limit_value,
                symbol=record.symbol,
                exchange=record.exchange,
                strategy_id=record.strategy_id,
            )
            for record in limit_rules
            if record.enabled and record.limit_type == "max_notional"
        ],
    )
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_control_store.py tests/test_control_plane.py -v`
Expected: PASS，控制面对象能写入、列举、删除，并能装配成运行时 `ControlPlane`

- [ ] **Step 5: 提交这一小步**

```bash
git add app/admin/control_store.py app/admin/control_plane.py tests/test_control_store.py tests/test_control_plane.py
git commit -m "feat: add redis-backed control plane store"
```

### Task 2: 打通缩量字段到执行探针

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\spot_arbitrage_probe.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_spot_arbitrage_probe.py`

- [ ] **Step 1: 先写失败测试，锁定 `target_quote_amount` 贯通行为**

```python
@pytest.mark.asyncio
async def test_dispatcher_forwards_target_quote_amount_into_probe_service():
    service = FakeSpotService()
    dispatcher = RedisOpportunityDispatcher(service)

    await dispatcher.dispatch(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
            "target_quote_amount": "55.5",
        },
        credentials_by_exchange={"bitget": object(), "gate": object()},
    )

    assert service.calls[0]["target_quote_amount"] == 55.5
```

```python
@pytest.mark.asyncio
async def test_spot_arbitrage_probe_uses_target_quote_amount_to_reduce_order_size():
    factory = FakeFactory()
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        target_quote_amount=5.0,
        env_mode="testnet",
    )

    assert result.ok is True
    buy_client = factory.created_clients["bitget"][0]
    sell_client = factory.created_clients["gate"][0]
    assert float(buy_client.last_create_order["amount"]) < 0.050
    assert float(sell_client.last_create_order["amount"]) < 0.050
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_redis_opportunity_flow.py tests/test_spot_arbitrage_probe.py -v`
Expected: FAIL，`RedisOpportunityDispatcher` 没有传 `target_quote_amount`，或 `SpotArbitrageProbeService.run_task()` 还不接受这个参数

- [ ] **Step 3: 最小实现字段传递和缩量下单**

```python
class RedisOpportunityDispatcher:
    async def dispatch(self, payload: dict, *, credentials_by_exchange: dict) -> object:
        exchanges = [payload["buy_exchange"], payload["sell_exchange"]]
        scoped_credentials = {
            exchange: credentials_by_exchange[exchange]
            for exchange in exchanges
        }
        target_quote_amount = float(payload.get("target_quote_amount", 15.0))
        return await self.spot_service.run_task(
            exchanges=exchanges,
            credentials_by_exchange=scoped_credentials,
            symbol=payload["symbol"],
            target_quote_amount=target_quote_amount,
            env_mode="testnet",
        )
```

```python
class SpotArbitrageProbeService:
    async def run_task(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict[str, ExchangeCredentials],
        symbol: str,
        target_quote_amount: float = 15.0,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> SpotArbitrageTaskResult:
        ...
        buy_amount = adapters[buy_exchange].amount_to_precision(
            symbol,
            self._build_safe_amount(
                buy_market,
                tickers[buy_exchange],
                target_quote_amount=target_quote_amount,
            ),
        )
        sell_amount = adapters[sell_exchange].amount_to_precision(
            symbol,
            self._build_safe_amount(
                sell_market,
                tickers[sell_exchange],
                target_quote_amount=target_quote_amount,
            ),
        )

    @staticmethod
    def _build_safe_amount(
        market: dict,
        ticker: dict,
        *,
        target_quote_amount: float,
    ) -> float:
        min_amount = market.get("limits", {}).get("amount", {}).get("min") or 0.0001
        reference_price = ticker.get("bid") or ticker.get("last") or ticker.get("ask") or 1.0
        requested_amount = float(target_quote_amount) / float(reference_price)
        return max(float(min_amount), requested_amount)
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_redis_opportunity_flow.py tests/test_spot_arbitrage_probe.py -v`
Expected: PASS，缩量后的 `target_quote_amount` 能一路传到执行探针并影响下单数量

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/redis_flow.py app/runtime/spot_arbitrage_probe.py tests/test_redis_opportunity_flow.py tests/test_spot_arbitrage_probe.py
git commit -m "feat: forward target quote amount into execution probe"
```

### Task 3: 新增 control-admin HTTP 服务

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_config.py`
- Create: `d:\old\FuRunSystemV4\app\runtime\control_admin_service.py`
- Create: `d:\old\FuRunSystemV4\tests\test_control_admin_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_config.py`

- [ ] **Step 1: 先写失败测试，锁定 HTTP 接口与配置解析**

```python
import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.admin.control_store import ControlPlaneStore
from app.runtime.control_admin_service import build_control_admin_app
from app.runtime.worker_config import WorkerSettings


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
    app = build_control_admin_app(store=store, admin_token="secret")
    client = TestClient(TestServer(app))
    await client.start_server()

    response = await client.get(
        "/control/limits",
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status == 200
    assert (await response.json())["limits"][0]["rule_id"] == "user-42-cap"


def test_worker_settings_parse_control_admin_fields():
    settings = WorkerSettings(
        control_admin_enabled=True,
        control_admin_bind_host="127.0.0.1",
        control_admin_port=8790,
        control_admin_token="top-secret",
    )

    assert settings.control_admin_enabled is True
    assert settings.control_admin_port == 8790
    assert settings.control_admin_token == "top-secret"
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_control_admin_service.py tests/test_worker_config.py -v`
Expected: FAIL，`control_admin_service.py` 和 `CONTROL_ADMIN_*` 配置项尚不存在

- [ ] **Step 3: 实现最小 HTTP 服务与配置**

```python
class WorkerSettings(BaseSettings):
    ...
    control_admin_enabled: bool = False
    control_admin_bind_host: str = "127.0.0.1"
    control_admin_port: int = 8788
    control_admin_token: str = ""
```

```python
def build_control_admin_app(*, store, admin_token: str, event_router=None) -> web.Application:
    app = web.Application()

    async def healthz(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def list_limits(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        limits = [asdict(rule) for rule in await store.list_limit_rules()]
        return web.json_response({"limits": limits})

    async def put_limit(request: web.Request) -> web.Response:
        ...

    async def list_switches(request: web.Request) -> web.Response:
        ...

    async def create_announcement(request: web.Request) -> web.Response:
        ...

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/control/limits", list_limits)
    app.router.add_put("/control/limits/{rule_id}", put_limit)
    app.router.add_get("/control/switches", list_switches)
    app.router.add_put("/control/switches/{switch_id}", put_switch)
    app.router.add_get("/announcements", list_announcements)
    app.router.add_post("/announcements", create_announcement)
    return app
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_control_admin_service.py tests/test_worker_config.py -v`
Expected: PASS，接口鉴权、对象查询和配置解析通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/worker_config.py app/runtime/control_admin_service.py tests/test_control_admin_service.py tests/test_worker_config.py
git commit -m "feat: add control admin http service"
```

### Task 4: 把控制规则接入 dispatcher 与 executor

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定双层阻断和缩量**

```python
@pytest.mark.asyncio
async def test_dispatcher_blocks_when_platform_reduce_only_is_active():
    redis_client = FakeRedis()
    redis_client.route_values = {"route:user_node:42": "node-a"}
    guard = FakeControlGuard(
        allowed=False,
        approved_notional=0.0,
        reason="reduce_only",
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        control_guard=guard,
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert redis_client.xadds == []
```

```python
@pytest.mark.asyncio
async def test_dispatcher_resizes_target_quote_amount_before_publishing_task():
    redis_client = FakeRedis()
    redis_client.route_values = {"route:user_node:42": "node-a"}
    guard = FakeControlGuard(
        allowed=True,
        approved_notional=35.0,
        reason=None,
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        control_guard=guard,
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert redis_client.xadds[0][1]["target_quote_amount"] == "35.0"
```

```python
@pytest.mark.asyncio
async def test_executor_blocks_even_if_dispatcher_already_published_task():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_exec_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "user_id": "42",
                            "symbol": "BTC/USDT",
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    service = FakeSpotService()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        control_guard=FakeControlGuard(
            allowed=False,
            approved_notional=0.0,
            reason="user.disable_open",
        ),
        block_ms=1,
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert service.calls == []
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -v`
Expected: FAIL，`RedisNodeTaskDispatcher` 和 `RedisExecutionTaskConsumer` 还不接受 `control_guard`

- [ ] **Step 3: 实现双层控制接入**

```python
@dataclass(slots=True)
class ControlGuard:
    control_plane_loader: Any
    event_router: Any | None = None
    service_name: str = "dispatcher"
    region: str = "default"

    async def evaluate(self, *, user_id: str, symbol: str, exchange: str, requested_notional: float, strategy_id: int | None = None):
        plane = await self.control_plane_loader.load()
        decision = plane.evaluate_open_request(
            user_id=int(user_id),
            strategy_id=strategy_id,
            symbol=symbol,
            exchange=exchange,
            requested_notional=requested_notional,
        )
        return decision
```

```python
class RedisNodeTaskDispatcher:
    def __init__(..., control_guard=None):
        ...
        self.control_guard = control_guard

    async def run(self, *, max_iterations: int | None = None) -> int:
        ...
        requested_notional = float(payload.get("target_quote_amount", 0.0))
        decision = None
        if self.control_guard is not None:
            decision = await self.control_guard.evaluate(
                user_id=user_id,
                symbol=str(payload["symbol"]),
                exchange=str(payload["buy_exchange"]),
                requested_notional=requested_notional,
            )
        if decision is not None and not decision.allowed:
            continue
        task_payload = build_node_execution_task_payload(
            payload,
            user_id=user_id,
            source_message_id=message_id,
        )
        if decision is not None and 0 < decision.approved_notional < requested_notional:
            task_payload["target_quote_amount"] = str(decision.approved_notional)
```

```python
class RedisExecutionTaskConsumer(RedisSpotConsumer):
    def __init__(..., control_guard=None, **kwargs):
        super().__init__(**kwargs)
        self.control_guard = control_guard

    async def run(self, *, credentials_by_exchange: dict, max_iterations: int | None = None) -> int:
        ...
        if self.control_guard is not None:
            decision = await self.control_guard.evaluate(
                user_id=str(payload["user_id"]),
                symbol=str(payload["symbol"]),
                exchange=str(payload["buy_exchange"]),
                requested_notional=float(payload.get("target_quote_amount", 0.0)),
            )
            if not decision.allowed:
                self.last_id = message_id
                processed += 1
                continue
            if 0 < decision.approved_notional < float(payload.get("target_quote_amount", 0.0)):
                payload = dict(payload)
                payload["target_quote_amount"] = str(decision.approved_notional)
        await self.dispatcher.dispatch(payload, credentials_by_exchange=credentials_by_exchange)
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_live_workers.py -v`
Expected: PASS，`dispatcher` 能前置拦截/缩量，`executor` 能二次兜底拦截/缩量

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py app/runtime/worker_service.py tests/test_live_workers.py
git commit -m "feat: enforce control plane in dispatcher and executor"
```

### Task 5: 补齐部署资产、运维文档和总回归

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\systemd_assets.py`
- Create: `d:\old\FuRunSystemV4\deploy\systemd\furun-control-admin.service`
- Modify: `d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
- Modify: `d:\old\FuRunSystemV4\tests\test_systemd_assets.py`

- [ ] **Step 1: 先写失败测试，锁定部署资产和样例配置**

```python
from app.runtime.systemd_assets import render_systemd_unit, render_worker_env_example


def test_render_systemd_unit_supports_control_admin():
    unit = render_systemd_unit(role="control-admin")

    assert "FuRun control admin service" in unit
    assert "-m app.runtime.control_admin_service" in unit


def test_worker_env_example_contains_control_admin_settings():
    content = render_worker_env_example()

    assert "CONTROL_ADMIN_ENABLED=0" in content
    assert "CONTROL_ADMIN_BIND_HOST=127.0.0.1" in content
    assert "CONTROL_ADMIN_PORT=8788" in content
    assert "CONTROL_ADMIN_TOKEN=" in content
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_systemd_assets.py -v`
Expected: FAIL，当前 systemd 资产还不支持 `control-admin`

- [ ] **Step 3: 实现部署资产和运维文档**

```python
def render_systemd_unit(*, role: str) -> str:
    if role == "route-admin":
        ...
    elif role == "control-admin":
        description = "FuRun control admin service"
        exec_start = (
            "/home/ubuntu/furunsystemv4/current/.venv/bin/python "
            "-m app.runtime.control_admin_service"
        )
    else:
        ...
```

```ini
[Unit]
Description=FuRun control admin service
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.control_admin_service
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

````md
### Control Admin

主服务器启用控制面服务：

```bash
sudo cp deploy/systemd/furun-control-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now furun-control-admin.service
sudo systemctl status furun-control-admin.service --no-pager
```

本机验证：

```bash
curl -H "Authorization: Bearer ${CONTROL_ADMIN_TOKEN}" http://127.0.0.1:8788/control/limits
curl -X PUT -H "Authorization: Bearer ${CONTROL_ADMIN_TOKEN}" -H "Content-Type: application/json" \
  -d '{"scope_type":"user","scope_id":"42","limit_type":"max_notional","limit_value":35.0,"enabled":true,"priority":100}' \
  http://127.0.0.1:8788/control/limits/user-42-cap
```
````

- [ ] **Step 4: 运行总回归**

Run: `python -m pytest tests/test_control_store.py tests/test_control_admin_service.py tests/test_control_plane.py tests/test_redis_opportunity_flow.py tests/test_live_workers.py tests/test_spot_arbitrage_probe.py tests/test_worker_config.py tests/test_systemd_assets.py -v`
Expected: PASS，控制面存储、接口、运行时接入和部署资产全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/systemd_assets.py deploy/systemd/furun-control-admin.service deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md tests/test_systemd_assets.py
git commit -m "feat: add control admin deployment assets"
```
