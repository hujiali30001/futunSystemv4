# 交易链路闭环：订单/成交/持仓记录 设计文档

## 1. 文档目标

本文档定义在现有交易执行链路（`TradeExecutor` → ccxt → 交易所）之上补齐记录层的方案，包含：

- 三张新表：`order_records`、`fill_records`、`position_snapshots`
- 现有执行路径插入写入点
- 订单状态轮询 + 成交明细采集
- 前端持仓页改为展示真实盈亏数据
- testnet 验证一笔完整交易（开仓 → 填充 → 平仓）

## 2. 设计原则

- **最小侵入**：不改 `TradeExecutor` 核心流程，在下单点前后插入写 record 的 hook
- **异步写入**：订单写入不阻塞下单关键路径，用 `asyncio.create_task` 异步写 DB
- **幂等优先**：所有 order record 按 `client_order_id` 去重
- **客户端定单 ID**：下单前生成 `client_order_id`（`task_uuid` + `leg_index` 组合），用于后续配对交易所回执

## 3. 数据模型

### 3.1 `order_records`

```python
class OrderRecord(TimestampMixin, Base):
    __tablename__ = "order_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("arbitrage_tasks.id"), index=True)
    leg_type: Mapped[str] = mapped_column(String(16))          # "spot" / "derivative"
    exchange: Mapped[str] = mapped_column(String(32))
    exchange_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    side: Mapped[str] = mapped_column(String(8))               # "buy" / "sell"
    market_type: Mapped[str] = mapped_column(String(8))        # "spot" / "swap"
    client_order_id: Mapped[str] = mapped_column(String(128), unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32))
    order_type: Mapped[str] = mapped_column(String(16))        # "limit" / "market"
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="submitting")  # submitting/open/closed/canceled/expired
    avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_amount: Mapped[float] = mapped_column(Float, default=0.0)
    fee_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    raw_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

### 3.2 `fill_records`

```python
class FillRecord(TimestampMixin, Base):
    __tablename__ = "fill_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("arbitrage_tasks.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_records.id"), index=True)
    leg_type: Mapped[str] = mapped_column(String(16))
    exchange: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(32))
    fill_price: Mapped[float] = mapped_column(Float)
    fill_amount: Mapped[float] = mapped_column(Float)
    fill_cost: Mapped[float] = mapped_column(Float)
    fee_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exchange_trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

### 3.3 `position_snapshots`

```python
class PositionSnapshot(TimestampMixin, Base):
    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("arbitrage_tasks.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    snapshot_type: Mapped[str] = mapped_column(String(16), default="open")  # "open" / "close" / "snapshot"
    symbol: Mapped[str] = mapped_column(String(32))
    spot_exchange: Mapped[str] = mapped_column(String(32))
    derivative_exchange: Mapped[str] = mapped_column(String(32))
    spot_amount: Mapped[float] = mapped_column(Float, default=0.0)
    spot_cost: Mapped[float] = mapped_column(Float, default=0.0)
    derivative_amount: Mapped[float] = mapped_column(Float, default=0.0)
    derivative_cost: Mapped[float] = mapped_column(Float, default=0.0)
    hedge_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    margin_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_fee_accrued: Mapped[float] = mapped_column(Float, default=0.0)
```

### 3.4 `arbitrage_tasks` 补充字段

```python
realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
total_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
```

## 4. 写入点设计

### 4.0 TradeExecutor 注入 OrderRecorder

当前 `TradeExecutor.__init__` 只接收 `adapter_factory`。需额外注入 `order_recorder`（可选）：

```python
class TradeExecutor:
    def __init__(self, *, adapter_factory, order_recorder=None):
        self.adapter_factory = adapter_factory
        self.order_recorder = order_recorder
```

`order_recorder=None` 时跳过所有写 DB 操作（兼容测试 / Mock / 非 DB 场景）。

### 4.1 第 1 步：下单前 → 建 OrderRecord（status="submitting"）

