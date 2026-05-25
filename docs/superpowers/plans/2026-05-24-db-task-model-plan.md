# Database Task Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为当前套利系统引入“用户账户真值 + 任务真值”最小数据库闭环，让 `dispatcher` 创建数据库任务、节点任务流携带 `task_uuid`、`executor` 回写任务状态。

**Architecture:** 保持 Redis 作为机会流、节点任务流、路由和控制面缓存，新增数据库会话与仓储层承接 `users / proxies / exchange_accounts / strategy_configs / arbitrage_tasks` 的正式真值。`dispatcher` 在向节点流发布前创建 `arbitrage_tasks` 并写入 `task_uuid`，`executor` 围绕 `task_uuid` 将任务从 `DISPATCHED/EXECUTING` 推进到 `SUCCEEDED / FAILED / BLOCKED`。

**Tech Stack:** Python 3.10+, SQLAlchemy, sqlite3, asyncio, pytest, pytest-asyncio, redis.asyncio

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\models.py`
  - 扩展 `ArbitrageTask`
  - 新增 `StrategyConfig`
- `d:\old\FuRunSystemV4\app\db\session.py`
  - 新增数据库引擎与 Session 工厂
- `d:\old\FuRunSystemV4\app\db\task_repository.py`
  - 新增套利任务仓储
- `d:\old\FuRunSystemV4\app\db\account_repository.py`
  - 新增用户账户与代理读取仓储
- `d:\old\FuRunSystemV4\app\runtime\worker_config.py`
  - 增加数据库连接配置
- `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
  - 节点任务载荷增加 `task_uuid` 等数据库关联字段
- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - `dispatcher` 创建数据库任务并写流
  - `executor` 按 `task_uuid` 回写状态
- `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
  - 装配数据库 Session 与仓储依赖
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - 增加数据库配置和任务状态验证说明
- `d:\old\FuRunSystemV4\tests\test_models.py`
  - 覆盖 `StrategyConfig` 和扩展后的 `ArbitrageTask`
- `d:\old\FuRunSystemV4\tests\test_task_repository.py`
  - 覆盖任务仓储 CRUD、状态推进与幂等
- `d:\old\FuRunSystemV4\tests\test_account_repository.py`
  - 覆盖用户账户与代理读取
- `d:\old\FuRunSystemV4\tests\test_worker_config.py`
  - 覆盖数据库配置解析
- `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
  - 覆盖 `task_uuid` 进入节点任务流
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 覆盖 `dispatcher` 建任务、`executor` 回写状态

### Task 1: 扩展 ORM 模型与数据库配置

**Files:**
- Modify: `d:\old\FuRunSystemV4\models.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_config.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_models.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_config.py`

- [ ] **Step 1: 先写失败测试，锁定新模型字段和数据库配置**

```python
from sqlalchemy import inspect

from models import ArbitrageTask, Base, StrategyConfig
from app.runtime.worker_config import WorkerSettings


def test_strategy_config_and_arbitrage_task_expose_expected_columns():
    strategy_columns = {column.key for column in inspect(StrategyConfig).columns}
    task_columns = {column.key for column in inspect(ArbitrageTask).columns}

    assert {
        "id",
        "user_id",
        "strategy_type",
        "name",
        "target_quote_amount",
        "open_spread_bps_threshold",
        "is_enabled",
    } <= strategy_columns
    assert {
        "task_uuid",
        "status",
        "status_reason",
        "worker_node_id",
        "dispatched_at",
        "started_at",
        "finished_at",
    } <= task_columns
    assert Base.metadata.tables["strategy_configs"].name == "strategy_configs"


def test_worker_settings_parse_database_fields():
    settings = WorkerSettings(
        database_enabled=True,
        database_url="sqlite:///./furun.db",
    )

    assert settings.database_enabled is True
    assert settings.database_url == "sqlite:///./furun.db"
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_models.py tests/test_worker_config.py -v`
Expected: FAIL，提示 `StrategyConfig` 不存在，或 `database_enabled / database_url` 尚未定义

