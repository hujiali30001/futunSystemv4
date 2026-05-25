# Dispatcher Account Region Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `dispatcher` 在通过账户双边交易所覆盖、自动交易过滤与 `market_type_scope` 过滤后，继续要求 `buy_exchange` 与 `sell_exchange` 两边都存在至少一条 `account_region` 与当前 `dispatcher.region` 兼容的账户，否则跳过该机会。

**Architecture:** 保持现有 `DispatchUserRepository`、`AccountRepository` 与 Redis `user-node` 路由真值不变，不改候选用户发现层，也不改账户仓储默认查询语义。实现上仅扩展 `app/runtime/live_workers.py` 里的账户 coverage helper：增加 `account_region` 归一化与兼容判断、把每个交易所下声明的区域聚合进返回结果，并在 `dispatcher.user.skipped` 上新增 `account_region_mismatch` reason 与调试字段。

**Tech Stack:** Python 3.10+, asyncio, redis.asyncio, SQLAlchemy, pytest, pytest-asyncio

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - 新增 `account_region` 字符串归一化 helper
  - 扩展账户 coverage helper，返回每个交易所的区域信息与区域放行结果
  - 在 dispatcher 主循环新增 `account_region_mismatch` 跳过分支
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 扩展 `FakeExchangeAccount` 以支持 `account_region`
  - 新增 helper 级测试，锁定区域归一化与兼容判断
  - 新增 dispatcher 级测试，锁定跳过 reason、payload、多账户放行与 control-rule 短路
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - 补充 `account_region` 联调与验收说明

## 实现前检查

- 当前 `app/runtime/live_workers.py` 已存在：
  - `_parse_market_type_scope(...)`
  - `_evaluate_account_exchange_coverage(...)`
- 当前 `tests/test_live_workers.py` 已存在：
  - `FakeExchangeAccount`
  - `test_evaluate_account_exchange_coverage_reports_market_type_scopes_by_exchange`
  - `test_dispatcher_skips_user_when_buy_exchange_market_type_scope_is_missing`
  - `test_dispatcher_market_type_scope_skip_does_not_emit_control_rule_events`
- 当前 `docs/ops/live-workers-systemd.md` 已存在：
  - `Dispatcher Account Auto Trade Validation`
  - `Dispatcher Market Type Scope Validation`

### Task 1: 扩展测试假对象并锁定 `account_region` 归一化规则

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定 `account_region` 归一化行为**

```python
from app.runtime.live_workers import _normalize_account_region


def test_normalize_account_region_normalizes_case_and_whitespace():
    assert _normalize_account_region(" Main ") == "main"


def test_normalize_account_region_treats_empty_and_none_as_default():
    assert _normalize_account_region("") == "default"
    assert _normalize_account_region("   ") == "default"
    assert _normalize_account_region(None) == "default"
```

- [ ] **Step 2: 再写失败测试，锁定测试假账户支持 `account_region`**

```python
def test_fake_exchange_account_defaults_account_region_to_default():
    account = FakeExchangeAccount(exchange="bitget")

    assert account.account_region == "default"
    assert account.market_type_scope == "spot,swap"


def test_fake_exchange_account_accepts_custom_account_region():
    account = FakeExchangeAccount(
        exchange="gate",
        account_region="main",
        market_type_scope="swap",
    )

    assert account.account_region == "main"
    assert account.market_type_scope == "swap"
```

- [ ] **Step 3: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "normalize_account_region or fake_exchange_account_defaults_account_region or fake_exchange_account_accepts_custom_account_region" -v`

Expected: FAIL，因为 `_normalize_account_region` 还不存在，`FakeExchangeAccount` 也还没有 `account_region` 参数与默认值。

- [ ] **Step 4: 写最小实现，补齐 helper 与 fake account**

```python
def _normalize_account_region(raw_value: Any) -> str:
    if raw_value is None:
        return "default"
    normalized = str(raw_value).strip().lower()
    return normalized or "default"
