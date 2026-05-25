# Dispatcher Account Auto Trade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `dispatcher` 在通过账户双边交易所覆盖过滤后，继续要求 `buy_exchange` 与 `sell_exchange` 两边账户都满足 `is_auto_trade_enabled=true`，否则跳过该机会。

**Architecture:** 保持现有 `DispatchUserRepository` 与 `AccountRepository` 的职责边界不变，不改候选用户发现层，也不改账户仓储的通用查询语义。实现上仅扩展 `live_workers.py` 中现有的账户覆盖 helper，让它同时返回 `available_exchanges` 与 `auto_trade_enabled_exchanges`，并在 `dispatcher.user.skipped` 上新增 `account_auto_trade_disabled` reason。

**Tech Stack:** Python 3.10+, SQLAlchemy, asyncio, redis.asyncio, pytest, pytest-asyncio

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - 扩展现有账户覆盖 helper，加入自动交易开关判断
  - 在 dispatcher 运行层增加 `account_auto_trade_disabled` 跳过语义
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 覆盖买边关闭、卖边关闭、双边开启、事件 payload 与现有 coverage reason 不回归
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - 补充账户自动交易开关联调与验收说明

### Task 1: 扩展账户覆盖 helper，加入自动交易开关判断

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定双边自动交易开启才放行**

```python
@pytest.mark.asyncio
async def test_dispatcher_requires_auto_trade_enabled_for_both_buy_and_sell_exchanges():
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
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", is_auto_trade_enabled=True),
                    FakeExchangeAccount(exchange="gate", is_auto_trade_enabled=True),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert [item.user_id for item in repository.created] == [42]
    assert len(redis_client.xadds) == 1
```

```python
@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_buy_exchange_auto_trade_is_disabled():
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
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", is_auto_trade_enabled=False),
                    FakeExchangeAccount(exchange="gate", is_auto_trade_enabled=True),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created == []
    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_auto_trade_disabled"
    assert skipped_event.payload["available_exchanges"] == ["bitget", "gate"]
    assert skipped_event.payload["auto_trade_enabled_exchanges"] == ["gate"]
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "requires_auto_trade_enabled_for_both_buy_and_sell_exchanges or buy_exchange_auto_trade_is_disabled" -v`
Expected: FAIL，当前实现只检查账户覆盖，不检查 `is_auto_trade_enabled`，会错误放行买边自动交易关闭的账户

- [ ] **Step 3: 写最小实现，扩展账户 helper**

```python
def _evaluate_account_exchange_coverage(*, payload: dict, accounts: list[Any]) -> dict[str, object]:
    available_exchanges = sorted(
        {str(account.exchange) for account in accounts if getattr(account, "exchange", None)}
    )
    auto_trade_enabled_exchanges = sorted(
        {
            str(account.exchange)
            for account in accounts
            if getattr(account, "exchange", None) and getattr(account, "is_auto_trade_enabled", True)
        }
    )
    buy_exchange = str(payload["buy_exchange"])
    sell_exchange = str(payload["sell_exchange"])
    has_exchange_coverage = (
        buy_exchange in available_exchanges and sell_exchange in available_exchanges
    )
    has_auto_trade_coverage = (
        buy_exchange in auto_trade_enabled_exchanges
        and sell_exchange in auto_trade_enabled_exchanges
    )
    return {
        "available_exchanges": available_exchanges,
        "auto_trade_enabled_exchanges": auto_trade_enabled_exchanges,
        "has_exchange_coverage": has_exchange_coverage,
        "has_auto_trade_coverage": has_auto_trade_coverage,
    }
```

```python
accounts = self._load_user_accounts(user_id=user_id)
coverage = _evaluate_account_exchange_coverage(payload=payload, accounts=accounts)
if not coverage["has_exchange_coverage"]:
    ...
    continue
if not coverage["has_auto_trade_coverage"]:
    if self.event_router is not None:
        await self.event_router.dispatch(
            _build_dispatch_user_event(
                event_type="dispatcher.user.skipped",
                region=self.region,
                payload={
                    "user_id": user_id,
                    "reason": "account_auto_trade_disabled",
                    "buy_exchange": str(payload["buy_exchange"]),
                    "sell_exchange": str(payload["sell_exchange"]),
                    "available_exchanges": coverage["available_exchanges"],
                    "auto_trade_enabled_exchanges": coverage["auto_trade_enabled_exchanges"],
                },
            )
        )
    continue
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "requires_auto_trade_enabled_for_both_buy_and_sell_exchanges or buy_exchange_auto_trade_is_disabled" -v`
Expected: PASS，双边自动交易开启时继续建任务，买边自动交易关闭时记录 `account_auto_trade_disabled`

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: require auto trade accounts in dispatcher"
```

### Task 2: 补卖边关闭与 coverage reason 不回归

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定卖边关闭与 coverage reason 仍然保留**

```python
@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_sell_exchange_auto_trade_is_disabled():
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
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", is_auto_trade_enabled=True),
                    FakeExchangeAccount(exchange="gate", is_auto_trade_enabled=False),
                ]
            }
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
    assert skipped_event.payload["reason"] == "account_auto_trade_disabled"
    assert skipped_event.payload["auto_trade_enabled_exchanges"] == ["bitget"]
