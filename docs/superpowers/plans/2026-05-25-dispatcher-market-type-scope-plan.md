# Dispatcher Market Type Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `dispatcher` 在通过账户双边交易所覆盖与自动交易过滤后，继续要求 `buy_exchange` 与 `sell_exchange` 两边都存在至少一条 `market_type_scope` 与允许集合 `{spot, swap}` 有交集的账户，否则跳过该机会。

**Architecture:** 保持现有 `DispatchUserRepository` 与 `AccountRepository` 的职责边界不变，不改候选用户发现层，也不改账户仓储默认查询语义。实现上仅扩展 `app/runtime/live_workers.py` 里的账户覆盖 helper：增加 `market_type_scope` 解析、把每个交易所下声明的市场类型聚合进返回结果，并在 `dispatcher.user.skipped` 上新增 `account_market_type_scope_missing` reason 与调试字段。

**Tech Stack:** Python 3.10+, asyncio, redis.asyncio, SQLAlchemy, pytest, pytest-asyncio

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - 新增 `market_type_scope` 字符串解析 helper
  - 扩展账户覆盖 helper，返回每个交易所的市场类型信息
  - 在 dispatcher 主循环新增 `account_market_type_scope_missing` 跳过分支
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 扩展 `FakeExchangeAccount` 以支持 `market_type_scope`
  - 新增 helper 级测试，锁定 scope 解析与覆盖判断
  - 新增 dispatcher 级测试，锁定跳过 reason、payload、多账户放行与 control-rule 短路
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - 补充 `market_type_scope` 联调与验收说明

## 实现前检查

- 当前 `app/runtime/live_workers.py` 已存在 `_evaluate_account_exchange_coverage(...)`
- 当前 `tests/test_live_workers.py` 已存在：
  - `FakeExchangeAccount`
  - `FakeAccountRepository`
  - `test_dispatcher_requires_auto_trade_enabled_for_both_buy_and_sell_exchanges`
  - `test_dispatcher_skips_user_when_buy_exchange_auto_trade_is_disabled`
  - `test_dispatcher_skips_user_when_sell_exchange_auto_trade_is_disabled`
  - `test_dispatcher_account_auto_trade_skip_does_not_emit_control_rule_events`
- 当前 `docs/ops/live-workers-systemd.md` 已存在 `Dispatcher Account Auto Trade Validation` 小节，可沿着同一结构增加新的验证小节

### Task 1: 扩展测试假对象并锁定 `market_type_scope` 解析规则

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定 `market_type_scope` 解析行为**

```python
from app.runtime.live_workers import _parse_market_type_scope


def test_parse_market_type_scope_normalizes_and_filters_values():
    assert _parse_market_type_scope(" spot , swap , futures , ") == {"spot", "swap"}


def test_parse_market_type_scope_treats_empty_and_none_as_empty_set():
    assert _parse_market_type_scope("") == set()
    assert _parse_market_type_scope("   ") == set()
    assert _parse_market_type_scope(None) == set()
```

- [ ] **Step 2: 再写失败测试，锁定测试假账户支持 `market_type_scope`**

```python
def test_fake_exchange_account_defaults_market_type_scope_to_spot_swap():
    account = FakeExchangeAccount(exchange="bitget")

    assert account.exchange == "bitget"
    assert account.is_auto_trade_enabled is True
    assert account.market_type_scope == "spot,swap"


def test_fake_exchange_account_accepts_custom_market_type_scope():
    account = FakeExchangeAccount(
        exchange="gate",
        is_auto_trade_enabled=False,
        market_type_scope="swap",
    )

    assert account.market_type_scope == "swap"
    assert account.is_auto_trade_enabled is False
```

- [ ] **Step 3: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "parse_market_type_scope or fake_exchange_account_defaults_market_type_scope or fake_exchange_account_accepts_custom_market_type_scope" -v`

Expected: FAIL，因为 `_parse_market_type_scope` 还不存在，`FakeExchangeAccount` 也还没有 `market_type_scope` 参数与默认值。

- [ ] **Step 4: 写最小实现，补齐 helper 与 fake account**

```python
def _parse_market_type_scope(raw_value: Any) -> set[str]:
    if raw_value is None:
        return set()
    values = {
        part.strip().lower()
        for part in str(raw_value).split(",")
        if part.strip()
    }
    return {value for value in values if value in {"spot", "swap"}}
