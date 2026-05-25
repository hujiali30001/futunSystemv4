# Control Rule Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为当前 `control-admin -> Redis 真值 -> dispatcher / executor` 双层控制链补齐最小结构化运行事件，让拦截与缩量行为在日志中直接可见，同时保持现有飞书/QQ外部通知策略不变。

**Architecture:** 保持现有 `RuntimeEvent`、`AlertRouter` 和 `control-admin` 架构不变，只在 `RedisNodeTaskDispatcher` 与 `RedisExecutionTaskConsumer` 命中控制规则时增加 `control.rule.blocked` 与 `control.rule.resized` 事件。事件通过现有 `event_router` 进入结构化日志，不新增 Redis 命中历史存储、HTTP 查询接口或外部通知逻辑。

**Tech Stack:** Python 3.10+, asyncio, dataclasses, pytest, pytest-asyncio

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - 增加控制命中事件构造函数
  - 在 `dispatcher` 和 `executor` 命中规则时发出 `control.rule.blocked` / `control.rule.resized`
- `d:\old\FuRunSystemV4\app\runtime\alerting.py`
  - 保持外部通知策略不变
  - 如有必要，只补中文标题映射或显式测试覆盖，不扩展外发条件
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 补 `dispatcher` / `executor` 两层命中事件测试
- `d:\old\FuRunSystemV4\tests\test_alerting.py`
  - 补 `AlertRouter` 对 `control.rule.*` 只记录日志、不外发的测试

### Task 1: 为 dispatcher 和 executor 增加控制命中运行事件

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定两层 blocked / resized 事件语义**

```python
@pytest.mark.asyncio
async def test_dispatcher_emits_control_rule_blocked_event():
    redis_client = FakeRedis()
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        control_guard=FakeControlGuard(
            allowed=False,
            approved_notional=0.0,
            reason="reduce_only",
        ),
        block_ms=0,
        event_router=router,
        region="main",
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert redis_client.xadds == []
    assert router.events[0].event_type == "control.rule.blocked"
    assert router.events[0].service == "dispatcher"
    assert router.events[0].payload["user_id"] == "42"
    assert router.events[0].payload["requested_notional"] == 15.0
    assert router.events[0].payload["approved_notional"] == 0.0
    assert router.events[0].payload["reason"] == "reduce_only"
```

```python
@pytest.mark.asyncio
async def test_dispatcher_emits_control_rule_resized_event():
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
                            "target_quote_amount": "50.0",
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
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        control_guard=FakeControlGuard(
            allowed=True,
            approved_notional=35.0,
            reason=None,
        ),
        block_ms=0,
        event_router=router,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert redis_client.xadds[0][1]["target_quote_amount"] == "35.0"
    assert router.events[0].event_type == "control.rule.resized"
    assert router.events[0].service == "dispatcher"
    assert router.events[0].payload["requested_notional"] == 50.0
    assert router.events[0].payload["approved_notional"] == 35.0
    assert router.events[0].payload["reason"] == "limit_rule_applied"
```

```python
@pytest.mark.asyncio
async def test_executor_emits_control_rule_blocked_event_before_dispatch():
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
                            "source_message_id": "src-1",
                        },
                    )
                ],
            )
        ]
    )
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(FakeSpotService()),
        stream_key="stream:spot_exec_tasks:node-a",
        control_guard=FakeControlGuard(
            allowed=False,
            approved_notional=0.0,
            reason="reduce_only",
        ),
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert router.events[0].event_type == "control.rule.blocked"
    assert router.events[0].service == "executor"
    assert router.events[0].payload["source_message_id"] == "src-1"
    assert router.events[0].payload["requested_notional"] == 40.0
    assert router.events[0].payload["approved_notional"] == 0.0
```

```python
@pytest.mark.asyncio
async def test_executor_emits_control_rule_resized_event():
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
                            "source_message_id": "src-1",
                        },
                    )
                ],
            )
        ]
    )
    service = FakeSpotService()
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        control_guard=FakeControlGuard(
            allowed=True,
            approved_notional=18.0,
            reason=None,
        ),
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert service.calls[0]["target_quote_amount"] == 18.0
    assert router.events[0].event_type == "control.rule.resized"
    assert router.events[0].service == "executor"
    assert router.events[0].payload["source_message_id"] == "src-1"
    assert router.events[0].payload["requested_notional"] == 40.0
    assert router.events[0].payload["approved_notional"] == 18.0
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -v`
Expected: FAIL，提示 `RedisNodeTaskDispatcher` / `RedisExecutionTaskConsumer` 尚未发出 `control.rule.*` 事件，或构造函数还不接受 `event_router`

