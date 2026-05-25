# Dispatcher Account Exchange Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `dispatcher` 在数据库自动发现候选用户之后，继续按当前机会的 `buy_exchange` 与 `sell_exchange` 做账户双边覆盖过滤，只有双边都具备启用账户的用户才进入策略匹配与任务创建链。

**Architecture:** 保持现有 `DispatchUserRepository` 的用户级发现边界不变，在 `live_workers.py` 运行层新增一层基于 `AccountRepository` 的双边交易所覆盖过滤。实现上为 `RedisNodeTaskDispatcher` 注入 `AccountRepository`，增加账户覆盖 helper 和 `dispatcher.user.skipped` 的新 reason，不改 executor 凭证来源、不改任务状态机。

**Tech Stack:** Python 3.10+, SQLAlchemy, asyncio, redis.asyncio, pytest, pytest-asyncio

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - 为 `RedisNodeTaskDispatcher` 增加账户双边覆盖过滤 helper
  - 在机会处理流程中接入账户覆盖校验
  - 复用 `dispatcher.user.skipped` 事件并新增 `account_exchange_coverage_missing` reason
- `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
  - 在 `database_enabled=True` 时为 `dispatcher` 装配 `AccountRepository`
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 覆盖双边覆盖通过、仅买边、仅卖边、跳过事件和白名单语义不回归
- `d:\old\FuRunSystemV4\tests\test_worker_service.py`
  - 覆盖 dispatcher worker 装配 `AccountRepository`
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - 补充账户级交易所覆盖远端联调与验收步骤

### Task 1: 为 dispatcher 增加账户双边覆盖过滤

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定双边覆盖通过与不足时跳过**

```python
@pytest.mark.asyncio
async def test_dispatcher_requires_accounts_for_both_buy_and_sell_exchanges():
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
    task_repository = FakeTaskRepository(task_uuid="task-seq")
    task_repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget"),
                    FakeExchangeAccount(exchange="gate"),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=task_repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert [item.user_id for item in task_repository.created] == [42]
    assert len(redis_client.xadds) == 1
    assert all(event.payload.get("reason") != "account_exchange_coverage_missing" for event in router.events)
```

```python
@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_account_exchange_coverage_is_incomplete():
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
    task_repository = FakeTaskRepository(task_uuid="task-seq")
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(exchange="bitget")]}
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=task_repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert task_repository.created == []
    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_exchange_coverage_missing"
    assert skipped_event.payload["available_exchanges"] == ["bitget"]
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "both_buy_and_sell_exchanges or account_exchange_coverage_is_incomplete" -v`
Expected: FAIL，提示 `RedisNodeTaskDispatcher` 不接受 `account_repository`，或当前仍会让只覆盖一边账户的用户进入后续链路

- [ ] **Step 3: 写最小实现，补账户覆盖 helper**

```python
def _has_account_exchange_coverage(
    *,
    payload: dict,
    accounts: list[Any],
) -> tuple[bool, list[str]]:
    available_exchanges = sorted(
        {str(account.exchange) for account in accounts if getattr(account, "exchange", None)}
    )
    buy_exchange = str(payload["buy_exchange"])
    sell_exchange = str(payload["sell_exchange"])
    covered = buy_exchange in available_exchanges and sell_exchange in available_exchanges
    return covered, available_exchanges
```

```python
class RedisNodeTaskDispatcher:
    def __init__(self, *, account_repository=None, **kwargs) -> None:
        ...
        self.account_repository = account_repository

    def _load_user_accounts(self, *, user_id: str) -> list[Any]:
        if self.account_repository is None:
            return []
        return self.account_repository.list_enabled_accounts(
            user_id=int(user_id),
            env_mode=self.env_mode,
        )
```

```python
for user_id in candidate_user_ids:
    node_id = await self.route_resolver.get_user_node(user_id)
    if node_id is None:
        ...
        continue

    accounts = self._load_user_accounts(user_id=user_id)
    covered, available_exchanges = _has_account_exchange_coverage(
        payload=payload,
        accounts=accounts,
    )
    if not covered:
        if self.event_router is not None:
            await self.event_router.dispatch(
                _build_dispatch_user_event(
                    event_type="dispatcher.user.skipped",
                    region=self.region,
                    payload={
                        "user_id": user_id,
                        "reason": "account_exchange_coverage_missing",
                        "buy_exchange": str(payload["buy_exchange"]),
                        "sell_exchange": str(payload["sell_exchange"]),
                        "available_exchanges": available_exchanges,
                    },
                )
            )
        continue

    strategies = self._iter_matching_strategies(
        user_id=user_id,
        payload=payload,
    )
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "both_buy_and_sell_exchanges or account_exchange_coverage_is_incomplete" -v`
Expected: PASS，双边覆盖通过时继续建任务，只覆盖一边时记录 skip 事件且不写节点流

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: require account exchange coverage in dispatcher"
```

### Task 2: 补白名单语义与仅卖边账户回归

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定仅卖边覆盖与白名单不回归**

```python
@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_only_sell_exchange_account_exists():
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
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(exchange="gate")]}
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_exchange_coverage_missing"
    assert skipped_event.payload["available_exchanges"] == ["gate"]
```

```python
@pytest.mark.asyncio
async def test_dispatcher_keeps_explicit_dispatch_user_ids_intersection_before_account_coverage():
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
    task_repository = FakeTaskRepository(task_uuid="task-seq")
    task_repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42", "99"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget"),
                    FakeExchangeAccount(exchange="gate"),
                ],
                "99": [
                    FakeExchangeAccount(exchange="bitget"),
                    FakeExchangeAccount(exchange="gate"),
                ],
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=task_repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert [item.user_id for item in task_repository.created] == [42]
    assert all(payload["user_id"] == "42" for _, payload in redis_client.xadds)
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "only_sell_exchange_account_exists or explicit_dispatch_user_ids_intersection_before_account_coverage" -v`
Expected: FAIL，若账户覆盖或白名单交集顺序处理不正确，会错误建任务或错误放行