```

```python
class FakeExchangeAccount:
    def __init__(
        self,
        *,
        exchange: str,
        is_auto_trade_enabled: bool = True,
        market_type_scope: str = "spot,swap",
    ):
        self.exchange = exchange
        self.is_auto_trade_enabled = is_auto_trade_enabled
        self.market_type_scope = market_type_scope
```

- [ ] **Step 5: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "parse_market_type_scope or fake_exchange_account_defaults_market_type_scope or fake_exchange_account_accepts_custom_market_type_scope" -v`

Expected: PASS，确认 scope 解析和测试假对象已具备后续 TDD 基础。

- [ ] **Step 6: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "test: add market type scope parsing fixtures"
```

### Task 2: 扩展账户覆盖 helper，返回市场类型覆盖结果

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定 helper 对 scope 的聚合与放行语义**

```python
from app.runtime.live_workers import _evaluate_account_exchange_coverage


def test_evaluate_account_exchange_coverage_reports_market_type_scopes_by_exchange():
    payload = {"buy_exchange": "bitget", "sell_exchange": "gate"}
    accounts = [
        FakeExchangeAccount(exchange="bitget", market_type_scope="spot"),
        FakeExchangeAccount(exchange="gate", market_type_scope="swap"),
    ]

    coverage = _evaluate_account_exchange_coverage(payload=payload, accounts=accounts)

    assert coverage["available_exchanges"] == ["bitget", "gate"]
    assert coverage["auto_trade_enabled_exchanges"] == ["bitget", "gate"]
    assert coverage["market_type_scopes_by_exchange"] == {
        "bitget": ["spot"],
        "gate": ["swap"],
    }
    assert coverage["allowed_market_types"] == ["spot", "swap"]
    assert coverage["has_market_type_coverage"] is True
```

```python
def test_evaluate_account_exchange_coverage_fails_when_one_side_scope_is_empty():
    payload = {"buy_exchange": "bitget", "sell_exchange": "gate"}
    accounts = [
        FakeExchangeAccount(exchange="bitget", market_type_scope="spot"),
        FakeExchangeAccount(exchange="gate", market_type_scope=""),
    ]

    coverage = _evaluate_account_exchange_coverage(payload=payload, accounts=accounts)

    assert coverage["market_type_scopes_by_exchange"] == {
        "bitget": ["spot"],
        "gate": [],
    }
    assert coverage["has_exchange_coverage"] is True
    assert coverage["has_auto_trade_coverage"] is True
    assert coverage["has_market_type_coverage"] is False
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "reports_market_type_scopes_by_exchange or fails_when_one_side_scope_is_empty" -v`

Expected: FAIL，当前 helper 只返回 `available_exchanges`、`auto_trade_enabled_exchanges`、`has_exchange_coverage` 与 `has_auto_trade_coverage`。

- [ ] **Step 3: 写最小实现，扩展 helper 返回值**

```python
def _evaluate_account_exchange_coverage(
    *,
    payload: dict,
    accounts: list[Any],
) -> dict[str, object]:
    available_exchanges = sorted(
        {str(account.exchange) for account in accounts if getattr(account, "exchange", None)}
    )
    auto_trade_enabled_exchanges = sorted(
        {
            str(account.exchange)
            for account in accounts
            if getattr(account, "exchange", None)
            and getattr(account, "is_auto_trade_enabled", True)
        }
    )
    allowed_market_types = ["spot", "swap"]
    allowed_market_type_set = set(allowed_market_types)
    market_type_scopes_by_exchange: dict[str, list[str]] = {}
    market_type_covered_exchanges: set[str] = set()

    for account in accounts:
        exchange = getattr(account, "exchange", None)
        if not exchange:
            continue
        exchange_name = str(exchange)
        parsed_scope = sorted(
            _parse_market_type_scope(getattr(account, "market_type_scope", None))
        )
        existing_scope = set(market_type_scopes_by_exchange.get(exchange_name, []))
        existing_scope.update(parsed_scope)
        market_type_scopes_by_exchange[exchange_name] = sorted(existing_scope)
        if (
            getattr(account, "is_auto_trade_enabled", True)
            and allowed_market_type_set.intersection(parsed_scope)
        ):
            market_type_covered_exchanges.add(exchange_name)

    buy_exchange = str(payload["buy_exchange"])
    sell_exchange = str(payload["sell_exchange"])
    has_exchange_coverage = (
        buy_exchange in available_exchanges and sell_exchange in available_exchanges
    )
    has_auto_trade_coverage = (
        buy_exchange in auto_trade_enabled_exchanges
        and sell_exchange in auto_trade_enabled_exchanges
    )
    has_market_type_coverage = (
        buy_exchange in market_type_covered_exchanges
        and sell_exchange in market_type_covered_exchanges
    )
    return {
        "available_exchanges": available_exchanges,
        "auto_trade_enabled_exchanges": auto_trade_enabled_exchanges,
        "market_type_scopes_by_exchange": market_type_scopes_by_exchange,
        "allowed_market_types": allowed_market_types,
        "has_exchange_coverage": has_exchange_coverage,
        "has_auto_trade_coverage": has_auto_trade_coverage,
        "has_market_type_coverage": has_market_type_coverage,
    }
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "reports_market_type_scopes_by_exchange or fails_when_one_side_scope_is_empty" -v`

Expected: PASS，helper 已能聚合交易所下的 scope 信息并输出市场类型放行结论。

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: evaluate market type scope coverage in dispatcher"
```