位置：`TradeExecutor.execute_open()` 调用 `adapter.create_order()` 之前。

逻辑：
1. 生成 `client_order_id = f"{task.task_id}_spot_0"` / `f"{task.task_id}_deriv_0"`
2. 调用 `order_recorder.record_submit(...)` 写入 `OrderRecord(status="submitting")`
3. 如果该 `client_order_id` 已存在（重试 / 幂等），跳过

### 4.2 第 2 步：下单后 → 更新 OrderRecord（status="open"）

位置：`create_order()` 成功返回后。

ccxt limit 单返回的字段已包含部分成交信息（`filled`、`average`、`fee`、`id`）。

逻辑：
1. `create_order` 成功 → 更新 `status="open"`, `exchange_order_id=info["id"]`
2. **若 `filled > 0`** → 立即写 `FillRecord`（取 `info["filled"]`、`info["average"]`、`info["fee"]`）
3. `create_order` 异常 → 更新 `status="canceled"`, `error_reason`

**注意**：limit 单返回时 `filled=0` 是正常的，不能立刻拉 `fill_records`。需等待后续成交。

### 4.3 第 3 步：订单状态轮询（补齐未成交部分）

位置：`TradeExecutor.execute_open()` 的 `asyncio.gather` 完成后。

逻辑：
1. 所有 legs 都 `status="open"` 后，启动后台 `asyncio.create_task` 轮询
2. 每 2 秒调 `client.fetch_order(exchange_order_id)` 查询状态
3. 若 `filled > 上次记录` → 计算增量成交 → 写新增 `FillRecord` → 更新 `OrderRecord.filled_amount / avg_price / fee`
4. 若 `status="closed"` → 更新 `OrderRecord(status="closed")`
5. 若 `status="canceled"` / `status="expired"` → 更新对应状态 + error_reason
6. 超时 300 秒后放弃，标记 `status="expired"`
7. 所有 legs 都 `closed` 或 `canceled` → 结束轮询

### 4.4 第 4 步：双腿成交 → 拍 PositionSnapshot（"open"）

时机：**所有 open legs 的 `OrderRecord.status` 都变为 `"closed"` 后**。

逻辑：
1. 从 FillRecords 汇总 spot leg 和 deriv leg 的 `fill_cost` / `fill_amount`
2. 计算 `hedge_ratio = spot_cost / deriv_cost`（越接近 1.0 越好）
3. 从 Redis `md:ticker:{exchange}:{symbol}` 取当前市价
4. 计算 `unrealized_pnl = (spot_current - spot_avg) * spot_amount + (deriv_current - deriv_avg) * deriv_amount`
5. 写入 `PositionSnapshot(snapshot_type="open", ...)`

### 4.5 第 5 步：平仓完成 → 拍 PositionSnapshot（"close"）+ 算盈亏

时机：平仓双腿的 OrderRecord 都为 `"closed"`。

逻辑：
1. 读取开仓 snapshot
2. 汇总平仓 fills 的 `fill_cost`
3. 计算 `realized_pnl = (平仓 spot 收入 - 开仓 spot 成本) + (平仓 deriv 收入 - 开仓 deriv 成本)`
4. 写入 `PositionSnapshot(snapshot_type="close", realized_pnl=...)`
5. 回写 `arbitrage_tasks.realized_pnl`、`arbitrage_tasks.total_fee`

### 4.6 repair 路径同样需要 OrderRecord

`repair_execution_service.py` 用市场单补单，也调 `adapter.create_order()`。在该路径中也注入 `OrderRecorder`，建 OrderRecord + FillRecord，确保补单记录不丢失。

### 4.7 定时持仓快照（后续迭代）

每 N 分钟对 `status=HOLDING` 的任务拍一次 `PositionSnapshot(snapshot_type="snapshot")`，取当前 ticker 算 `unrealized_pnl`。本轮不实现，留接口。

## 5. 数据写入异步化

