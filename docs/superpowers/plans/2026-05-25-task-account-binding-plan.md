# Task Account Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `dispatcher` 在创建任务时选定 `buy_account_id / sell_account_id`，把它们同时写入 `arbitrage_tasks` 和节点 payload，并让 `executor` 改为按这两个绑定账户 ID 读取账户而不是再次选账户。

**Architecture:** 这次改造围绕“账户选择只发生一次”展开：先扩展 `ArbitrageTask`/`TaskRepository` 支持保存绑定账户 ID，再把 `dispatcher` 当前的账户 coverage 逻辑升级为“过滤 + 选定账户”，并把结果写进任务与 payload，最后把 `executor` 的账户真值解析器从“按条件选择账户”切到“按绑定 ID 加载并校验账户”。`dispatcher` 仍负责账户选择，`executor` 只负责 binding 校验、凭证解密与代理装配。

**Tech Stack:** Python 3.10+, asyncio, redis.asyncio, SQLAlchemy ORM, pytest, pytest-asyncio

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\models.py`
  - 给 `ArbitrageTask` 增加 `buy_account_id`、`sell_account_id`
- `d:\old\FuRunSystemV4\app\db\task_repository.py`
  - 扩展 `ArbitrageTaskCreate` 与 `create_task()`，支持持久化绑定账户
- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - 把 dispatcher 的账户 coverage 逻辑升级为“过滤 + 选定账户”
  - 创建任务和写节点 payload 时透传 `buy_account_id/sell_account_id`
  - executor 侧消费时优先使用 payload 中的绑定账户
- `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
  - 扩展 `build_node_execution_task_payload()`，把绑定账户 ID 写入 stream payload
- `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
  - 新增按 `buy_account_id/sell_account_id` 加载并校验账户的入口
  - 区分 binding 失败与普通账户真值失败
- `d:\old\FuRunSystemV4\tests\test_task_repository.py`
  - 锁定任务表持久化 binding 字段
- `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
  - 锁定节点 payload 增加 binding 字段
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 锁定 dispatcher 选定账户、任务落库、节点 payload 透传、executor 按 binding 账户执行
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - 增加任务绑定账户的联调与远端验收说明

## 实现前检查