- [ ] **Step 3: 实现最小事件构造与发射逻辑**

```python
def _build_control_rule_event(
    *,
    event_type: str,
    service: str,
    region: str,
    symbol: str | None,
    exchange: str | None,
    user_id: str,
    source_message_id: str | None,
    requested_notional: float,
    approved_notional: float,
    reason: str | None,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type=event_type,
        level="INFO",
        service=service,
        region=region,
        symbol=symbol,
        exchange=exchange,
        message=(
            "control rule blocked request"
            if event_type == "control.rule.blocked"
            else "control rule resized request"
        ),
        payload={
            "user_id": user_id,
            "source_message_id": source_message_id,
            "requested_notional": requested_notional,
            "approved_notional": approved_notional,
            "reason": reason or "limit_rule_applied",
        },
    )
```

```python
class RedisNodeTaskDispatcher:
    def __init__(..., event_router=None, region: str = "default", **kwargs) -> None:
        ...
        self.event_router = event_router
        self.region = region

    async def run(self, *, max_iterations: int | None = None) -> int:
        ...
        if decision is not None and not decision.allowed:
            if self.event_router is not None:
                await self.event_router.dispatch(
                    _build_control_rule_event(
                        event_type="control.rule.blocked",
                        service="dispatcher",
                        region=self.region,
                        symbol=str(payload.get("symbol")),
                        exchange=str(payload.get("buy_exchange")),
                        user_id=user_id,
                        source_message_id=message_id,
                        requested_notional=requested_notional,
                        approved_notional=decision.approved_notional,
                        reason=decision.reason,
                    )
                )
            continue
        ...
        if decision is not None and 0 < decision.approved_notional < requested_notional:
            task_payload["target_quote_amount"] = str(decision.approved_notional)
            if self.event_router is not None:
                await self.event_router.dispatch(
                    _build_control_rule_event(
                        event_type="control.rule.resized",
                        service="dispatcher",
                        region=self.region,
                        symbol=str(payload.get("symbol")),
                        exchange=str(payload.get("buy_exchange")),
                        user_id=user_id,
                        source_message_id=message_id,
                        requested_notional=requested_notional,
                        approved_notional=decision.approved_notional,
                        reason=decision.reason,
                    )
                )
```

```python
class RedisExecutionTaskConsumer(RedisSpotConsumer):
    async def run(...):
        ...
        if not decision.allowed:
            if self.event_router is not None:
                await self.event_router.dispatch(
                    _build_control_rule_event(
                        event_type="control.rule.blocked",
                        service="executor",
                        region=self.region,
                        symbol=str(payload.get("symbol")),
                        exchange=str(payload.get("buy_exchange")),
                        user_id=str(payload["user_id"]),
                        source_message_id=str(payload.get("source_message_id")),
                        requested_notional=requested_notional,
                        approved_notional=decision.approved_notional,
                        reason=decision.reason,
                    )
                )
            ...
        if 0 < decision.approved_notional < requested_notional:
            payload = dict(payload)
            payload["target_quote_amount"] = str(decision.approved_notional)
            if self.event_router is not None:
                await self.event_router.dispatch(
                    _build_control_rule_event(
                        event_type="control.rule.resized",
                        service="executor",
                        region=self.region,
                        symbol=str(payload.get("symbol")),
                        exchange=str(payload.get("buy_exchange")),
                        user_id=str(payload["user_id"]),
                        source_message_id=str(payload.get("source_message_id")),
                        requested_notional=requested_notional,
                        approved_notional=decision.approved_notional,
                        reason=decision.reason,
                    )
                )
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -v`
Expected: PASS，`dispatcher` 与 `executor` 的 blocked / resized 事件都能按预期发出

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: emit control rule runtime events"
```

### Task 2: 验证 control.rule 事件只记录日志不外发

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\alerting.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_alerting.py`