- [ ] **Step 3: 实现最小模型扩展与数据库配置**

```python
class StrategyConfig(TimestampMixin, Base):
    __tablename__ = "strategy_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_type: Mapped[str] = mapped_column(String(32), default="spot_futures")
    name: Mapped[str] = mapped_column(String(128))
    symbol_scope_json: Mapped[list] = mapped_column(JSON, default=list)
    exchange_scope_json: Mapped[list] = mapped_column(JSON, default=list)
    target_quote_amount: Mapped[float] = mapped_column(Float, default=100.0)
    open_spread_bps_threshold: Mapped[float] = mapped_column(Float, default=0.0)
    close_spread_bps_threshold: Mapped[float] = mapped_column(Float, default=0.0)
    max_single_task_notional: Mapped[float] = mapped_column(Float, default=100.0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
```

```python
class ArbitrageTask(TimestampMixin, Base):
    __tablename__ = "arbitrage_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_configs.id"),
        nullable=True,
    )
    opportunity_id: Mapped[str] = mapped_column(String(128), index=True)
    env_mode: Mapped[str] = mapped_column(String(16), default="testnet")
    task_type: Mapped[str] = mapped_column(String(32), default="open")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    spot_exchange: Mapped[str] = mapped_column(String(32))
    derivative_exchange: Mapped[str] = mapped_column(String(32))
    target_notional: Mapped[float] = mapped_column(Float)
    expected_spread_bps: Mapped[float] = mapped_column(Float)
    expected_funding_bps: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="CREATED")
    status_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    home_region: Mapped[str] = mapped_column(String(32), default="default")
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

```python
class WorkerSettings(BaseSettings):
    worker_role: Literal["scanner", "consumer", "dispatcher", "executor"] = "scanner"
    worker_region: str = "default"
    database_enabled: bool = False
    database_url: str = "sqlite:///./furun.db"
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_models.py tests/test_worker_config.py -v`
Expected: PASS，模型列与数据库配置解析通过

- [ ] **Step 5: 提交这一小步**

```bash
git add models.py app/runtime/worker_config.py tests/test_models.py tests/test_worker_config.py
git commit -m "feat: add database task model entities"
```

### Task 2: 新增数据库会话与仓储层

**Files:**
- Create: `d:\old\FuRunSystemV4\app\db\session.py`
- Create: `d:\old\FuRunSystemV4\app\db\task_repository.py`
- Create: `d:\old\FuRunSystemV4\app\db\account_repository.py`
- Create: `d:\old\FuRunSystemV4\tests\test_task_repository.py`
- Create: `d:\old\FuRunSystemV4\tests\test_account_repository.py`

- [ ] **Step 1: 先写失败测试，锁定任务仓储与账户读取仓储行为**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.account_repository import AccountRepository
from app.db.task_repository import ArbitrageTaskCreate, TaskRepository
from models import Base, ExchangeAccount, Proxy, StrategyConfig, User


def test_task_repository_creates_and_updates_task_status():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    task = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-1",
            user_id=42,
            strategy_config_id=None,
            opportunity_id="opp-1",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="okx",
            derivative_exchange="gate",
            target_notional=100.0,
            expected_spread_bps=120.0,
            expected_funding_bps=0.0,
            idempotency_key="idem-1",
            home_region="main",
        )
    )
    repository.mark_dispatched(task.task_uuid, worker_node_id="node-a")
    repository.mark_executing(task.task_uuid, worker_node_id="node-a")
    repository.mark_succeeded(task.task_uuid)

    refreshed = repository.get_by_task_uuid("task-1")
    assert refreshed is not None
    assert refreshed.status == "SUCCEEDED"
    assert refreshed.worker_node_id == "node-a"
    assert refreshed.dispatched_at is not None
    assert refreshed.started_at is not None
    assert refreshed.finished_at is not None
```