```

```python
class FakeExchangeAccount:
    def __init__(
        self,
        *,
        exchange: str,
        is_auto_trade_enabled: bool = True,
        market_type_scope: str = "spot,swap",
        account_region: str = "default",
    ):
        self.exchange = exchange
        self.is_auto_trade_enabled = is_auto_trade_enabled
        self.market_type_scope = market_type_scope
        self.account_region = account_region
```

- [ ] **Step 5: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "normalize_account_region or fake_exchange_account_defaults_account_region or fake_exchange_account_accepts_custom_account_region" -v`

Expected: PASS，确认区域归一化和测试假对象已具备后续 TDD 基础。

- [ ] **Step 6: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "test: add account region parsing fixtures"
```

### Task 2: 扩展账户 coverage helper，返回区域覆盖结果

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定 helper 对区域的聚合与放行语义**

```python
def test_evaluate_account_exchange_coverage_reports_account_regions_by_exchange():
    payload = {"buy_exchange": "bitget", "sell_exchange": "gate"}
    accounts = [
        FakeExchangeAccount(exchange="bitget", account_region="default"),
        FakeExchangeAccount(exchange="gate", account_region="main"),
    ]

    coverage = _evaluate_account_exchange_coverage(
        payload=payload,
        accounts=accounts,
        dispatcher_region="main",
    )

    assert coverage["account_regions_by_exchange"] == {
        "bitget": ["default"],
        "gate": ["main"],
    }
    assert coverage["dispatcher_region"] == "main"
    assert coverage["has_region_coverage"] is True
```

```python
def test_evaluate_account_exchange_coverage_fails_when_one_side_region_is_incompatible():
    payload = {"buy_exchange": "bitget", "sell_exchange": "gate"}
    accounts = [
        FakeExchangeAccount(exchange="bitget", account_region="main"),
        FakeExchangeAccount(exchange="gate", account_region="hk"),
    ]

    coverage = _evaluate_account_exchange_coverage(
        payload=payload,
        accounts=accounts,
        dispatcher_region="main",
    )

    assert coverage["account_regions_by_exchange"] == {
        "bitget": ["main"],
        "gate": ["hk"],
    }
    assert coverage["has_market_type_coverage"] is True
    assert coverage["has_region_coverage"] is False
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "reports_account_regions_by_exchange or fails_when_one_side_region_is_incompatible" -v`

Expected: FAIL，当前 helper 还不接受 `dispatcher_region` 参数，也不会返回 `account_regions_by_exchange`、`dispatcher_region` 与 `has_region_coverage`。

- [ ] **Step 3: 写最小实现，扩展 helper 返回值**

```python
def _evaluate_account_exchange_coverage(
    *,
    payload: dict,
    accounts: list[Any],
    dispatcher_region: str = "default",
) -> dict[str, object]:
    ...
    normalized_dispatcher_region = _normalize_account_region(dispatcher_region)
    account_regions_by_exchange: dict[str, list[str]] = {}
    region_covered_exchanges: set[str] = set()

    for account in accounts:
        ...
        normalized_region = _normalize_account_region(
            getattr(account, "account_region", None)
        )
        existing_regions = set(account_regions_by_exchange.get(exchange_name, []))
        existing_regions.add(normalized_region)
        account_regions_by_exchange[exchange_name] = sorted(existing_regions)
        if (
            getattr(account, "is_auto_trade_enabled", True)
            and allowed_market_type_set.intersection(parsed_scope)
            and (
                normalized_region == "default"
                or normalized_region == normalized_dispatcher_region
            )
        ):
            region_covered_exchanges.add(exchange_name)
    ...
    has_region_coverage = (
        buy_exchange in region_covered_exchanges
        and sell_exchange in region_covered_exchanges
    )
    return {
        ...
        "dispatcher_region": normalized_dispatcher_region,
        "account_regions_by_exchange": account_regions_by_exchange,
        "has_region_coverage": has_region_coverage,
    }
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "reports_account_regions_by_exchange or fails_when_one_side_region_is_incompatible" -v`

Expected: PASS，helper 已能聚合交易所下的区域信息并输出区域放行结论。

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: evaluate account region coverage in dispatcher"
```