- [ ] **Step 3: 写最小实现**

```python
candidate_user_ids = self._resolve_candidate_user_ids()
for user_id in candidate_user_ids:
    ...
    accounts = self._load_user_accounts(user_id=user_id)
    covered, available_exchanges = _has_account_exchange_coverage(
        payload=payload,
        accounts=accounts,
    )
    if not covered:
        ...
        continue
```

```python
available_exchanges = sorted(
    {str(account.exchange) for account in accounts if getattr(account, "exchange", None)}
)
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "only_sell_exchange_account_exists or explicit_dispatch_user_ids_intersection_before_account_coverage" -v`
Expected: PASS，仅卖边账户被跳过，白名单与数据库资格交集语义保持不变

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "test: cover account exchange coverage edge cases"
```

### Task 3: 在 worker_service 装配 AccountRepository

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: 先写失败测试，锁定 dispatcher worker 装配账户仓储**

```python
@pytest.mark.asyncio
async def test_default_worker_factory_builds_dispatcher_with_account_repository_when_database_enabled():
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

    assert worker.account_repository is not None
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_worker_service.py -k "account_repository_when_database_enabled" -v`
Expected: FAIL，提示 `worker.account_repository` 不存在或为 `None`

- [ ] **Step 3: 写最小实现**

```python
from app.db.account_repository import AccountRepository


def build_dispatcher_worker(self, *, redis_client: Redis) -> RedisNodeTaskDispatcher:
    ...
    account_repository = None
    if self.settings.database_enabled:
        session_factory = build_session_factory(self.settings.database_url)
        session = session_factory()
        task_repository = TaskRepository(session)
        strategy_repository = StrategyConfigRepository(session)
        dispatch_user_repository = DispatchUserRepository(session)
        account_repository = AccountRepository(session)
    return RedisNodeTaskDispatcher(
        ...,
        account_repository=account_repository,
        env_mode=self.settings.env_mode,
    )
```

- [ ] **Step 4: 重新运行测试**

Run: `python -m pytest tests/test_worker_service.py -k "account_repository_when_database_enabled" -v`
Expected: PASS，dispatcher worker 在数据库模式下成功装配 `AccountRepository`

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/worker_service.py tests/test_worker_service.py
git commit -m "feat: wire account repository into dispatcher"
```

### Task 4: 文档与完整相关回归

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: 先补最后一条失败测试，锁定账户覆盖不足时不影响已有控制规则事件断言**

```python
@pytest.mark.asyncio
async def test_dispatcher_account_exchange_coverage_skip_does_not_emit_control_rule_events():
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
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(exchange="bitget")]}
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert all(not event.event_type.startswith("control.rule.") for event in router.events)
```

- [ ] **Step 2: 运行目标测试确认失败**

Run: `python -m pytest tests/test_live_workers.py::test_dispatcher_account_exchange_coverage_skip_does_not_emit_control_rule_events -v`
Expected: FAIL，如果当前流程仍会在账户覆盖不足时继续进入 control guard，则会出现 `control.rule.*` 事件

- [ ] **Step 3: 完成最小实现并补运维文档**

```python
covered, available_exchanges = _has_account_exchange_coverage(
    payload=payload,
    accounts=accounts,
)
if not covered:
    ...
    continue

strategies = self._iter_matching_strategies(
    user_id=user_id,
    payload=payload,
)
```

```md
## Dispatcher Account Exchange Coverage Validation

1. 在主服务器数据库插入测试用户 42：
   - `testnet` 下有 `bitget` 和 `gate` 两条启用账户
   - 有启用中的 `spot_futures` 策略
2. 再插入测试用户 99：
   - 仅有 `bitget` 启用账户
   - 同样有启用中的 `spot_futures` 策略
3. 清空 `DISPATCH_USER_IDS`，重启 `furun-spot-dispatcher.service`
4. 向 `stream:spot_opps` 写入一条 `buy_exchange=bitget`、`sell_exchange=gate` 的机会
5. 查询数据库，确认只为用户 42 生成任务，不为用户 99 生成任务
6. 用 `journalctl -u furun-spot-dispatcher.service -n 120 --no-pager` 检查 `dispatcher.user.skipped` 是否带有 `account_exchange_coverage_missing`
7. 再把用户 99 的 `gate` 账户补齐，重复注入机会，确认其开始进入策略匹配与任务创建链
```

- [ ] **Step 4: 跑完整相关回归**

Run: `python -m pytest tests/test_live_workers.py tests/test_worker_service.py -v`
Expected: PASS，账户覆盖过滤、skip reason、worker 装配和现有策略/控制相关行为全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add docs/ops/live-workers-systemd.md tests/test_live_workers.py tests/test_worker_service.py
git commit -m "docs: cover dispatcher account exchange coverage validation"
```

## 自检

- Spec coverage:
  - 账户双边覆盖规则与运行层 helper 由 Task 1 覆盖
  - 仅卖边账户、白名单语义不回归由 Task 2 覆盖
  - `AccountRepository` 装配由 Task 3 覆盖
  - 控制事件边界、运维文档与完整回归由 Task 4 覆盖
- Placeholder scan:
  - 已检查全文，无 `TBD`、`TODO`、`implement later`、`类似前一任务`
- Type consistency:
  - 全文统一使用 `AccountRepository`、`account_repository`、`dispatcher.user.skipped`、`account_exchange_coverage_missing`