所有 OrderRecord / FillRecord 的写入通过独立的 `OrderRecorder` 类完成，不阻塞主交易路径：

```python
class OrderRecorder:
    def __init__(self, db_session_factory):
        self._factory = db_session_factory

    async def record_submit(self, order: dict) -> int:
        """插入 submitting 状态，返回 order_record.id"""
        ...

    async def record_filled(self, order_id: int, exchange_order_id: str,
                            avg_price: float, filled_amount: float,
                            fills: list[dict]) -> None:
        """更新 order + 插入 fill_records"""
        ...

    async def record_failed(self, order_id: int, reason: str) -> None:
        """标记为 canceled + 记录错误"""
        ...
```

## 6. 前端持仓页改造

当前 `PositionsPage` 只读 `arbitrage_tasks.status`。改造后：

| 列 | 原来源 | 新来源 |
|----|------|------|
| 币种 | symbol | 不变 |
| 交易所 | spot/derivative_exchange | 不变 |
| 方向 | task_type | 不变 |
| 名义金额 | target_notional | **取 fill_records 的实际 fill_cost 之和** |
| 开仓价差 | expected_spread_bps / 100 | **从 position_snapshots 取 realized spread** |
| 当前盈亏 | 无 | **新增：position_snapshots.unrealized_pnl** |
| 状态 | status | 不变 |
| 恢复状态 | auto_recovery_status | 不变 |

API 端点改造：`GET /api/positions` 返回的数据结构中 `items` 每项新增 `realized_pnl`、`unrealized_pnl`、`filled_notional`。

## 7. testnet 验证计划

验证步骤（在服务器上执行）：

1. **查策略配置**：确认数据库中有一条 `is_enabled=True` 的策略
2. **查账户余额**：确认 testnet 账户有 USDT 余额
3. **查 Redis**：确认 `stream:opportunities` 有数据，`arb:zset:open` 有排行
4. **触发 dispatch**：重启 `arb_dispatcher` worker，观察是否创建 `arbitrage_task`
5. **观察 executor**：重启 `arb_executor` worker，观察日志中是否出现 `create_order` 调用
6. **查 DB**：看 `order_records` 表是否有记录写入
7. **验证闭环**：如果 testnet Balance 足够，一笔完整交易应能走通开仓 → 看到 order_records → 看到 fill_records → 看到 position_snapshots

## 8. 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `models.py` | 新增 OrderRecord / FillRecord / PositionSnapshot；arbitrage_tasks 加 realized_pnl / total_fee；PositionSnapshot 加 snapshot_type |
| 新建 | `app/trading/order_recorder.py` | OrderRecorder 类（异步写 DB） |
| 新建 | `app/trading/order_poller.py` | OrderPoller 类（后台轮询订单状态 + 增量写 fills） |
| 修改 | `app/trading/executor.py` | 注入 order_recorder；下单前/后写 record；启动 order_poller |
| 修改 | `app/runtime/trade_execution_service.py` | 注入 order_recorder 并传递给 executor |
| 修改 | `app/runtime/repair_execution_service.py` | 注入 order_recorder，补单时建 OrderRecord + FillRecord |
| 修改 | `app/runtime/live_workers.py` | ArbitrageExecutionTaskConsumer 构建 order_recorder 并注入 |
| 修改 | `app/api/positions.py` | 返回 enriched 数据（fills / pnl） |
| 修改 | `web/src/pages/PositionsPage.tsx` | 展示真实盈亏 |
| 修改 | `web/src/api.ts` | PositionItem 类型扩展 |

## 9. 不涉及

- 不修改 Dispatcher 匹配逻辑
- 不新增新的交易所或交易对
- 不修改 ccxt 会话管理
- 行情采集与机会计算完全不变

---

*与主设计文档 [2026-05-24-cross-exchange-arbitrage-design.md](2026-05-24-cross-exchange-arbitrage-design.md) 第 6.1 节 order_records / fill_records / position_snapshots 表对应。*
