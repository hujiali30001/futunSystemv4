# Dispatcher DB Account Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `dispatcher` 默认从数据库自动发现“有启用账户且有启用策略”的候选用户，再结合 Redis 路由继续走现有策略分发与任务创建主链。

**Architecture:** 保持现有 `strategy_configs -> arbitrage_tasks -> node stream -> executor` 主链和 Redis 路由不变，只把“候选用户来源”从静态 `dispatch_user_ids` 扩展为“数据库自动发现 + 可选白名单覆盖”。实现上新增独立的 `DispatchUserRepository`，在 `live_workers.py` 中新增候选用户解析与轻量发现/跳过事件，同时在 `worker_service.py` 中为 dispatcher 装配该仓储。

**Tech Stack:** Python 3.10+, SQLAlchemy, asyncio, redis.asyncio, pytest, pytest-asyncio

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\app\db\dispatch_user_repository.py`
  - 新增候选用户发现仓储
  - 只负责按 `env_mode` 返回具备分发资格的用户 ID
- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - 为 `RedisNodeTaskDispatcher` 增加数据库候选用户解析逻辑
  - 增加发现层/跳过层结构化事件
- `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
  - 在 `database_enabled=True` 时为 `dispatcher` 装配 `DispatchUserRepository`
- `d:\old\FuRunSystemV4\tests\test_dispatch_user_repository.py`
  - 覆盖数据库候选用户资格规则
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 覆盖数据库自动发现、白名单覆盖、无路由跳过与发现事件
- `d:\old\FuRunSystemV4\tests\test_worker_service.py`
  - 覆盖 dispatcher worker 装配 `DispatchUserRepository`
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - 补充数据库自动发现联调与验证说明

### Task 1: 新增数据库候选用户发现仓储

**Files:**
- Create: `d:\old\FuRunSystemV4\app\db\dispatch_user_repository.py`
- Create: `d:\old\FuRunSystemV4\tests\test_dispatch_user_repository.py`

- [ ] **Step 1: 先写失败测试，锁定候选用户资格规则**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.dispatch_user_repository import DispatchUserRepository
from models import Base, ExchangeAccount, StrategyConfig, User


def test_dispatch_user_repository_returns_only_dispatchable_users_for_env_mode():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            User(id=1, username="u1", is_trading_enabled=True),
            User(id=2, username="u2", is_trading_enabled=False),
            User(id=3, username="u3", is_trading_enabled=True),
            User(id=4, username="u4", is_trading_enabled=True),
        ]
    )
    session.add_all(
        [
            ExchangeAccount(
                user_id=1,
                exchange="bitget",
                account_label="default",
                env_mode="testnet",
                api_key_ciphertext="ak1",
                secret_ciphertext="sk1",
                is_enabled=True,
            ),
            ExchangeAccount(
                user_id=2,
                exchange="bitget",
                account_label="default",
                env_mode="testnet",
                api_key_ciphertext="ak2",
                secret_ciphertext="sk2",
                is_enabled=True,
            ),
            ExchangeAccount(
                user_id=3,
                exchange="bitget",
                account_label="default",
                env_mode="mainnet",
                api_key_ciphertext="ak3",
                secret_ciphertext="sk3",
                is_enabled=True,
            ),
            ExchangeAccount(
                user_id=4,
                exchange="bitget",
                account_label="default",
                env_mode="testnet",
                api_key_ciphertext="ak4",
                secret_ciphertext="sk4",
                is_enabled=True,
            ),
        ]
    )
    session.add_all(
        [
            StrategyConfig(
                user_id=1,
                strategy_type="spot_futures",
                name="s1",
                target_quote_amount=80.0,
                open_spread_bps_threshold=10.0,
                is_enabled=True,
            ),
            StrategyConfig(
                user_id=2,
                strategy_type="spot_futures",
                name="s2",
                target_quote_amount=80.0,
                open_spread_bps_threshold=10.0,
                is_enabled=True,
            ),
            StrategyConfig(
                user_id=3,
                strategy_type="spot_futures",
                name="s3",
                target_quote_amount=80.0,
                open_spread_bps_threshold=10.0,
                is_enabled=True,
            ),
        ]
    )
    session.commit()

    repository = DispatchUserRepository(session)

    user_ids = repository.list_dispatchable_user_ids(env_mode="testnet")

    assert user_ids == ["1"]
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_dispatch_user_repository.py -v`
Expected: FAIL，提示 `app.db.dispatch_user_repository` 不存在

- [ ] **Step 3: 写最小仓储实现**

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import ExchangeAccount, StrategyConfig, User


class DispatchUserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_dispatchable_user_ids(self, *, env_mode: str) -> list[str]:
        statement = (
            select(User.id)
            .where(User.is_trading_enabled.is_(True))
            .where(
                select(ExchangeAccount.id)
                .where(
                    ExchangeAccount.user_id == User.id,
                    ExchangeAccount.env_mode == env_mode,
                    ExchangeAccount.is_enabled.is_(True),
                )
                .exists()
            )
            .where(
                select(StrategyConfig.id)
                .where(
                    StrategyConfig.user_id == User.id,
                    StrategyConfig.strategy_type == "spot_futures",
                    StrategyConfig.is_enabled.is_(True),
                )
                .exists()
            )
            .order_by(User.id.asc())
        )
        return [str(user_id) for user_id in self.session.scalars(statement)]
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_dispatch_user_repository.py -v`
Expected: PASS，只返回同时满足交易开关、账户启用、环境匹配、策略启用的用户