- [ ] **Step 1: 先写失败测试，锁定新事件不会触发外部通知**

```python
import pytest

from app.runtime.alerting import AlertRouter, StructuredEventLogger
from app.runtime.runtime_events import RuntimeEvent


class FakeFeishu:
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)


class FakeEmail:
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_alert_router_does_not_send_external_notifications_for_control_rule_events():
    lines = []
    feishu = FakeFeishu()
    email = FakeEmail()
    router = AlertRouter(
        logger=StructuredEventLogger(sink=lines.append),
        feishu_notifier=feishu,
        email_notifier=email,
        alerts_enabled=True,
        feishu_enabled=True,
        email_enabled=True,
    )

    await router.dispatch(
        RuntimeEvent(
            event_type="control.rule.blocked",
            level="INFO",
            service="dispatcher",
            region="main",
            symbol="BTC/USDT",
            exchange="okx",
            message="control rule blocked request",
            payload={
                "user_id": "42",
                "source_message_id": "1-0",
                "requested_notional": 100.0,
                "approved_notional": 0.0,
                "reason": "reduce_only",
            },
        )
    )

    assert len(lines) == 1
    assert "control.rule.blocked" in lines[0]
    assert feishu.events == []
    assert email.events == []
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_alerting.py -v`
Expected: FAIL，如果当前 `AlertRouter` 对 `INFO` 事件的行为假设与新测试不一致，或测试基建尚未准备好

- [ ] **Step 3: 用最小改动确保行为显式且可测试**

```python
def _event_title_zh(event: RuntimeEvent) -> str:
    mapping = {
        "worker.start_failed": "服务启动失败",
        "worker.started": "服务已启动",
        "worker.stopped": "服务已停止",
        "scanner.iteration.failed": "扫描任务异常",
        "consumer.message.failed": "机会消费异常",
        "opportunity.detected": "检测到套利机会",
        "control.rule.blocked": "控制规则已拦截",
        "control.rule.resized": "控制规则已缩量",
    }
    return mapping.get(event.event_type, event.event_type)
```

```python
class AlertRouter:
    async def dispatch(self, event: RuntimeEvent) -> None:
        self.logger.record(event)
        if not self.alerts_enabled:
            return
        if event.level == "CRITICAL":
            await self._send_feishu(event)
            await self._send_email(event)
            return
        if event.level == "ERROR":
            if self._should_dedupe(event):
                return
            await self._send_feishu(event)
            return
        if event.level == "INFO" and event.event_type == "opportunity.detected":
            spread_bps = float(event.payload.get("spread_bps", 0.0))
            if spread_bps > self.success_spread_bps_threshold:
                await self._send_feishu(event)
```

说明：

- 如果现有代码已经满足“只记录不外发”，则保持逻辑不变，只补中文标题映射与测试
- 不要为 `control.rule.*` 增加新的外发分支

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_alerting.py -v`
Expected: PASS，`control.rule.*` 事件只进入日志，不触发飞书或邮件

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/alerting.py tests/test_alerting.py
git commit -m "test: lock control rule events to log-only alerts"
```

### Task 3: 运行本阶段总回归并补远端验证说明

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 先补运维文档里的日志检查示例**

````md
### Control Rule Events

控制链命中后，可在主服务器和执行节点直接查看结构化日志：

```bash
sudo journalctl -u furun-spot-dispatcher.service -n 50 --no-pager | grep 'control.rule'
sudo journalctl -u furun-spot-executor.service -n 50 --no-pager | grep 'control.rule'
```

常见事件：

- `control.rule.blocked`
- `control.rule.resized`

关注字段：

- `service`
- `symbol`
- `exchange`
- `payload.user_id`
- `payload.source_message_id`
- `payload.requested_notional`
- `payload.approved_notional`
- `payload.reason`
````

- [ ] **Step 2: 运行本阶段总回归**

Run: `python -m pytest tests/test_live_workers.py tests/test_alerting.py tests/test_live_worker_alerts.py -v`
Expected: PASS，运行事件和告警行为同时通过

- [ ] **Step 3: 提交这一小步**

```bash
git add docs/ops/live-workers-systemd.md
git commit -m "docs: add control rule event verification steps"
```
