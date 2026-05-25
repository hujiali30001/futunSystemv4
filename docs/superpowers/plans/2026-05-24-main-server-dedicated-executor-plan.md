# Main Server Dedicated Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前双机同构的 `scanner + consumer` 运行形态，改造成“主服务器负责公共扫描与节点级任务分发、专用节点只负责绑定用户执行任务”的可落地实现。

**Architecture:** 复用现有 `stream:spot_opps` 作为公共机会流，在 Redis 中新增节点级执行任务流与用户路由键。主服务器新增 `dispatcher` 角色，把公共机会转成节点专属执行任务；专用节点新增 `executor` 角色，只消费自己的执行任务流并复用现有执行链路完成下单。

**Tech Stack:** Python 3.10+, asyncio, redis.asyncio, pydantic-settings, pytest, pytest-asyncio, systemd

---

## 文件结构与职责

- `app/runtime/worker_config.py`
  - 扩展运行配置，新增 `dispatcher` / `executor` 角色、`node_id`、节点级 stream 配置和待分发用户列表
- `app/runtime/redis_flow.py`
  - 新增用户路由查询、节点级执行任务 payload 构造、执行结果回写辅助
- `app/runtime/live_workers.py`
  - 新增公共机会分发 worker 和节点级执行任务 consumer
- `app/runtime/worker_service.py`
  - 扩展 `WorkerApp` 与 `DefaultWorkerFactory`，支持 `scanner`、`dispatcher`、`executor`
- `deploy/systemd/furun-spot-dispatcher.service`
  - 主服务器用的 dispatcher unit
- `deploy/systemd/furun-spot-executor.service`
  - 专用执行节点用的 executor unit
- `deploy/systemd/.env.worker.example`
  - 增加节点角色与 stream 键示例
- `docs/ops/live-workers-systemd.md`
  - 补充新部署拓扑、启动命令、迁移顺序和当前 OKX close 修复结论
- `tests/test_worker_config.py`
  - 覆盖新配置字段、角色和值解析
- `tests/test_redis_opportunity_flow.py`
  - 覆盖节点级执行任务 payload、用户路由键读取
- `tests/test_live_workers.py`
  - 覆盖 dispatcher 和 executor 的读写链路
- `tests/test_worker_service.py`
  - 覆盖新角色分发、stream 选择和 Redis 关闭
- `tests/test_systemd_assets.py`
  - 覆盖新增 unit 与 `.env.worker.example`

### Task 1: 扩展配置与节点路由基础能力

**Files:**
- Modify: `app/runtime/worker_config.py`
- Modify: `app/runtime/redis_flow.py`
- Test: `tests/test_worker_config.py`
- Test: `tests/test_redis_opportunity_flow.py`

- [ ] **Step 1: 先写配置与路由的失败测试**

```python
import pytest

from app.runtime.worker_config import WorkerSettings
from app.runtime.redis_flow import NodeExecutionTaskPublisher, UserNodeRouter


def test_worker_settings_support_dispatcher_and_executor_roles():
    settings = WorkerSettings(
        worker_role="dispatcher",
        node_id="main",
        dispatch_source_stream="stream:spot_opps",
        executor_stream_key="stream:spot_exec_tasks:main",
        dispatch_user_ids="42,99",
    )

    assert settings.worker_role == "dispatcher"
    assert settings.node_id == "main"
    assert settings.dispatch_source_stream == "stream:spot_opps"
    assert settings.executor_stream_key == "stream:spot_exec_tasks:main"
    assert settings.dispatch_user_ids == ["42", "99"]


@pytest.mark.asyncio
async def test_user_node_router_reads_route_key_from_redis():
    class FakeRedis:
        def __init__(self, route_values):
            self.route_values = route_values

        async def get(self, key):
            return self.route_values.get(key)

    redis_client = FakeRedis(route_values={"route:user_node:42": "node-a"})
    router = UserNodeRouter(redis_client)

    node_id = await router.get_user_node("42")

    assert node_id == "node-a"


@pytest.mark.asyncio
async def test_node_execution_task_publisher_writes_node_task_stream():
    class FakeRedis:
        def __init__(self):
            self.xadds = []

        async def xadd(self, key, fields):
            self.xadds.append((key, fields))
            return "1-0"

    redis_client = FakeRedis()
    publisher = NodeExecutionTaskPublisher(redis_client)

    await publisher.publish(
        node_id="node-a",
        task_payload={
            "user_id": "42",
            "symbol": "BTC/USDT",
            "buy_exchange": "okx",
            "sell_exchange": "gate",
            "source_message_id": "1-0",
        },
    )

    assert redis_client.xadds[0][0] == "stream:spot_exec_tasks:node-a"
    assert redis_client.xadds[0][1]["user_id"] == "42"
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `pytest tests/test_worker_config.py tests/test_redis_opportunity_flow.py -v`
Expected: FAIL，提示 `WorkerSettings` 没有新字段，或 `UserNodeRouter` / `NodeExecutionTaskPublisher` 尚不存在

- [ ] **Step 3: 最小化实现配置与路由基础能力**

```python
class WorkerSettings(BaseSettings):
    worker_role: Literal["scanner", "consumer", "dispatcher", "executor"] = "scanner"
    worker_region: str = "default"
    node_id: str = "default"
    dispatch_user_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    dispatch_source_stream: str = "stream:spot_opps"
    executor_stream_key: str | None = None

    @property
    def resolved_executor_stream_key(self) -> str:
        return self.executor_stream_key or f"stream:spot_exec_tasks:{self.node_id}"