- 当前 `ArbitrageTask` 还没有 `buy_account_id/sell_account_id` 字段，位置：[models.py](file:///d:/old/FuRunSystemV4/models.py)
- 当前 `ArbitrageTaskCreate` 还没有 binding 字段，位置：[task_repository.py](file:///d:/old/FuRunSystemV4/app/db/task_repository.py)
- 当前 `build_node_execution_task_payload()` 只追加：
  - `user_id`
  - `source_message_id`
  - `task_uuid`
  - `strategy_config_id`
  位置：[redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py)
- 当前 dispatcher 只做 coverage，不选具体账户，位置：[live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py)
- 当前 executor 仍通过 `ExecutorAccountTruthResolver.resolve_accounts()` 按 `user_id + exchange + env_mode` 做选择，位置：[executor_account_truth.py](file:///d:/old/FuRunSystemV4/app/runtime/executor_account_truth.py)

### Task 1: 扩展任务模型与仓储，持久化 `buy_account_id/sell_account_id`

**Files:**
- Modify: `d:\old\FuRunSystemV4\models.py`
- Modify: `d:\old\FuRunSystemV4\app\db\task_repository.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_task_repository.py`

- [ ] **Step 1: 先写失败测试，锁定仓储会保存绑定账户 ID**

```python
def test_task_repository_persists_bound_account_ids():
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
            buy_account_id=101,
            sell_account_id=202,
            opportunity_id="opp-1",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="bitget",
            derivative_exchange="gate",
            target_notional=100.0,
            expected_spread_bps=120.0,
            expected_funding_bps=0.0,
            idempotency_key="idem-1",
            home_region="main",
        )
    )

    refreshed = repository.get_by_task_uuid(task.task_uuid)

    assert refreshed is not None
    assert refreshed.buy_account_id == 101
    assert refreshed.sell_account_id == 202
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_task_repository.py -k "persists_bound_account_ids" -v`

Expected: FAIL，因为 `ArbitrageTaskCreate` 和 `ArbitrageTask` 目前都还没有 `buy_account_id/sell_account_id` 字段。

- [ ] **Step 3: 写最小实现，扩展模型与仓储入参**

```python
class ArbitrageTask(Base):
    __tablename__ = "arbitrage_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_configs.id"),
        nullable=True,
    )
    buy_account_id: Mapped[int | None] = mapped_column(nullable=True)
    sell_account_id: Mapped[int | None] = mapped_column(nullable=True)
    opportunity_id: Mapped[str] = mapped_column(String(128), index=True)
```

```python
@dataclass(slots=True)
class ArbitrageTaskCreate:
    task_uuid: str
    user_id: int
    strategy_config_id: int | None
    buy_account_id: int | None
    sell_account_id: int | None
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
```

```python
task = ArbitrageTask(
    task_uuid=data.task_uuid,
    user_id=data.user_id,
    strategy_config_id=data.strategy_config_id,
    buy_account_id=data.buy_account_id,
    sell_account_id=data.sell_account_id,
    opportunity_id=data.opportunity_id,
    env_mode=data.env_mode,
    task_type=data.task_type,
    symbol=data.symbol,
    spot_exchange=data.spot_exchange,
    derivative_exchange=data.derivative_exchange,
    target_notional=data.target_notional,
    expected_spread_bps=data.expected_spread_bps,
    expected_funding_bps=data.expected_funding_bps,
    idempotency_key=data.idempotency_key,
    home_region=data.home_region,
)
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_task_repository.py -k "persists_bound_account_ids or creates_and_updates_task_status" -v`

Expected: PASS，binding 字段被保存且不回归现有任务状态测试。

- [ ] **Step 5: 提交这一小步**

```bash
git add models.py app/db/task_repository.py tests/test_task_repository.py
git commit -m "feat: persist bound account ids on arbitrage tasks"
```

### Task 2: 扩展节点 payload，透传 `buy_account_id/sell_account_id`

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`

- [ ] **Step 1: 先写失败测试，锁定 payload 包含 binding 字段**

```python
def test_build_node_execution_task_payload_includes_bound_account_ids():
    task_payload = build_node_execution_task_payload(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
        },
        user_id="42",
        source_message_id="1-0",
        task_uuid="task-1",
        buy_account_id="101",
        sell_account_id="202",
    )

    assert task_payload["task_uuid"] == "task-1"
    assert task_payload["buy_account_id"] == "101"
    assert task_payload["sell_account_id"] == "202"
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_redis_opportunity_flow.py -k "includes_bound_account_ids" -v`

Expected: FAIL，因为 `build_node_execution_task_payload()` 还不接受这两个参数。

- [ ] **Step 3: 写最小实现，扩展 payload builder**

```python
def build_node_execution_task_payload(
    payload: dict,
    *,
    user_id: str,
    source_message_id: str,
    task_uuid: str,
    strategy_config_id: str | None = None,
    buy_account_id: str | None = None,
    sell_account_id: str | None = None,
) -> dict[str, str]:
    task_payload = {key: str(value) for key, value in payload.items()}
    task_payload["user_id"] = user_id
    task_payload["source_message_id"] = source_message_id
    task_payload["task_uuid"] = task_uuid
    if strategy_config_id is not None:
        task_payload["strategy_config_id"] = strategy_config_id
    if buy_account_id is not None:
        task_payload["buy_account_id"] = buy_account_id
    if sell_account_id is not None:
        task_payload["sell_account_id"] = sell_account_id
    return task_payload
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_redis_opportunity_flow.py -k "includes_bound_account_ids or strategy_config_id" -v`

Expected: PASS，binding 字段进入 payload，且不回归现有 `strategy_config_id` 透传语义。

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/redis_flow.py tests/test_redis_opportunity_flow.py
git commit -m "feat: include bound account ids in node payload"
```

### Task 3: 把 dispatcher 的账户过滤升级为“过滤 + 选定账户”

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定 dispatcher 会选定账户并写入任务**

```python
@pytest.mark.asyncio
async def test_dispatcher_selects_bound_accounts_and_persists_them_on_task():
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
    repository = FakeTaskRepository(task_uuid="task-1")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", account_id=101),
                    FakeExchangeAccount(exchange="gate", account_id=202),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created[0].buy_account_id == 101
    assert repository.created[0].sell_account_id == 202
```

- [ ] **Step 2: 再写失败测试，锁定节点 payload 也会带 binding 字段**

```python
@pytest.mark.asyncio
async def test_dispatcher_publishes_bound_account_ids_to_node_stream():
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
    repository = FakeTaskRepository(task_uuid="task-1")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", account_id=101),
                    FakeExchangeAccount(exchange="gate", account_id=202),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        region="main",
    )
    await dispatcher.run(max_iterations=1)

    assert redis_client.xadds[0][1]["buy_account_id"] == "101"
    assert redis_client.xadds[0][1]["sell_account_id"] == "202"
```

- [ ] **Step 3: 再写失败测试，锁定多账户场景按最小 id 选定**

```python
@pytest.mark.asyncio
async def test_dispatcher_selects_lowest_account_id_per_exchange_for_binding():
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
    repository = FakeTaskRepository(task_uuid="task-1")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
    account_repository=FakeAccountRepository(
        {
            "42": [
                FakeExchangeAccount(exchange="bitget", account_id=305),
                FakeExchangeAccount(exchange="bitget", account_id=101),
                FakeExchangeAccount(exchange="gate", account_id=202),
                FakeExchangeAccount(exchange="gate", account_id=404),
            ]
        }
    ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        region="main",
    )
    await dispatcher.run(max_iterations=1)

    assert repository.created[0].buy_account_id == 101
    assert repository.created[0].sell_account_id == 202
```

- [ ] **Step 4: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "bound_accounts and dispatcher" -v`

Expected: FAIL，因为当前 dispatcher 还只做 coverage，不会把具体账户写进任务或 payload。

- [ ] **Step 5: 写最小实现，新增 dispatcher 侧 binding 选择**

```python
def _select_bound_accounts(
    *,
    payload: dict,
    accounts: list[Any],
    dispatcher_region: str,
) -> dict[str, Any] | None:
    buy_exchange = str(payload["buy_exchange"])
    sell_exchange = str(payload["sell_exchange"])
    buy_candidates = _iter_eligible_accounts_for_exchange(
        accounts=accounts,
        exchange=buy_exchange,
        dispatcher_region=dispatcher_region,
    )
    sell_candidates = _iter_eligible_accounts_for_exchange(
        accounts=accounts,
        exchange=sell_exchange,
        dispatcher_region=dispatcher_region,
    )
    if not buy_candidates or not sell_candidates:
        return None
    return {
        "buy_account": sorted(buy_candidates, key=lambda item: int(getattr(item, "id", 0)))[0],
        "sell_account": sorted(sell_candidates, key=lambda item: int(getattr(item, "id", 0)))[0],
    }
```

```python
binding = _select_bound_accounts(
    payload=payload,
    accounts=accounts,
    dispatcher_region=self.region,
)
if binding is None:
    continue
task_record = self._create_database_task(
    user_id=user_id,
    message_id=message_id,
    payload=payload,
    strategy=strategy,
    requested_notional=requested_notional,
    buy_account_id=int(getattr(binding["buy_account"], "id")),
    sell_account_id=int(getattr(binding["sell_account"], "id")),
)
task_payload = build_node_execution_task_payload(
    payload,
    user_id=user_id,
    source_message_id=message_id,
    task_uuid=task_uuid,
    strategy_config_id=strategy_config_id,
    buy_account_id=str(int(getattr(binding["buy_account"], "id"))),
    sell_account_id=str(int(getattr(binding["sell_account"], "id"))),
)
```

- [ ] **Step 6: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "bound_accounts and dispatcher" -v`

Expected: PASS，dispatcher 已能稳定选定账户并把 binding 写入任务与节点流。

- [ ] **Step 7: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: bind accounts when dispatcher creates tasks"
```

### Task 4: 改造 executor，按绑定账户 ID 读取账户并校验 binding

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定 executor 按 binding 账户 ID 读取账户**

```python
def test_executor_account_truth_resolver_loads_accounts_by_binding_ids():
    accounts = [
        FakeExchangeAccount(exchange="bitget", account_id=101, api_key_ciphertext="ak-1", secret_ciphertext="sk-1"),
        FakeExchangeAccount(exchange="gate", account_id=202, api_key_ciphertext="ak-2", secret_ciphertext="sk-2"),
    ]
    cipher = FakeSecretCipher(
        {
            "ak-1": "plain-ak-1",
            "sk-1": "plain-sk-1",
            "ak-2": "plain-ak-2",
            "sk-2": "plain-sk-2",
        }
    )
    resolver = ExecutorAccountTruthResolver(secret_cipher=cipher)

    resolved = resolver.resolve_bound_accounts(
        accounts=accounts,
        user_id="42",
        buy_account_id="101",
        sell_account_id="202",
        buy_exchange="bitget",
        sell_exchange="gate",
        env_mode="testnet",
        region="main",
    )

    assert resolved["bitget"].account_id == 101
    assert resolved["gate"].account_id == 202
```

- [ ] **Step 2: 再写失败测试，锁定 binding 失效时任务失败原因**

```python
def test_executor_account_truth_resolver_raises_binding_not_found():
    resolver = ExecutorAccountTruthResolver(secret_cipher=FakeSecretCipher({}))

    with pytest.raises(ExecutorAccountTruthError) as exc_info:
        resolver.resolve_bound_accounts(
            accounts=[],
            user_id="42",
            buy_account_id="101",
            sell_account_id="202",
            buy_exchange="bitget",
            sell_exchange="gate",
            env_mode="testnet",
            region="main",
        )

    assert exc_info.value.reason == "executor_account_binding_not_found"
```

- [ ] **Step 3: 再写失败测试，锁定 consumer 优先走 binding 账户路径**

```python
@pytest.mark.asyncio
async def test_executor_consumer_uses_bound_account_ids_from_payload():
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
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "buy_account_id": "101",
                            "sell_account_id": "202",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    dispatcher = FakeResolvedDispatcher()
    resolver = FakeExecutorAccountTruthResolver(
        {
            "bitget": {"credentials": "cred-a", "proxies": {}},
            "gate": {"credentials": "cred-b", "proxies": {}},
        }
    )
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=dispatcher,
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        account_repository=FakeAccountRepository({"42": [FakeExchangeAccount(exchange="bitget"), FakeExchangeAccount(exchange="gate")]}),
        account_truth_resolver=resolver,
        env_mode="testnet",
        block_ms=1,
        region="main",
    )

    await consumer.run(max_iterations=1)

    assert resolver.bound_calls[0]["buy_account_id"] == "101"
    assert resolver.bound_calls[0]["sell_account_id"] == "202"
```

- [ ] **Step 4: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "binding_not_found or bound_account_ids_from_payload or loads_accounts_by_binding_ids" -v`

Expected: FAIL，因为当前 resolver 还没有 `resolve_bound_accounts()`，consumer 也还没有优先读取 payload 中的 binding 账户。

- [ ] **Step 5: 写最小实现，把 executor 切到 binding 账户读取**

```python
class ExecutorAccountTruthResolver:
    def resolve_bound_accounts(
        self,
        *,
        accounts: list[object],
        user_id: str,
        buy_account_id: str,
        sell_account_id: str,
        buy_exchange: str,
        sell_exchange: str,
        env_mode: str,
        region: str,
    ) -> dict[str, ResolvedExecutionAccount]:
        return {
            buy_exchange: self._load_bound_account(
                accounts=accounts,
                user_id=user_id,
                exchange=buy_exchange,
                account_id=buy_account_id,
                env_mode=env_mode,
                region=region,
            ),
            sell_exchange: self._load_bound_account(
                accounts=accounts,
                user_id=user_id,
                exchange=sell_exchange,
                account_id=sell_account_id,
                env_mode=env_mode,
                region=region,
            ),
        }
```

```python
if "buy_account_id" in payload and "sell_account_id" in payload:
    execution_accounts_by_exchange = self.account_truth_resolver.resolve_bound_accounts(
        accounts=list(accounts or []),
        user_id=str(payload["user_id"]),
        buy_account_id=str(payload["buy_account_id"]),
        sell_account_id=str(payload["sell_account_id"]),
        buy_exchange=str(payload["buy_exchange"]),
        sell_exchange=str(payload["sell_exchange"]),
        env_mode=self.env_mode,
        region=self.region,
    )
else:
    execution_accounts_by_exchange = self.account_truth_resolver.resolve_accounts(
        accounts=list(accounts or []),
        user_id=str(payload["user_id"]),
        buy_exchange=str(payload["buy_exchange"]),
        sell_exchange=str(payload["sell_exchange"]),
        env_mode=self.env_mode,
        region=self.region,
    )
```

- [ ] **Step 6: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "binding_not_found or bound_account_ids_from_payload or loads_accounts_by_binding_ids" -v`

Expected: PASS，executor 已优先按 binding 账户执行。

- [ ] **Step 7: 提交这一小步**

```bash
git add app/runtime/executor_account_truth.py app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: resolve executor accounts from task bindings"
```

### Task 5: 补 binding 失败回归与运维文档

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 先写失败测试，锁定 binding 无效时不进入 spot service**

```python
@pytest.mark.asyncio
async def test_executor_binding_failure_does_not_call_spot_service():
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
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "buy_account_id": "101",
                            "sell_account_id": "202",
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
        task_repository=FakeTaskRepository(task_uuid="task-1"),
        account_repository=FakeAccountRepository({"42": []}),
        account_truth_resolver=FailingExecutorAccountTruthResolver(
            reason="executor_account_binding_not_found"
        ),
        env_mode="testnet",
        block_ms=1,
        region="main",
    )

    processed = await consumer.run(max_iterations=1)

    assert processed == 0
    assert service.calls == []
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "binding_failure_does_not_call_spot_service" -v`

Expected: FAIL，如果 consumer 仍没把 binding reason 正确落到失败路径，或仍然会继续进入 dispatcher/spot service。

- [ ] **Step 3: 用最小代码修正并跑回归**

```python
except ExecutorAccountTruthError as exc:
    if task_uuid is not None and self.task_repository is not None:
        self.task_repository.mark_failed(task_uuid, reason=exc.reason)
    return processed