- [ ] **Step 5: 提交这一小步**

```bash
git add app/db/dispatch_user_repository.py tests/test_dispatch_user_repository.py
git commit -m "feat: add dispatch user discovery repository"
```

### Task 2: 让 dispatcher 默认按数据库自动发现候选用户

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定数据库自动发现和白名单覆盖语义**

```python
@pytest.mark.asyncio
async def test_dispatcher_uses_db_discovered_users_when_dispatch_user_ids_is_empty():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    strategy_repository = FakeStrategyConfigRepository(
        [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
    )
    dispatch_user_repository = FakeDispatchUserRepository(["42"])
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=dispatch_user_repository,
        strategy_repository=strategy_repository,
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert dispatch_user_repository.calls == [{"env_mode": "testnet"}]
    assert repository.created[0].user_id == 42
```

```python
@pytest.mark.asyncio
async def test_dispatcher_filters_explicit_dispatch_user_ids_by_database_eligibility():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {
        "route:user_node:42": "node-a",
        "route:user_node:99": "node-b",
    }
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    strategy_repository = FakeStrategyConfigRepository(
        [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
    )
    dispatch_user_repository = FakeDispatchUserRepository(["42"])
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42", "99"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=dispatch_user_repository,
        strategy_repository=strategy_repository,
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert [item.user_id for item in repository.created] == [42]
    assert all(payload["user_id"] == "42" for _, payload in redis_client.xadds)
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "db_discovered_users or explicit_dispatch_user_ids" -v`
Expected: FAIL，提示 `RedisNodeTaskDispatcher` 不接受 `dispatch_user_repository` 或仍只依赖 `self.user_ids`

- [ ] **Step 3: 写最小实现，补候选用户解析 helper**

```python
class RedisNodeTaskDispatcher:
    def __init__(
        self,
        *,
        dispatch_user_repository=None,
        user_ids: list[str],
        env_mode: str = "testnet",
        **kwargs,
    ) -> None:
        ...
        self.dispatch_user_repository = dispatch_user_repository
        self.user_ids = user_ids
        self.env_mode = env_mode

    def _resolve_candidate_user_ids(self) -> list[str]:
        if self.dispatch_user_repository is None:
            return list(self.user_ids)

        discovered = self.dispatch_user_repository.list_dispatchable_user_ids(
            env_mode=self.env_mode
        )
        if not self.user_ids:
            return discovered

        allowed = set(discovered)
        return [user_id for user_id in self.user_ids if user_id in allowed]
```