```

```python
class UserNodeRouter:
    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    async def get_user_node(self, user_id: str) -> str | None:
        return await self.redis_client.get(f"route:user_node:{user_id}")


class NodeExecutionTaskPublisher:
    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    async def publish(self, *, node_id: str, task_payload: dict[str, str]) -> str:
        return await self.redis_client.xadd(
            f"stream:spot_exec_tasks:{node_id}",
            task_payload,
        )
```

- [ ] **Step 4: 重新运行配置与路由测试**

Run: `pytest tests/test_worker_config.py tests/test_redis_opportunity_flow.py -v`
Expected: PASS，新增角色、节点 stream 和用户路由测试全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/worker_config.py app/runtime/redis_flow.py tests/test_worker_config.py tests/test_redis_opportunity_flow.py
git commit -m "feat: add node routing and execution stream config"
```

### Task 2: 新增主服务器 dispatcher，把公共机会转成节点级执行任务

**Files:**
- Modify: `app/runtime/redis_flow.py`
- Modify: `app/runtime/live_workers.py`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_redis_opportunity_flow.py`

- [ ] **Step 1: 写 dispatcher 行为的失败测试**

```python
@pytest.mark.asyncio
async def test_dispatcher_worker_routes_public_opportunity_into_node_stream():
    class FakeRedis:
        def __init__(self, xread_messages, route_values):
            self.xread_messages = xread_messages
            self.route_values = route_values
            self.xadds = []

        async def xread(self, *args, **kwargs):
            return self.xread_messages

        async def get(self, key):
            return self.route_values.get(key)

        async def xadd(self, key, fields):
            self.xadds.append((key, fields))
            return "2-0"

    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ],
        route_values={"route:user_node:42": "node-a"},
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        block_ms=1,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert redis_client.xadds[0][0] == "stream:spot_exec_tasks:node-a"
    assert redis_client.xadds[0][1]["user_id"] == "42"
    assert redis_client.xadds[0][1]["source_message_id"] == "1-0"
```

- [ ] **Step 2: 运行 dispatcher 测试并确认失败**

Run: `pytest tests/test_live_workers.py tests/test_redis_opportunity_flow.py -v`
Expected: FAIL，提示 `RedisNodeTaskDispatcher` 不存在或没有节点级任务写入

- [ ] **Step 3: 实现最小 dispatcher worker**

```python
class RedisNodeTaskDispatcher:
    def __init__(
        self,
        *,
        redis_client,
        user_ids: list[str],
        route_resolver,
        task_publisher,
        stream_key: str,
        block_ms: int = 1000,
    ) -> None:
        self.redis_client = redis_client
        self.user_ids = user_ids
        self.route_resolver = route_resolver
        self.task_publisher = task_publisher
        self.stream_key = stream_key
        self.block_ms = block_ms
        self.last_id = "0-0"

    async def run(self, *, max_iterations: int | None = None) -> int:
        processed = 0
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            entries = await self.redis_client.xread(
                {self.stream_key: self.last_id},
                count=1,
                block=self.block_ms,
            )
            for _, messages in entries:
                for message_id, payload in messages:
                    for user_id in self.user_ids:
                        node_id = await self.route_resolver.get_user_node(user_id)
                        if node_id is None:
                            continue
                        task_payload = dict(payload)
                        task_payload["user_id"] = user_id
                        task_payload["source_message_id"] = message_id
                        await self.task_publisher.publish(
                            node_id=node_id,
                            task_payload=task_payload,
                        )
                    self.last_id = message_id
                    processed += 1
            iteration += 1
        return processed