### Task 3: 在 dispatcher 主循环新增 `account_region_mismatch` 跳过分支

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定买边区域不兼容时跳过**

```python
@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_buy_exchange_account_region_mismatches_dispatcher_region():
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
                    FakeExchangeAccount(exchange="bitget", account_region="hk"),
                    FakeExchangeAccount(exchange="gate", account_region="main"),
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
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created == []
    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_region_mismatch"
    assert skipped_event.payload["dispatcher_region"] == "main"
    assert skipped_event.payload["account_regions_by_exchange"] == {
        "bitget": ["hk"],
        "gate": ["main"],
    }
```

- [ ] **Step 2: 再写失败测试，锁定卖边区域不兼容时也跳过**

```python
@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_sell_exchange_account_region_mismatches_dispatcher_region():
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
                    FakeExchangeAccount(exchange="bitget", account_region="default"),
                    FakeExchangeAccount(exchange="gate", account_region="hk"),
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
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_region_mismatch"
    assert skipped_event.payload["account_regions_by_exchange"] == {
        "bitget": ["default"],
        "gate": ["hk"],
    }
```

- [ ] **Step 3: 再写失败测试，锁定 `default` 与多账户场景仍可通过**

```python
@pytest.mark.asyncio
async def test_dispatcher_allows_user_when_one_account_per_exchange_matches_dispatcher_region():
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
                    FakeExchangeAccount(exchange="bitget", account_region="hk"),
                    FakeExchangeAccount(exchange="bitget", account_region="default"),
                    FakeExchangeAccount(exchange="gate", account_region="main"),
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

    assert [item.user_id for item in repository.created] == [42]
    assert len(redis_client.xadds) == 1
```

- [ ] **Step 4: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "account_region_mismatch or matches_dispatcher_region" -v`

Expected: FAIL，当前 dispatcher 还没有 `account_region_mismatch` 分支，也不会在 payload 中写入 `dispatcher_region` 与 `account_regions_by_exchange`。

- [ ] **Step 5: 写最小实现，把 helper 结果接入 dispatcher**

```python
coverage = _evaluate_account_exchange_coverage(
    payload=payload,
    accounts=accounts,
    dispatcher_region=self.region,
)
...
if not coverage["has_region_coverage"]:
    if self.event_router is not None:
        await self.event_router.dispatch(
            _build_dispatch_user_event(
                event_type="dispatcher.user.skipped",
                region=self.region,
                payload={
                    "user_id": user_id,
                    "reason": "account_region_mismatch",
                    "buy_exchange": str(payload["buy_exchange"]),
                    "sell_exchange": str(payload["sell_exchange"]),
                    "dispatcher_region": coverage["dispatcher_region"],
                    "available_exchanges": coverage["available_exchanges"],
                    "auto_trade_enabled_exchanges": coverage["auto_trade_enabled_exchanges"],
                    "market_type_scopes_by_exchange": coverage["market_type_scopes_by_exchange"],
                    "account_regions_by_exchange": coverage["account_regions_by_exchange"],
                },
            )
        )
    continue
```

- [ ] **Step 6: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "account_region_mismatch or matches_dispatcher_region" -v`

Expected: PASS，买边或卖边区域不兼容时被跳过，`default` 和多账户场景下只要任意一条可接受账户存在即可通过。