### Task 3: 在 dispatcher 主循环新增 `account_market_type_scope_missing` 跳过分支

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定买边 scope 不匹配时跳过**

```python
@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_buy_exchange_market_type_scope_is_missing():
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
                    FakeExchangeAccount(exchange="bitget", market_type_scope=""),
                    FakeExchangeAccount(exchange="gate", market_type_scope="swap"),
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
    assert skipped_event.payload["reason"] == "account_market_type_scope_missing"
    assert skipped_event.payload["market_type_scopes_by_exchange"] == {
        "bitget": [],
        "gate": ["swap"],
    }
    assert skipped_event.payload["allowed_market_types"] == ["spot", "swap"]
```

- [ ] **Step 2: 再写失败测试，锁定卖边 scope 不匹配时也跳过**

```python
@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_sell_exchange_market_type_scope_is_missing():
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
                    FakeExchangeAccount(exchange="bitget", market_type_scope="spot"),
                    FakeExchangeAccount(exchange="gate", market_type_scope=""),
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

    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_market_type_scope_missing"
    assert skipped_event.payload["market_type_scopes_by_exchange"] == {
        "bitget": ["spot"],
        "gate": [],
    }
```

- [ ] **Step 3: 再写失败测试，锁定多账户记录时任意一条满足即可通过**

```python
@pytest.mark.asyncio
async def test_dispatcher_allows_user_when_one_account_per_exchange_matches_market_type_scope():
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
                    FakeExchangeAccount(exchange="bitget", market_type_scope=""),
                    FakeExchangeAccount(exchange="bitget", market_type_scope="spot"),
                    FakeExchangeAccount(exchange="gate", market_type_scope="swap"),
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

- [ ] **Step 4: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "market_type_scope_is_missing or matches_market_type_scope" -v`

Expected: FAIL，当前 dispatcher 还没有 `account_market_type_scope_missing` 分支，也不会在 payload 中写入 `market_type_scopes_by_exchange` 与 `allowed_market_types`。

- [ ] **Step 5: 写最小实现，把 helper 结果接入 dispatcher**

```python
coverage = _evaluate_account_exchange_coverage(
    payload=payload,
    accounts=accounts,
)
if not coverage["has_exchange_coverage"]:
    ...
    continue
if not coverage["has_auto_trade_coverage"]:
    ...
    continue
if not coverage["has_market_type_coverage"]:
    if self.event_router is not None:
        await self.event_router.dispatch(
            _build_dispatch_user_event(
                event_type="dispatcher.user.skipped",
                region=self.region,
                payload={
                    "user_id": user_id,
                    "reason": "account_market_type_scope_missing",
                    "buy_exchange": str(payload["buy_exchange"]),
                    "sell_exchange": str(payload["sell_exchange"]),
                    "available_exchanges": coverage["available_exchanges"],
                    "auto_trade_enabled_exchanges": coverage["auto_trade_enabled_exchanges"],
                    "market_type_scopes_by_exchange": coverage["market_type_scopes_by_exchange"],
                    "allowed_market_types": coverage["allowed_market_types"],
                },
            )
        )
    continue
```

- [ ] **Step 6: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "market_type_scope_is_missing or matches_market_type_scope" -v`

Expected: PASS，买边或卖边 scope 不匹配时被跳过，多账户记录场景下只要任意一条可接受账户存在即可通过。

- [ ] **Step 7: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: skip dispatcher users on missing market type scope"
```

### Task 4: 补控制规则短路回归与运维验证文档

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 先写失败测试，锁定 `market_type_scope` 失败不会进入 control guard**