```

Run: `python -m pytest tests/test_live_workers.py -k "binding_failure or executor_account_truth or control_rule" -v`

Expected: PASS，binding 失败不会进入执行，且失败原因按 reason code 落库。

- [ ] **Step 4: 更新运维文档，新增 `Task Account Binding Validation` 小节**

```md
### Task Account Binding Validation

1. 为 canary 用户准备可执行的 `bitget` 与 `gate` 账户，并确认 dispatcher 过滤链全部通过。
2. 注入一条专用机会，确认：
   - `arbitrage_tasks.buy_account_id` 与 `sell_account_id` 已落库
   - 节点流 payload 同时包含 `buy_account_id` 与 `sell_account_id`
3. 让 executor 消费该任务，确认其按绑定账户执行成功。
4. 删除或禁用其中一个绑定账户后再次消费同类任务，预期：
   - 任务进入 `FAILED`
   - `status_reason` 为 `executor_account_binding_not_found` 或 `executor_account_binding_invalid`
   - 不出现静默重选账户
5. 恢复绑定账户后再次注入同类任务，预期重新成功。
```

- [ ] **Step 5: 重新运行测试并校验文档文件**

Run: `python -m pytest tests/test_live_workers.py -k "binding_failure or executor_account_truth or dispatcher_selects_bound_accounts" -v`

Expected: PASS，binding 失败与既有账户真值链同时通过。

- [ ] **Step 6: 提交这一小步**

```bash
git add tests/test_live_workers.py docs/ops/live-workers-systemd.md
git commit -m "docs: add task account binding validation"
```

### Task 6: 全量验收与收尾检查

**Files:**
- Modify: `d:\old\FuRunSystemV4\models.py`
- Modify: `d:\old\FuRunSystemV4\app\db\task_repository.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_task_repository.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 运行主链测试组**