- [ ] **Step 7: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: skip dispatcher users on account region mismatch"
```

### Task 4: 补 control-rule 短路回归与运维验证文档

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 先写失败测试，锁定 `account_region` 失败不会进入 control guard**

```python
@pytest.mark.asyncio
async def test_dispatcher_account_region_skip_does_not_emit_control_rule_events():
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
                    FakeExchangeAccount(exchange="bitget", account_region="hk"),
                    FakeExchangeAccount(exchange="gate", account_region="main"),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        control_guard=guard,
        stream_key="stream:spot_opps",
        block_ms=0,
        region="main",
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert guard.calls == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_region_mismatch"
    assert all(not event.event_type.startswith("control.rule.") for event in router.events)
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "account_region_skip_does_not_emit_control_rule_events" -v`

Expected: FAIL，如果 dispatcher 还没有在 `account_region` 阶段短路，`guard.calls` 会非空或缺少对应 reason。

- [ ] **Step 3: 用最小代码修正并跑回归**

```python
if not coverage["has_region_coverage"]:
    ...
    continue

decision = self.control_guard.evaluate(
    strategy_id=strategy_id,
    user_id=int(user_id),
    symbol=str(payload["symbol"]),
    ...
)
```

Run: `python -m pytest tests/test_live_workers.py -k "account_region or market_type_scope or auto_trade or account_exchange_coverage" -v`

Expected: PASS，新增区域过滤不回归现有 `market_type_scope`、`auto_trade` 与 `account_exchange_coverage` 语义。

- [ ] **Step 4: 更新运维文档，新增 `Dispatcher Account Region Validation` 小节**

```md
### Dispatcher Account Region Validation

1. 在 canary 用户的 `buy_exchange` 与 `sell_exchange` 账户都已启用、自动交易开启、`market_type_scope` 通过的前提下，先把其中一边账户的 `account_region` 设为与当前 dispatcher 不兼容的值，例如 `hk`。
2. 保持另一边账户 `account_region=main` 或 `default`，重启 `furun-spot-dispatcher.service` 后注入一条专用机会：
   - `symbol=BTC/USDT`
   - `buy_exchange=bitget`
   - `sell_exchange=gate`
3. 预期结果：
   - 不创建新的 `DISPATCHED` 任务
   - 不写新的节点执行 payload
   - `dispatcher.user.skipped` 的 `payload.reason` 为 `account_region_mismatch`
   - `payload.dispatcher_region` 为当前 dispatcher 区域
   - `payload.account_regions_by_exchange` 显示各交易所当前声明的区域
   - 不出现新的 `control.rule.blocked` / `control.rule.resized`
4. 将缺失一侧的 `account_region` 恢复为 `default` 或当前 dispatcher 对应区域后再次注入同类机会，预期恢复创建任务与写节点流。
```

- [ ] **Step 5: 重新运行测试并校验文档文件**

Run: `python -m pytest tests/test_live_workers.py -k "account_region or market_type_scope or auto_trade or account_exchange_coverage" -v`

Expected: PASS，新增测试与既有账户过滤链同时通过。

- [ ] **Step 6: 提交这一小步**

```bash
git add tests/test_live_workers.py docs/ops/live-workers-systemd.md
git commit -m "docs: add dispatcher account region validation"
```

### Task 5: 全量验收与收尾检查

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 运行针对 dispatcher 的完整测试组**

Run: `python -m pytest tests/test_live_workers.py -v`

Expected: PASS，`account_region` 新增逻辑与现有 scanner / dispatcher / control guard 测试全部兼容。

- [ ] **Step 2: 补跑相邻测试**

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
git commit -m "feat: enforce account region in dispatcher"
```

## 自检结论

- `spec` 覆盖情况：
  - `account_region` 运行层接入：Task 2, Task 3
  - `default` 全局兼容语义：Task 1, Task 2, Task 3
  - 新 skip reason 与 payload：Task 3
  - 不进入 control rule：Task 4
  - 测试与运维验证：Task 1, Task 2, Task 3, Task 4, Task 5
- 无占位符、无 `TODO/TBD`、无未定义步骤引用
- 函数名、reason 名、字段名在各任务中保持一致：
  - `_normalize_account_region`
  - `_evaluate_account_exchange_coverage`
  - `account_region_mismatch`
  - `dispatcher_region`
  - `account_regions_by_exchange`
  - `has_region_coverage`