```python
for _, messages in entries:
    for message_id, payload in messages:
        candidate_user_ids = self._resolve_candidate_user_ids()
        for user_id in candidate_user_ids:
            node_id = await self.route_resolver.get_user_node(user_id)
            if node_id is None:
                continue
            ...
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "db_discovered_users or explicit_dispatch_user_ids" -v`
Expected: PASS，空白名单时走 DB 自动发现，显式白名单时只处理白名单与数据库资格交集

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: discover dispatch users from database"
```

### Task 3: 补发现/跳过事件与 worker 装配

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: 先写失败测试，锁定发现事件、无路由跳过事件和装配行为**

```python
@pytest.mark.asyncio
async def test_dispatcher_emits_discovery_succeeded_event_with_candidate_user_ids():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert router.events[0].event_type == "dispatcher.user.discovery.succeeded"
    assert router.events[0].payload["candidate_user_ids"] == ["42"]
```

```python
@pytest.mark.asyncio
async def test_dispatcher_emits_user_skipped_event_when_route_missing():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    router = FakeEventRouter()
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert router.events[1].event_type == "dispatcher.user.skipped"
    assert router.events[1].payload["user_id"] == "42"
    assert router.events[1].payload["reason"] == "user_route_missing"
```

```python
@pytest.mark.asyncio
async def test_default_worker_factory_builds_dispatcher_with_dispatch_user_repository_when_database_enabled():
    factory = DefaultWorkerFactory(
        settings=WorkerSettings(
            worker_role="dispatcher",
            worker_region="main",
            spot_exchanges=["okx", "bitget"],
            database_enabled=True,
            database_url="sqlite:///:memory:",
        ),
        event_router=FakeEventRouter(),
    )

    worker = factory.build_dispatcher_worker(redis_client=FakeRedis())

    assert worker.dispatch_user_repository is not None
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py tests/test_worker_service.py -k "discovery_succeeded_event or user_skipped_event or dispatch_user_repository" -v`
Expected: FAIL，提示 discovery/skipped 事件未发出，或 `worker.dispatch_user_repository` 不存在

- [ ] **Step 3: 写最小实现**

```python
def _build_dispatch_user_event(
    *,
    event_type: str,
    region: str,
    payload: dict[str, object],
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type=event_type,
        level="INFO",
        service="dispatcher",
        region=region,
        message="dispatcher user discovery event",
        payload=payload,
    )
```

```python
candidate_user_ids = self._resolve_candidate_user_ids()
if self.event_router is not None:
    await self.event_router.dispatch(
        _build_dispatch_user_event(
            event_type="dispatcher.user.discovery.succeeded",
            region=self.region,
            payload={"candidate_user_ids": candidate_user_ids},
        )
    )
for user_id in candidate_user_ids:
    node_id = await self.route_resolver.get_user_node(user_id)
    if node_id is None:
        if self.event_router is not None:
            await self.event_router.dispatch(
                _build_dispatch_user_event(
                    event_type="dispatcher.user.skipped",
                    region=self.region,
                    payload={"user_id": user_id, "reason": "user_route_missing"},
                )
            )
        continue
```

```python
from app.db.dispatch_user_repository import DispatchUserRepository

def build_dispatcher_worker(self, *, redis_client: Redis) -> RedisNodeTaskDispatcher:
    ...
    dispatch_user_repository = None
    if self.settings.database_enabled:
        session_factory = build_session_factory(self.settings.database_url)
        session = session_factory()
        task_repository = TaskRepository(session)
        strategy_repository = StrategyConfigRepository(session)
        dispatch_user_repository = DispatchUserRepository(session)
    return RedisNodeTaskDispatcher(
        ...,
        dispatch_user_repository=dispatch_user_repository,
        env_mode=self.settings.env_mode,
    )
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_live_workers.py tests/test_worker_service.py -k "discovery_succeeded_event or user_skipped_event or dispatch_user_repository" -v`
Expected: PASS，发现事件、无路由跳过事件和 worker 装配全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py app/runtime/worker_service.py tests/test_live_workers.py tests/test_worker_service.py
git commit -m "feat: wire db user discovery into dispatcher"
```