```python
def test_account_repository_returns_enabled_accounts_with_proxy():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.add(Proxy(id=7, host="1.2.3.4", port=8080, region="sg"))
    session.add(
        ExchangeAccount(
            user_id=42,
            exchange="okx",
            account_label="default",
            env_mode="testnet",
            api_key_ciphertext="ak",
            secret_ciphertext="sk",
            proxy_id=7,
            is_enabled=True,
        )
    )
    session.commit()

    repository = AccountRepository(session)
    accounts = repository.list_enabled_accounts(user_id=42, env_mode="testnet")

    assert len(accounts) == 1
    assert accounts[0].exchange == "okx"
    assert accounts[0].proxy.host == "1.2.3.4"
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_task_repository.py tests/test_account_repository.py -v`
Expected: FAIL，提示 `app.db` 下相关模块尚不存在

- [ ] **Step 3: 实现最小数据库会话和仓储层**

```python
from dataclasses import dataclass
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def build_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, future=True, connect_args=connect_args)


def build_session_factory(database_url: str):
    engine = build_engine(database_url)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
```

```python
@dataclass(slots=True)
class ArbitrageTaskCreate:
    task_uuid: str
    user_id: int
    strategy_config_id: int | None
    opportunity_id: str
    env_mode: str
    task_type: str
    symbol: str
    spot_exchange: str
    derivative_exchange: str
    target_notional: float
    expected_spread_bps: float
    expected_funding_bps: float
    idempotency_key: str
    home_region: str


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_task(self, data: ArbitrageTaskCreate) -> ArbitrageTask:
        task = ArbitrageTask(**data.__dict__)
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task
```

```python
class AccountRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_enabled_accounts(self, *, user_id: int, env_mode: str) -> list[ExchangeAccount]:
        return (
            self.session.query(ExchangeAccount)
            .filter(
                ExchangeAccount.user_id == user_id,
                ExchangeAccount.env_mode == env_mode,
                ExchangeAccount.is_enabled.is_(True),
            )
            .all()
        )
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_task_repository.py tests/test_account_repository.py -v`
Expected: PASS，任务仓储与账户仓储行为通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/db/session.py app/db/task_repository.py app/db/account_repository.py tests/test_task_repository.py tests/test_account_repository.py
git commit -m "feat: add database repositories for tasks and accounts"
```

### Task 3: 让节点任务流携带 task_uuid，并由 dispatcher 创建数据库任务

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定 dispatcher 建任务与 task_uuid 透传**

```python
def test_build_node_execution_task_payload_includes_task_uuid():
    payload = build_node_execution_task_payload(
        {"symbol": "BTC/USDT"},
        user_id="42",
        source_message_id="1-0",
        task_uuid="task-1",
    )

    assert payload["task_uuid"] == "task-1"
    assert payload["user_id"] == "42"
    assert payload["source_message_id"] == "1-0"
```

```python
@pytest.mark.asyncio
async def test_dispatcher_creates_database_task_before_publishing_node_task():
    redis_client = FakeRedis()
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-1")
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        task_repository=repository,
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert repository.created[0]["user_id"] == 42
    assert redis_client.xadds[0][1]["task_uuid"] == "task-1"
    assert repository.dispatched == [("task-1", "node-a")]
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_redis_opportunity_flow.py tests/test_live_workers.py -v`
Expected: FAIL，`build_node_execution_task_payload()` 尚不接受 `task_uuid`，`RedisNodeTaskDispatcher` 尚不接任务仓储

- [ ] **Step 3: 实现最小 task_uuid 透传与 dispatcher 建任务逻辑**

```python
def build_node_execution_task_payload(
    payload: dict,
    *,
    user_id: str,
    source_message_id: str,
    task_uuid: str,
) -> dict[str, str]:
    task_payload = {key: str(value) for key, value in payload.items()}
    task_payload["user_id"] = user_id
    task_payload["source_message_id"] = source_message_id
    task_payload["task_uuid"] = task_uuid
    return task_payload