Run: `python -m pytest tests/test_task_repository.py tests/test_redis_opportunity_flow.py tests/test_live_workers.py -v`

Expected: PASS，任务 schema、dispatcher、payload、executor binding 主链全部兼容。

- [ ] **Step 2: 补跑相邻回归**

Run: `python -m pytest tests/test_worker_service.py tests/test_dispatch_user_repository.py tests/test_account_repository.py -v`

Expected: PASS，本轮不回归 worker 注入与账户读取基础行为。

- [ ] **Step 3: 检查最近编辑文件的诊断信息**

Run diagnostics for:
- `d:\old\FuRunSystemV4\models.py`
- `d:\old\FuRunSystemV4\app\db\task_repository.py`
- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
- `d:\old\FuRunSystemV4\tests\test_task_repository.py`
- `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

Expected: 无新的语法错误、导入错误或明显静态检查错误。

- [ ] **Step 4: 做最终提交**

```bash
git add models.py app/db/task_repository.py app/runtime/live_workers.py app/runtime/redis_flow.py app/runtime/executor_account_truth.py tests/test_task_repository.py tests/test_redis_opportunity_flow.py tests/test_live_workers.py docs/ops/live-workers-systemd.md
git commit -m "feat: bind buy and sell accounts to arbitrage tasks"
```

## 自检结论

- `spec` 覆盖情况：
  - dispatcher 负责选定账户：Task 3
  - `arbitrage_tasks` 保存 binding 字段：Task 1
  - 节点 payload 保存 binding 字段：Task 2
  - executor 按 binding 账户执行：Task 4
  - binding 失败语义与运维验证：Task 5
- 无占位符、无 `TODO/TBD`、无未定义步骤引用
- 字段名与 reason 名在各任务中保持一致：
  - `buy_account_id`
  - `sell_account_id`
  - `executor_account_binding_not_found`
  - `executor_account_binding_invalid`