```python
@pytest.mark.asyncio
async def test_dispatcher_market_type_scope_skip_does_not_emit_control_rule_events():
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
                    FakeExchangeAccount(exchange="bitget", market_type_scope=""),
                    FakeExchangeAccount(exchange="gate", market_type_scope="swap"),
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
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_market_type_scope_missing"
    assert all(not event.event_type.startswith("control.rule.") for event in router.events)
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "market_type_scope_skip_does_not_emit_control_rule_events" -v`

Expected: FAIL，如果 dispatcher 还没有在 market type scope 阶段短路，`guard.calls` 会非空或缺少对应 reason。

- [ ] **Step 3: 用最小代码修正并跑完整回归**

```python
if not coverage["has_market_type_coverage"]:
    ...
    continue

decision = self.control_guard.evaluate(
    strategy_id=strategy_id,
    user_id=int(user_id),
    symbol=str(payload["symbol"]),
    ...
)
```

Run: `python -m pytest tests/test_live_workers.py -k "market_type_scope or auto_trade or account_exchange_coverage" -v`

Expected: PASS，新增市场类型过滤不回归现有 `auto_trade` 与 `account_exchange_coverage` 语义。

- [ ] **Step 4: 更新运维文档，新增 `Dispatcher Market Type Scope Validation` 小节**

```md
### Dispatcher Market Type Scope Validation

1. 在 canary 用户的 `buy_exchange` 与 `sell_exchange` 账户都已启用且自动交易开启的前提下，先把其中一边账户的 `market_type_scope` 置空或改成非 `spot/swap` 值。
2. 重启 `furun-spot-dispatcher.service` 后注入一条专用机会：
   - `symbol=BTC/USDT`
   - `buy_exchange=bitget`
   - `sell_exchange=gate`
3. 预期结果：
   - 不创建新的 `DISPATCHED` 任务
   - 不写新的节点执行 payload
   - `dispatcher.user.skipped` 的 `payload.reason` 为 `account_market_type_scope_missing`
   - `payload.market_type_scopes_by_exchange` 显示各交易所当前声明的 scope
   - `payload.allowed_market_types` 为 `["spot", "swap"]`
   - 不出现新的 `control.rule.blocked` / `control.rule.resized`
4. 将缺失一侧的 `market_type_scope` 恢复为 `spot,swap` 后再次注入同类机会，预期恢复创建任务与写节点流。
```

- [ ] **Step 5: 重新运行测试并校验文档文件**

Run: `python -m pytest tests/test_live_workers.py -k "market_type_scope or auto_trade or account_exchange_coverage" -v`

Expected: PASS，新增测试与既有账户过滤链同时通过。

- [ ] **Step 6: 提交这一小步**

```bash
git add tests/test_live_workers.py docs/ops/live-workers-systemd.md
git commit -m "docs: add dispatcher market type scope validation"
```

### Task 5: 全量验收与收尾检查

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 运行针对 dispatcher 的完整测试组**

Run: `python -m pytest tests/test_live_workers.py -v`

Expected: PASS，`market_type_scope` 新增逻辑与现有 scanner / dispatcher / control guard 测试全部兼容。

- [ ] **Step 2: 如有数据库或 payload 相关回归疑虑，补跑相邻测试**

Run: `python -m pytest tests/test_worker_service.py tests/test_redis_opportunity_flow.py -v`

Expected: PASS，确认本轮没有误伤 worker 组装和任务 payload 基础行为。

- [ ] **Step 3: 检查最近编辑文件的诊断信息**

Run diagnostics for:
- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

Expected: 无新的语法错误、导入错误或明显静态检查错误。

- [ ] **Step 4: 做最终提交**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py docs/ops/live-workers-systemd.md
git commit -m "feat: enforce market type scope in dispatcher"
```

## 自检结论

- `spec` 覆盖情况：
  - `market_type_scope` 运行层接入：Task 2, Task 3
  - 粗粒度允许集合 `{spot, swap}`：Task 2
  - 新 skip reason 与 payload：Task 3
  - 不进入 control rule：Task 4
  - 测试与运维验证：Task 1, Task 2, Task 3, Task 4, Task 5
- 无占位符、无 `TODO/TBD`、无未定义步骤引用
- 函数名、reason 名、字段名在各任务中保持一致：
  - `_parse_market_type_scope`
  - `_evaluate_account_exchange_coverage`
  - `account_market_type_scope_missing`
  - `market_type_scopes_by_exchange`
  - `allowed_market_types`
