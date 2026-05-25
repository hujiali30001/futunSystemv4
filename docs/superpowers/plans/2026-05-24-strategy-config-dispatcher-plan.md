# Strategy Config Dispatcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `dispatcher` 从数据库读取用户启用中的 `strategy_configs`，按策略匹配机会并为每条命中策略独立创建带 `strategy_config_id` 的数据库任务与节点任务。

**Architecture:** 保持现有 Redis 机会流、节点任务流、控制面和数据库任务状态机不变，只在 `dispatcher` 边界新增“策略读取 + 策略匹配 + 多策略并行任务创建”能力。实现上新增独立的 `StrategyConfigRepository` 和轻量匹配辅助函数，在 `live_workers.py` 内按“用户 -> 策略 -> 任务”顺序推进，同时把 `strategy_config_id` 透传给控制规则、幂等键和节点任务 payload。

**Tech Stack:** Python 3.10+, SQLAlchemy, asyncio, redis.asyncio, pytest, pytest-asyncio

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\app\db\strategy_config_repository.py`
  - 新增策略读取仓储
  - 只负责按用户读取启用中的策略配置
- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - 新增策略匹配辅助函数
  - 调整 `RedisNodeTaskDispatcher` 为“按命中策略逐条建任务”
- `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
  - 扩展节点任务 payload，增加 `strategy_config_id`
- `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
  - 为 `dispatcher` 装配 `StrategyConfigRepository`
- `d:\old\FuRunSystemV4\tests\test_strategy_config_repository.py`
  - 覆盖策略读取仓储
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 覆盖多策略命中、策略维度控制、幂等键和阻断语义
- `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
  - 覆盖 `strategy_config_id` 进入节点任务 payload
- `d:\old\FuRunSystemV4\tests\test_worker_service.py`
  - 覆盖 `dispatcher` worker 装配策略仓储
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - 补充数据库策略配置联调与日志/数据库验证说明

### Task 1: 新增策略配置读取仓储

**Files:**
- Create: `d:\old\FuRunSystemV4\app\db\strategy_config_repository.py`
- Create: `d:\old\FuRunSystemV4\tests\test_strategy_config_repository.py`

- [ ] **Step 1: 先写失败测试，锁定按用户读取启用策略的行为**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.strategy_config_repository import StrategyConfigRepository
from models import Base, StrategyConfig, User


def test_strategy_config_repository_returns_enabled_spot_futures_strategies_only():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.add_all(
        [
            StrategyConfig(
                id=1,
                user_id=42,
                strategy_type="spot_futures",
                name="btc-primary",
                symbol_scope_json=["BTC/USDT"],
                exchange_scope_json=["bitget", "gate"],
                target_quote_amount=80.0,
                open_spread_bps_threshold=15.0,
                is_enabled=True,
            ),
            StrategyConfig(
                id=2,
                user_id=42,
                strategy_type="spot_futures",
                name="disabled",
                target_quote_amount=60.0,
                open_spread_bps_threshold=10.0,
                is_enabled=False,
            ),
            StrategyConfig(
                id=3,
                user_id=42,
                strategy_type="grid",
                name="other-type",
                target_quote_amount=50.0,
                open_spread_bps_threshold=10.0,
                is_enabled=True,
            ),
        ]
    )
    session.commit()

    repository = StrategyConfigRepository(session)

    strategies = repository.list_enabled_for_user(user_id=42)

    assert [strategy.id for strategy in strategies] == [1]
    assert strategies[0].name == "btc-primary"
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_strategy_config_repository.py -v`
Expected: FAIL，提示 `app.db.strategy_config_repository` 不存在

- [ ] **Step 3: 写最小仓储实现**

```python
from sqlalchemy.orm import Session

from models import StrategyConfig


class StrategyConfigRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_enabled_for_user(
        self,
        *,
        user_id: int,
        strategy_type: str = "spot_futures",
    ) -> list[StrategyConfig]:
        return (
            self.session.query(StrategyConfig)
            .filter(
                StrategyConfig.user_id == user_id,
                StrategyConfig.is_enabled.is_(True),
                StrategyConfig.strategy_type == strategy_type,
            )
            .order_by(StrategyConfig.id.asc())
            .all()
        )
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_strategy_config_repository.py -v`
Expected: PASS，返回启用且类型匹配的策略列表

- [ ] **Step 5: 提交这一小步**

```bash
git add app/db/strategy_config_repository.py tests/test_strategy_config_repository.py
git commit -m "feat: add strategy config repository"
```

### Task 2: 实现策略匹配与多策略任务创建

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定多策略并行、策略金额和幂等键**

```python
@pytest.mark.asyncio
async def test_dispatcher_creates_one_task_per_matching_strategy():
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
                            "target_quote_amount": "15.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1", "task-2"]
    strategy_repository = FakeStrategyConfigRepository(
        [
            FakeStrategyConfig(
                id=11,
                target_quote_amount=80.0,
                open_spread_bps_threshold=10.0,
                symbol_scope_json=["BTC/USDT"],
                exchange_scope_json=["bitget", "gate"],
            ),
            FakeStrategyConfig(
                id=12,
                target_quote_amount=35.0,
                open_spread_bps_threshold=20.0,
                symbol_scope_json=[],
                exchange_scope_json=[],
            ),
        ]
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        strategy_repository=strategy_repository,
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert [item.strategy_config_id for item in repository.created] == [11, 12]
    assert [item.target_notional for item in repository.created] == [80.0, 35.0]
    assert [item.idempotency_key for item in repository.created] == [
        "42:1-0:open:11",
        "42:1-0:open:12",
    ]
    assert redis_client.xadds[0][1]["strategy_config_id"] == "11"
    assert redis_client.xadds[0][1]["target_quote_amount"] == "80.0"
    assert redis_client.xadds[1][1]["strategy_config_id"] == "12"
    assert redis_client.xadds[1][1]["target_quote_amount"] == "35.0"
```

```python
@pytest.mark.asyncio
async def test_dispatcher_passes_strategy_id_into_control_guard_and_blocks_per_strategy():
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
    repository.generated_task_uuids = ["task-1", "task-2"]
    strategy_repository = FakeStrategyConfigRepository(
        [
            FakeStrategyConfig(id=11, target_quote_amount=80.0),
            FakeStrategyConfig(id=12, target_quote_amount=50.0),
        ]
    )
    guard = SequenceControlGuard(
        [
            ControlDecision(allowed=False, approved_notional=0.0, reason="reduce_only"),
            ControlDecision(allowed=True, approved_notional=40.0, reason=None),
        ]
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        strategy_repository=strategy_repository,
        control_guard=guard,
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert [call["strategy_id"] for call in guard.calls] == [11, 12]
    assert repository.blocked == [("task-1", "reduce_only")]
    assert repository.dispatched == [("task-2", "node-a")]
    assert redis_client.xadds == [
        (
            "stream:spot_exec_tasks:node-a",
            {
                "symbol": "BTC/USDT",
                "buy_exchange": "bitget",
                "sell_exchange": "gate",
                "spread_bps": "25.0",
                "user_id": "42",
                "source_message_id": "1-0",
                "task_uuid": "task-2",
                "strategy_config_id": "12",
                "target_quote_amount": "40.0",
            },
        )
    ]
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "matching_strategy or strategy_id_into_control_guard" -v`
Expected: FAIL，提示 `RedisNodeTaskDispatcher` 不接受 `strategy_repository` 或仍只创建一条任务

- [ ] **Step 3: 写最小实现，补策略匹配辅助函数并改 dispatcher 主循环**

```python
def _matches_strategy(payload: dict, strategy) -> bool:
    symbol = str(payload["symbol"])
    buy_exchange = str(payload["buy_exchange"])
    sell_exchange = str(payload["sell_exchange"])
    spread_bps = float(payload.get("spread_bps", 0.0))

    symbols = list(strategy.symbol_scope_json or [])
    exchanges = set(strategy.exchange_scope_json or [])

    if symbols and symbol not in symbols:
        return False
    if exchanges and ({buy_exchange, sell_exchange} - exchanges):
        return False
    if spread_bps < float(strategy.open_spread_bps_threshold):
        return False
    return True
```

```python
class RedisNodeTaskDispatcher:
    def __init__(self, *, strategy_repository=None, env_mode: str = "testnet", **kwargs) -> None:
        ...
        self.strategy_repository = strategy_repository
        self.env_mode = env_mode

    def _iter_matching_strategies(self, *, user_id: str, payload: dict) -> list[Any]:
        if self.strategy_repository is None:
            return []
        strategies = self.strategy_repository.list_enabled_for_user(user_id=int(user_id))
        return [strategy for strategy in strategies if _matches_strategy(payload, strategy)]

    def _create_database_task(..., strategy) :
        requested_notional = float(strategy.target_quote_amount)
        return self.task_repository.create_task(
            ArbitrageTaskCreate(
                task_uuid=uuid4().hex,
                user_id=int(user_id),
                strategy_config_id=int(strategy.id),
                opportunity_id=message_id,
                env_mode=self.env_mode,
                task_type="open",
                symbol=str(payload["symbol"]),
                spot_exchange=str(payload["buy_exchange"]),
                derivative_exchange=str(payload["sell_exchange"]),
                target_notional=requested_notional,
                expected_spread_bps=float(payload.get("spread_bps", 0.0)),
                expected_funding_bps=0.0,
                idempotency_key=f"{user_id}:{message_id}:open:{strategy.id}",
                home_region=self.region,
            )
        )
```

```python
for user_id in self.user_ids:
    node_id = await self.route_resolver.get_user_node(user_id)
    if node_id is None:
        continue
    strategies = self._iter_matching_strategies(user_id=user_id, payload=payload)
    for strategy in strategies:
        requested_notional = float(strategy.target_quote_amount)
        task_record = self._create_database_task(
            user_id=user_id,
            message_id=message_id,
            payload=payload,
            strategy=strategy,
        )
        decision = None
        if self.control_guard is not None:
            decision = await self.control_guard.evaluate(
                user_id=user_id,
                symbol=str(payload["symbol"]),
                exchange=str(payload["buy_exchange"]),
                requested_notional=requested_notional,
                strategy_id=int(strategy.id),
            )
        ...
        task_payload = build_node_execution_task_payload(
            payload,
            user_id=user_id,
            source_message_id=message_id,
            task_uuid=task_uuid,
            strategy_config_id=str(strategy.id),
        )
        task_payload["target_quote_amount"] = str(
            decision.approved_notional if decision is not None and 0 < decision.approved_notional < requested_notional else requested_notional
        )
```

- [ ] **Step 4: 运行定向测试并确认通过**

Run: `python -m pytest tests/test_live_workers.py -k "matching_strategy or strategy_id_into_control_guard" -v`
Expected: PASS，多策略任务创建、策略金额、策略级阻断和 `strategy_id` 透传全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: dispatch tasks per strategy config"
```

### Task 3: 扩展节点任务 payload 与 worker 装配

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: 先写失败测试，锁定 payload 和 worker_factory 装配**

```python
def test_build_node_execution_task_payload_includes_strategy_config_id():
    payload = build_node_execution_task_payload(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
        },
        user_id="42",
        source_message_id="1-0",
        task_uuid="task-1",
        strategy_config_id="11",
    )

    assert payload["task_uuid"] == "task-1"
    assert payload["strategy_config_id"] == "11"
    assert payload["user_id"] == "42"
```

```python
@pytest.mark.asyncio
async def test_default_worker_factory_builds_dispatcher_with_strategy_repository_when_database_enabled():
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

    assert worker.strategy_repository is not None
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_redis_opportunity_flow.py tests/test_worker_service.py -k "strategy_config_id or strategy_repository" -v`
Expected: FAIL，提示 `build_node_execution_task_payload()` 不接受 `strategy_config_id`，或 `worker.strategy_repository` 不存在

- [ ] **Step 3: 写最小实现**

```python
def build_node_execution_task_payload(
    payload: dict,
    *,
    user_id: str,
    source_message_id: str,
    task_uuid: str,
    strategy_config_id: str | None = None,
) -> dict[str, str]:
    task_payload = {key: str(value) for key, value in payload.items()}
    task_payload["user_id"] = user_id
    task_payload["source_message_id"] = source_message_id
    task_payload["task_uuid"] = task_uuid
    if strategy_config_id is not None:
        task_payload["strategy_config_id"] = strategy_config_id
    return task_payload
```

```python
from app.db.strategy_config_repository import StrategyConfigRepository


def build_dispatcher_worker(self, *, redis_client: Redis) -> RedisNodeTaskDispatcher:
    ...
    strategy_repository = None
    if self.settings.database_enabled:
        session_factory = build_session_factory(self.settings.database_url)
        session = session_factory()
        task_repository = TaskRepository(session)
        strategy_repository = StrategyConfigRepository(session)
    return RedisNodeTaskDispatcher(
        ...,
        strategy_repository=strategy_repository,
        env_mode=self.settings.env_mode,
    )
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_redis_opportunity_flow.py tests/test_worker_service.py -k "strategy_config_id or strategy_repository" -v`
Expected: PASS，payload 带上 `strategy_config_id`，`dispatcher` worker 装配策略仓储

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/redis_flow.py app/runtime/worker_service.py tests/test_redis_opportunity_flow.py tests/test_worker_service.py
git commit -m "feat: wire strategy repository into dispatcher"
```

### Task 4: 文档、回归与远端验证说明

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_strategy_config_repository.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: 先补最后一组失败测试，锁定“无命中策略不创建任务”**

```python
@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_no_strategy_matches_opportunity():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "ETH/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "8.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-1")
    strategy_repository = FakeStrategyConfigRepository(
        [
            FakeStrategyConfig(
                id=11,
                target_quote_amount=80.0,
                open_spread_bps_threshold=15.0,
                symbol_scope_json=["BTC/USDT"],
                exchange_scope_json=["bitget", "gate"],
            )
        ]
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        strategy_repository=strategy_repository,
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert repository.created == []
    assert redis_client.xadds == []
```

- [ ] **Step 2: 运行目标测试确认失败**

Run: `python -m pytest tests/test_live_workers.py::test_dispatcher_skips_user_when_no_strategy_matches_opportunity -v`
Expected: FAIL，当前实现仍可能为用户建任务，或 `strategy_repository` 流程未覆盖空命中

- [ ] **Step 3: 完成最小实现并补运维文档**

```python
strategies = self._iter_matching_strategies(user_id=user_id, payload=payload)
if not strategies:
    continue
for strategy in strategies:
    ...
```

```md
## 策略配置接入 dispatcher 验证

1. 在主服务器数据库插入一名测试用户的两条启用策略：
   - 一条 `BTC/USDT` + `bitget/gate` + `target_quote_amount=80`
   - 一条全符号全交易所 + `open_spread_bps_threshold=20` + `target_quote_amount=35`
2. 重启 `furun-spot-dispatcher.service`
3. 向 `stream:spot_opps` 写入一条 `BTC/USDT`、`bitget/gate`、`spread_bps=25` 的机会
4. 用数据库查询确认生成两条 `arbitrage_tasks`，且 `strategy_config_id` 分别对应两条策略
5. 用 `journalctl -u furun-spot-dispatcher.service -n 100 --no-pager` 检查是否出现策略维度的 `control.rule.blocked` 或 `control.rule.resized`
```

- [ ] **Step 4: 跑完整相关回归**

Run: `python -m pytest tests/test_strategy_config_repository.py tests/test_live_workers.py tests/test_redis_opportunity_flow.py tests/test_worker_service.py -v`
Expected: PASS，策略仓储、多策略分发、payload 透传和 worker 装配全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add docs/ops/live-workers-systemd.md tests/test_strategy_config_repository.py tests/test_live_workers.py tests/test_redis_opportunity_flow.py tests/test_worker_service.py
git commit -m "docs: cover strategy config dispatcher validation"
```

## 自检

- Spec coverage:
  - `StrategyConfigRepository` 边界由 Task 1 覆盖
  - 多策略并行命中、独立建任务、控制规则透传、幂等键扩展由 Task 2 覆盖
  - `strategy_config_id` 进入节点流和 worker 装配由 Task 3 覆盖
  - 无命中跳过、运维说明和回归由 Task 4 覆盖
- Placeholder scan:
  - 已检查全文，无 `TBD`、`TODO`、`类似前一任务` 之类占位语
- Type consistency:
  - 全文统一使用 `StrategyConfigRepository`、`strategy_repository`、`strategy_config_id`、`build_node_execution_task_payload()`、`RedisNodeTaskDispatcher`