```

- [ ] **Step 4: 重新运行 dispatcher 相关测试**

Run: `pytest tests/test_live_workers.py tests/test_redis_opportunity_flow.py -v`
Expected: PASS，公共机会流可被转发到节点级执行任务流

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/redis_flow.py app/runtime/live_workers.py tests/test_live_workers.py tests/test_redis_opportunity_flow.py
git commit -m "feat: add node task dispatcher worker"
```

### Task 3: 新增 executor 角色，并让专用节点只消费自己的任务流

**Files:**
- Modify: `app/runtime/live_workers.py`
- Modify: `app/runtime/worker_service.py`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_worker_service.py`

- [ ] **Step 1: 先写 executor 与 worker_service 的失败测试**

```python
@pytest.mark.asyncio
async def test_executor_worker_reads_only_its_node_stream():
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
        block_ms=1,
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert service.calls[0]["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_worker_app_dispatches_executor_role(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="executor",
            node_id="node-a",
            spot_exchanges=["okx", "gate"],
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert factory.executor_worker.calls[0]["stream_key"] == "stream:spot_exec_tasks:node-a"
```

- [ ] **Step 2: 运行 executor 与 worker_service 测试并确认失败**

Run: `pytest tests/test_live_workers.py tests/test_worker_service.py -v`
Expected: FAIL，提示 `executor` 角色或 `RedisExecutionTaskConsumer` 尚未实现

- [ ] **Step 3: 最小化实现 executor consumer 与角色分发**

```python
class RedisExecutionTaskConsumer(RedisSpotConsumer):
    pass
```

```python
class DefaultWorkerFactory:
    def build_dispatcher_worker(self, *, redis_client: Redis):
        dispatcher = RedisNodeTaskDispatcher(
            redis_client=redis_client,
            user_ids=self.settings.dispatch_user_ids,
            route_resolver=UserNodeRouter(redis_client),
            task_publisher=NodeExecutionTaskPublisher(redis_client),
            stream_key=self.settings.dispatch_source_stream,
            block_ms=self.settings.consumer_block_ms,
        )
        return dispatcher

    def build_executor_worker(self, *, redis_client: Redis):
        dispatcher = RedisOpportunityDispatcher(self.spot_service)
        consumer = RedisExecutionTaskConsumer(
            redis_client=redis_client,
            dispatcher=dispatcher,
            stream_key=self.settings.resolved_executor_stream_key,
            block_ms=self.settings.consumer_block_ms,
            event_router=self.event_router,
            region=self.settings.worker_region,
        )
        return ConsumerWorker(consumer=consumer)
```

```python
if self.settings.worker_role == "dispatcher":
    worker = factory.build_dispatcher_worker(redis_client=redis_client)
    await worker.run(max_iterations=None)
    return

if self.settings.worker_role == "executor":
    worker = factory.build_executor_worker(redis_client=redis_client)
    await worker.run(
        credentials_by_exchange=credentials_by_exchange,
        stream_key=self.settings.resolved_executor_stream_key,
    )
    return
```

- [ ] **Step 4: 重新运行 worker 相关测试**

Run: `pytest tests/test_live_workers.py tests/test_worker_service.py -v`
Expected: PASS，专用节点只读取自己的执行任务流，`WorkerApp` 能正确分发 `dispatcher` 和 `executor`

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py app/runtime/worker_service.py tests/test_live_workers.py tests/test_worker_service.py
git commit -m "feat: add dispatcher and executor worker roles"
```

### Task 4: 补 systemd、示例环境和运维文档

**Files:**
- Create: `deploy/systemd/furun-spot-dispatcher.service`
- Create: `deploy/systemd/furun-spot-executor.service`
- Modify: `deploy/systemd/.env.worker.example`
- Modify: `docs/ops/live-workers-systemd.md`
- Test: `tests/test_systemd_assets.py`

- [ ] **Step 1: 先写部署资产的失败测试**

```python
from pathlib import Path


def test_dispatcher_and_executor_units_exist():
    dispatcher_unit = Path("deploy/systemd/furun-spot-dispatcher.service")
    executor_unit = Path("deploy/systemd/furun-spot-executor.service")

    assert dispatcher_unit.exists()
    assert executor_unit.exists()


def test_env_example_contains_node_role_fields():
    content = Path("deploy/systemd/.env.worker.example").read_text(encoding="utf-8")

    assert "NODE_ID=" in content
    assert "DISPATCH_SOURCE_STREAM=" in content
    assert "EXECUTOR_STREAM_KEY=" in content
```

- [ ] **Step 2: 运行部署资产测试并确认失败**

Run: `pytest tests/test_systemd_assets.py -v`
Expected: FAIL，提示 unit 文件不存在或示例环境缺少节点配置字段

- [ ] **Step 3: 增加 unit、环境示例和运维文档**

```ini
[Unit]
Description=FuRun spot dispatcher worker
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.worker_service --role dispatcher
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```ini
[Unit]
Description=FuRun spot executor worker
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.worker_service --role executor
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```dotenv
WORKER_ROLE=scanner
WORKER_REGION=main
NODE_ID=main
DISPATCH_USER_IDS=42,99
DISPATCH_SOURCE_STREAM=stream:spot_opps
EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:main
```

- [ ] **Step 4: 重新运行部署资产测试**

Run: `pytest tests/test_systemd_assets.py -v`
Expected: PASS，新增 unit 与环境示例字段全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add deploy/systemd/furun-spot-dispatcher.service deploy/systemd/furun-spot-executor.service deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md tests/test_systemd_assets.py
git commit -m "docs: add dispatcher executor deployment assets"
```

### Task 5: 总回归与远端迁移验证

**Files:**
- Modify: `docs/ops/live-workers-systemd.md`
- Test: `tests/test_worker_config.py`
- Test: `tests/test_redis_opportunity_flow.py`
- Test: `tests/test_live_workers.py`
- Test: `tests/test_worker_service.py`
- Test: `tests/test_systemd_assets.py`

- [ ] **Step 1: 运行本地总回归**

Run: `pytest tests/test_worker_config.py tests/test_redis_opportunity_flow.py tests/test_live_workers.py tests/test_worker_service.py tests/test_systemd_assets.py -v`
Expected: PASS，节点级路由、dispatcher、executor 和 systemd 资产测试全部通过

- [ ] **Step 2: 按文档在主服务器启用 scanner + dispatcher**

Run:

```bash
sudo cp deploy/systemd/furun-spot-scanner.service /etc/systemd/system/
sudo cp deploy/systemd/furun-spot-dispatcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable furun-spot-scanner.service
sudo systemctl enable furun-spot-dispatcher.service
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-dispatcher.service
```

Expected: `systemctl is-active` 返回 `active`

- [ ] **Step 3: 在专用节点启用 executor，并停止旧 scanner**

Run:

```bash
sudo cp deploy/systemd/furun-spot-executor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable furun-spot-scanner.service || true
sudo systemctl stop furun-spot-scanner.service || true
sudo systemctl enable furun-spot-executor.service
sudo systemctl restart furun-spot-executor.service
```

Expected: 专用节点只保留 `furun-spot-executor.service` 为 `active`

- [ ] **Step 4: 验证 Redis 路由与执行结果**

Run:

```bash
redis-cli GET route:user_node:42
redis-cli XLEN stream:spot_opps
redis-cli XLEN stream:spot_exec_tasks:node-a
redis-cli XREVRANGE stream:spot_exec_tasks:node-a + - COUNT 5
redis-cli XLEN stream:spot_exec_results
```

Expected:
- `route:user_node:42` 返回 `node-a`
- `stream:spot_opps` 持续增长
- `stream:spot_exec_tasks:node-a` 出现被路由过来的任务
- `stream:spot_exec_results` 出现执行节点回写结果

- [ ] **Step 5: 完成最终提交**

```bash
git add app/runtime/worker_config.py app/runtime/redis_flow.py app/runtime/live_workers.py app/runtime/worker_service.py deploy/systemd/furun-spot-dispatcher.service deploy/systemd/furun-spot-executor.service deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md tests/test_worker_config.py tests/test_redis_opportunity_flow.py tests/test_live_workers.py tests/test_worker_service.py tests/test_systemd_assets.py
git commit -m "feat: split main server and dedicated executor roles"
```

## 自检结果

- Spec coverage:
  - 新角色与节点职责拆分：`Task 1`、`Task 3`
  - 公共机会流与节点级任务流：`Task 1`、`Task 2`
  - 专用节点只消费自己的执行任务：`Task 3`
  - `systemd` 与环境变量迁移：`Task 4`
  - 当前联调环境的迁移验收：`Task 5`
  - 运维文档补充 OKX close 修复与部署结论：`Task 4`
- Placeholder scan:
  - 未保留 `TODO`、`TBD`、"后续补"、"类似 Task N" 等占位语句
- Type consistency:
  - 统一使用 `worker_role`、`node_id`、`dispatch_user_ids`、`dispatch_source_stream`、`resolved_executor_stream_key`
  - 节点级执行任务流统一使用 `stream:spot_exec_tasks:<node_id>`