### Task 4: 补文档与完整相关回归

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
- Modify: `d:\old\FuRunSystemV4\tests\test_dispatch_user_repository.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: 先补最后一条失败测试，锁定 `env_mode` 过滤**

```python
def test_dispatch_user_repository_filters_out_accounts_from_other_env_mode():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42", is_trading_enabled=True))
    session.add(
        ExchangeAccount(
            user_id=42,
            exchange="bitget",
            account_label="default",
            env_mode="mainnet",
            api_key_ciphertext="ak",
            secret_ciphertext="sk",
            is_enabled=True,
        )
    )
    session.add(
        StrategyConfig(
            user_id=42,
            strategy_type="spot_futures",
            name="s1",
            target_quote_amount=80.0,
            open_spread_bps_threshold=10.0,
            is_enabled=True,
        )
    )
    session.commit()

    repository = DispatchUserRepository(session)

    assert repository.list_dispatchable_user_ids(env_mode="testnet") == []
```

- [ ] **Step 2: 运行目标测试确认失败**

Run: `python -m pytest tests/test_dispatch_user_repository.py::test_dispatch_user_repository_filters_out_accounts_from_other_env_mode -v`
Expected: FAIL，如果仓储未正确按 `env_mode` 过滤会返回 `["42"]`

- [ ] **Step 3: 完成最小实现并补运维文档**

```python
.where(
    select(ExchangeAccount.id)
    .where(
        ExchangeAccount.user_id == User.id,
        ExchangeAccount.env_mode == env_mode,
        ExchangeAccount.is_enabled.is_(True),
    )
    .exists()
)
```

```md
## Dispatcher DB Account Discovery Validation

1. 在主服务器数据库插入两类用户：
   - 用户 42：`is_trading_enabled=1`，有 `testnet` 启用账户，且有启用策略
   - 用户 99：有启用策略，但只有 `mainnet` 账户
2. 清空 `.env.worker` 中的 `DISPATCH_USER_IDS`，重启 `furun-spot-dispatcher.service`
3. 向 `stream:spot_opps` 写入一条会命中用户 42 策略的机会
4. 查询数据库，确认只为用户 42 生成任务，不为用户 99 生成任务
5. 再把 `DISPATCH_USER_IDS=99` 写回 `.env.worker`，重启 dispatcher，确认即使白名单显式包含 99，只要数据库资格不满足，仍不会生成任务
6. 用 `journalctl -u furun-spot-dispatcher.service -n 100 --no-pager` 检查 `dispatcher.user.discovery.succeeded` 与 `dispatcher.user.skipped`
```

- [ ] **Step 4: 跑完整相关回归**

Run: `python -m pytest tests/test_dispatch_user_repository.py tests/test_live_workers.py tests/test_worker_service.py -v`
Expected: PASS，数据库候选用户发现、dispatcher 自动发现、白名单覆盖、运行事件和 worker 装配全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add docs/ops/live-workers-systemd.md tests/test_dispatch_user_repository.py tests/test_live_workers.py tests/test_worker_service.py
git commit -m "docs: cover dispatcher db account discovery validation"
```

## 自检

- Spec coverage:
  - `DispatchUserRepository` 与资格规则由 Task 1 覆盖
  - 数据库自动发现、`dispatch_user_ids` 可选覆盖、候选用户解析由 Task 2 覆盖
  - 发现/跳过事件与 `worker_service` 装配由 Task 3 覆盖
  - `env_mode` 过滤、运维文档与回归由 Task 4 覆盖
- Placeholder scan:
  - 已检查全文，无 `TBD`、`TODO`、`implement later`、`类似前一任务`
- Type consistency:
  - 全文统一使用 `DispatchUserRepository`、`dispatch_user_repository`、`list_dispatchable_user_ids()`、`RedisNodeTaskDispatcher`