```

```python
@pytest.mark.asyncio
async def test_dispatcher_keeps_account_exchange_coverage_missing_when_sell_exchange_account_absent():
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
            {"42": [FakeExchangeAccount(exchange="bitget", is_auto_trade_enabled=False)]}
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
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "sell_exchange_auto_trade_is_disabled or coverage_missing_when_sell_exchange_account_absent" -v`
Expected: FAIL，如果 reason 边界没分开，可能错误把“账户不存在”也归类成 `account_auto_trade_disabled`

- [ ] **Step 3: 写最小实现**

```python
coverage = _evaluate_account_exchange_coverage(payload=payload, accounts=accounts)
if not coverage["has_exchange_coverage"]:
    ...
    continue
if not coverage["has_auto_trade_coverage"]:
    ...
    continue
```

```python
"reason": "account_exchange_coverage_missing",
"available_exchanges": coverage["available_exchanges"],
"auto_trade_enabled_exchanges": coverage["auto_trade_enabled_exchanges"],
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "sell_exchange_auto_trade_is_disabled or coverage_missing_when_sell_exchange_account_absent" -v`
Expected: PASS，卖边关闭归类为 `account_auto_trade_disabled`，账户缺失仍归类为 `account_exchange_coverage_missing`

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "test: cover auto trade dispatcher edge cases"
```

### Task 3: 补控制事件边界与运维文档

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 先写失败测试，锁定自动交易关闭时不进入 control.rule 链**

```python
@pytest.mark.asyncio
async def test_dispatcher_account_auto_trade_skip_does_not_emit_control_rule_events():
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
    guard = FakeControlGuard(
        allowed=False,
        approved_notional=0.0,
        reason="reduce_only",
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", is_auto_trade_enabled=False),
                    FakeExchangeAccount(exchange="gate", is_auto_trade_enabled=True),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        control_guard=guard,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert guard.calls == []
    assert all(not event.event_type.startswith("control.rule.") for event in router.events)
```

- [ ] **Step 2: 运行目标测试确认失败**

Run: `python -m pytest tests/test_live_workers.py::test_dispatcher_account_auto_trade_skip_does_not_emit_control_rule_events -v`
Expected: FAIL，如果自动交易关闭仍会进入 control guard，则 `guard.calls` 非空或会出现 `control.rule.*`

- [ ] **Step 3: 完成最小实现并补运维文档**

```python
if not coverage["has_auto_trade_coverage"]:
    if self.event_router is not None:
        await self.event_router.dispatch(
            _build_dispatch_user_event(
                event_type="dispatcher.user.skipped",
                region=self.region,
                payload={
                    "user_id": user_id,
                    "reason": "account_auto_trade_disabled",
                    "buy_exchange": str(payload["buy_exchange"]),
                    "sell_exchange": str(payload["sell_exchange"]),
                    "available_exchanges": coverage["available_exchanges"],
                    "auto_trade_enabled_exchanges": coverage["auto_trade_enabled_exchanges"],
                },
            )
        )
    continue
```

```md
## Dispatcher Account Auto Trade Validation

1. 在主服务器数据库插入测试用户 42：
   - `bitget` 和 `gate` 两边账户都存在，且两边都 `is_auto_trade_enabled=1`
   - 有启用中的 `spot_futures` 策略
2. 再插入测试用户 99：
   - `bitget` 和 `gate` 两边账户都存在
   - 但仅 `bitget` 为 `is_auto_trade_enabled=1`，`gate` 为 `0`
3. 清空 `DISPATCH_USER_IDS`，重启 `furun-spot-dispatcher.service`
4. 向 `stream:spot_opps` 写入一条 `buy_exchange=bitget`、`sell_exchange=gate` 的机会
5. 查询数据库，确认只为用户 42 生成任务，不为用户 99 生成任务
6. 用 `journalctl -u furun-spot-dispatcher.service -n 120 --no-pager` 检查 `dispatcher.user.skipped` 是否带有 `account_auto_trade_disabled`
7. 把用户 99 的 `gate` 账户改成 `is_auto_trade_enabled=1` 后重新注入机会，确认其恢复进入任务链
```

- [ ] **Step 4: 跑完整相关回归**

Run: `python -m pytest tests/test_live_workers.py -v`
Expected: PASS，自动交易开关过滤、skip reason、control 边界与现有账户覆盖/策略/控制行为全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add docs/ops/live-workers-systemd.md tests/test_live_workers.py app/runtime/live_workers.py
git commit -m "docs: cover dispatcher account auto trade validation"
```

## 自检

- Spec coverage:
  - 双边自动交易放行规则与运行层 helper 由 Task 1 覆盖
  - `account_auto_trade_disabled` 与 `account_exchange_coverage_missing` 边界由 Task 2 覆盖
  - control 事件边界与运维文档由 Task 3 覆盖
- Placeholder scan:
  - 已检查全文，无 `TBD`、`TODO`、`implement later`、`类似前一任务`
- Type consistency:
  - 全文统一使用 `account_auto_trade_disabled`、`account_exchange_coverage_missing`、`auto_trade_enabled_exchanges`