```

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
        control_guard=None,
        task_repository=None,
        block_ms: int = 1000,
        event_router=None,
        region: str = "default",
    ) -> None:
        self.redis_client = redis_client
        self.user_ids = user_ids
        self.route_resolver = route_resolver
        self.task_publisher = task_publisher
        self.stream_key = stream_key
        self.control_guard = control_guard
        self.task_repository = task_repository
        self.block_ms = block_ms
        self.event_router = event_router
        self.region = region
        self.last_id = "0-0"

    async def run(self, *, max_iterations: int | None = None) -> int:
        processed = 0
        entries = await self.redis_client.xread(
            {self.stream_key: self.last_id},
            count=1,
            block=self.block_ms,
        )
        if not entries:
            return processed
        _, messages = entries[0]
        for message_id, payload in messages:
            for user_id in self.user_ids:
                node_id = await self.route_resolver.get_user_node(user_id)
                if node_id is None:
                    continue
                requested_notional = float(payload.get("target_quote_amount", 15.0))
                task_record = self.task_repository.create_task(
                    ArbitrageTaskCreate(
                        task_uuid=f"task-{message_id}-{user_id}",
                        user_id=int(user_id),
                        strategy_config_id=None,
                        opportunity_id=str(message_id),
                        env_mode="testnet",
                        task_type="open",
                        symbol=str(payload["symbol"]),
                        spot_exchange=str(payload["buy_exchange"]),
                        derivative_exchange=str(payload["sell_exchange"]),
                        target_notional=requested_notional,
                        expected_spread_bps=float(payload.get("spread_bps", 0.0)),
                        expected_funding_bps=0.0,
                        idempotency_key=f"{user_id}:{message_id}:open",
                        home_region=self.region,
                    )
                )
                decision = None
                if self.control_guard is not None:
                    decision = await self.control_guard.evaluate(
                        user_id=user_id,
                        symbol=str(payload["symbol"]),
                        exchange=str(payload["buy_exchange"]),
                        requested_notional=requested_notional,
                    )
                if decision is not None and not decision.allowed:
                    self.task_repository.mark_blocked(
                        task_record.task_uuid,
                        reason=decision.reason or "blocked",
                    )
                    continue
                task_payload = build_node_execution_task_payload(
                    payload,
                    user_id=user_id,
                    source_message_id=message_id,
                    task_uuid=task_record.task_uuid,
                )
                if decision is not None and 0 < decision.approved_notional < requested_notional:
                    task_payload["target_quote_amount"] = str(decision.approved_notional)
                await self.task_publisher.publish(node_id=node_id, task_payload=task_payload)
                self.task_repository.mark_dispatched(
                    task_record.task_uuid,
                    worker_node_id=node_id,
                )
            self.last_id = message_id
            processed += 1
        return processed
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_redis_opportunity_flow.py tests/test_live_workers.py -v`
Expected: PASS，dispatcher 会先建任务，再把 `task_uuid` 带入节点任务流

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/redis_flow.py app/runtime/live_workers.py app/runtime/worker_service.py tests/test_redis_opportunity_flow.py tests/test_live_workers.py
git commit -m "feat: create database tasks in dispatcher"
```

### Task 4: 让 executor 围绕 task_uuid 回写数据库状态

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定 executor 的状态推进**

```python
@pytest.mark.asyncio
async def test_executor_marks_task_executing_and_succeeded():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_exec_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "task_uuid": "task-1",
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
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert repository.executing == [("task-1", "node-a")]
    assert repository.succeeded == ["task-1"]
```

```python
@pytest.mark.asyncio
async def test_executor_marks_task_blocked_when_control_guard_rejects():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_exec_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "task_uuid": "task-1",
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
    repository = FakeTaskRepository(task_uuid="task-1")
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(FakeSpotService()),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        control_guard=FakeControlGuard(
            allowed=False,
            approved_notional=0.0,
            reason="reduce_only",
        ),
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert repository.blocked == [("task-1", "reduce_only")]
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -v`
Expected: FAIL，`RedisExecutionTaskConsumer` 尚不接收 `task_repository`，也不会按 `task_uuid` 更新状态

- [ ] **Step 3: 实现最小 executor 状态回写**

```python
class RedisExecutionTaskConsumer(RedisSpotConsumer):
    def __init__(self, *, control_guard=None, task_repository=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.control_guard = control_guard
        self.task_repository = task_repository

    async def run(
        self,
        *,
        credentials_by_exchange: dict,
        max_iterations: int | None = None,
    ) -> int:
        processed = 0
        entries = await self.redis_client.xread(
            {self.stream_key: self.last_id},
            count=1,
            block=self.block_ms,
        )
        if not entries:
            return processed
        _, messages = entries[0]
        for message_id, payload in messages:
            task_uuid = str(payload["task_uuid"])
            requested_notional = float(payload.get("target_quote_amount", 15.0))
            if self.task_repository is not None:
                self.task_repository.mark_executing(
                    task_uuid,
                    worker_node_id=self.region,
                )
            decision = None
            if self.control_guard is not None:
                decision = await self.control_guard.evaluate(
                    user_id=str(payload["user_id"]),
                    symbol=str(payload["symbol"]),
                    exchange=str(payload["buy_exchange"]),
                    requested_notional=requested_notional,
                )
            effective_payload = payload
            if decision is not None and not decision.allowed:
                if self.task_repository is not None:
                    self.task_repository.mark_blocked(
                        task_uuid,
                        reason=decision.reason or "blocked",
                    )
                self.last_id = message_id
                processed += 1
                continue
            if decision is not None and 0 < decision.approved_notional < requested_notional:
                effective_payload = dict(payload)
                effective_payload["target_quote_amount"] = str(decision.approved_notional)
            try:
                await self.dispatcher.dispatch(
                    effective_payload,
                    credentials_by_exchange=credentials_by_exchange,
                )
                if self.task_repository is not None:
                    self.task_repository.mark_succeeded(task_uuid)
            except Exception as exc:
                if self.task_repository is not None:
                    self.task_repository.mark_failed(task_uuid, reason=str(exc))
                raise
            self.last_id = message_id
            processed += 1
        return processed
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -v`
Expected: PASS，executor 会按 `task_uuid` 回写 `EXECUTING / SUCCEEDED / FAILED / BLOCKED`

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py app/runtime/worker_service.py tests/test_live_workers.py
git commit -m "feat: persist executor task status transitions"
```

### Task 5: 更新运维文档并跑阶段总回归

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 先补运维文档里的数据库任务验证示例**

````md
### Database Task Model

启用数据库真值后，可通过数据库直接检查任务状态：

```bash
sqlite3 furun.db "select task_uuid, user_id, status, status_reason, worker_node_id from arbitrage_tasks order by id desc limit 10;"
```

关注状态：

- `CREATED`
- `DISPATCHED`
- `EXECUTING`
- `SUCCEEDED`
- `FAILED`
- `BLOCKED`

节点任务流里应包含：

- `task_uuid`
- `user_id`
- `source_message_id`
````

- [ ] **Step 2: 运行本阶段总回归**

Run: `python -m pytest tests/test_models.py tests/test_worker_config.py tests/test_task_repository.py tests/test_account_repository.py tests/test_redis_opportunity_flow.py tests/test_live_workers.py -v`
Expected: PASS，模型、仓储、dispatcher、executor 相关行为全部通过

- [ ] **Step 3: 提交这一小步**

```bash
git add docs/ops/live-workers-systemd.md
git commit -m "docs: add database task model verification steps"
```
