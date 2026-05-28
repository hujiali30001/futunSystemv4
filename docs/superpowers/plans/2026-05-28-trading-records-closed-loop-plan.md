# 交易链路闭环 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有交易执行链路上补齐订单记录、成交明细、持仓快照三张表及相关写入逻辑，前端持仓页展示真实盈亏。

**Architecture:** 新增 `OrderRecorder`（异步写 DB）和 `OrderPoller`（后台轮询 ccxt fetch_order 补成交明细），注入到 `TradeExecutor` 和 repair 服务中。平仓后在 `ArbitrageExecutionTaskConsumer.run_once()` 中拍 snapshot 并计算 realized_pnl。

**Tech Stack:** Python 3.10+ / SQLAlchemy / ccxt / FastAPI / React + TypeScript

---

## File Structure Map

```
MOD  models.py                       — 新增 OrderRecord / FillRecord / PositionSnapshot；arbitrage_tasks 加 realized_pnl / total_fee
NEW  app/trading/order_recorder.py   — OrderRecorder 类（异步 INSERT/UPDATE）
NEW  app/trading/order_poller.py     — OrderPoller 类（后台轮询 ccxt fetch_order）
MOD  app/trading/executor.py         — 注入 order_recorder；下单前/后写 record；启动 order_poller
MOD  app/runtime/trade_execution_service.py — 注入 order_recorder 并传递给 executor
MOD  app/runtime/repair_execution_service.py — 注入 order_recorder，补单时建 OrderRecord
MOD  app/runtime/live_workers.py     — 构建 order_recorder 注入链路
MOD  app/api/positions.py            — 返回 enriched 盈亏数据
MOD  web/src/pages/PositionsPage.tsx  — 展示真实盈亏
MOD  web/src/api.ts                  — PositionItem 类型扩展
```

---

### Task 1: 数据模型 — 新增三张表 + arbitrage_tasks 字段

**Files:**
- Modify: `models.py:176-203`

- [ ] **Step 1: 在 models.py 末尾（AdminActionLog 之后）新增 OrderRecord、FillRecord、PositionSnapshot**

```python
class OrderRecord(TimestampMixin, Base):
    __tablename__ = "order_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("arbitrage_tasks.id"), index=True)
    leg_type: Mapped[str] = mapped_column(String(16), default="spot")
    exchange: Mapped[str] = mapped_column(String(32))
    exchange_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    side: Mapped[str] = mapped_column(String(8))
    market_type: Mapped[str] = mapped_column(String(8), default="spot")
    client_order_id: Mapped[str] = mapped_column(String(128), unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32))
    order_type: Mapped[str] = mapped_column(String(16), default="limit")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="submitting")
    avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_amount: Mapped[float] = mapped_column(Float, default=0.0)
    fee_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    raw_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class FillRecord(TimestampMixin, Base):
    __tablename__ = "fill_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("arbitrage_tasks.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_records.id"), index=True)
    leg_type: Mapped[str] = mapped_column(String(16), default="spot")
    exchange: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(32))
    fill_price: Mapped[float] = mapped_column(Float)
    fill_amount: Mapped[float] = mapped_column(Float)
    fill_cost: Mapped[float] = mapped_column(Float)
    fee_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exchange_trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filled_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.utcnow)


class PositionSnapshot(TimestampMixin, Base):
    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("arbitrage_tasks.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    snapshot_type: Mapped[str] = mapped_column(String(16), default="open")
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

- [ ] **Step 2: 在 arbitrage_tasks 类末尾（worker_node_id 之后）加字段**

在 `ArbitrageTask` 类的 `worker_node_id` 字段后面插入：
```python
realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
total_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
```

- [ ] **Step 3: 运行测试**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 4: Commit**

```bash
git add models.py
git commit -m "feat: add OrderRecord, FillRecord, PositionSnapshot tables + arbitrage_tasks.pnl fields"
```

---

### Task 2: OrderRecorder — 异步写 DB

**Files:**
- Create: `app/trading/order_recorder.py`

- [ ] **Step 1: 创建 app/trading/order_recorder.py**

```python
import asyncio
from datetime import datetime
from sqlalchemy.orm import sessionmaker, Session

from models import OrderRecord, FillRecord


class OrderRecorder:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    async def record_submit(self, *,
                            task_id: int,
                            leg_type: str,
                            exchange: str,
                            side: str,
                            market_type: str,
                            client_order_id: str,
                            symbol: str,
                            order_type: str,
                            price: float | None,
                            amount: float,
                            ) -> int:
        def _do():
            with self._factory() as s:
                existing = s.query(OrderRecord).filter(
                    OrderRecord.client_order_id == client_order_id
                ).first()
                if existing is not None:
                    return existing.id
                rec = OrderRecord(
                    task_id=task_id, leg_type=leg_type,
                    exchange=exchange, side=side, market_type=market_type,
                    client_order_id=client_order_id, symbol=symbol,
                    order_type=order_type, price=price, amount=amount,
                    status="submitting",
                )
                s.add(rec)
                s.commit()
                return rec.id
        return await asyncio.to_thread(_do)

    async def record_open(self, *,
                          order_id: int,
                          exchange_order_id: str,
                          avg_price: float | None,
                          filled_amount: float,
                          fee_cost: float | None,
                          fee_currency: str | None,
                          raw_response: dict | None,
                          ) -> None:
        def _do():
            with self._factory() as s:
                rec = s.query(OrderRecord).filter(OrderRecord.id == order_id).first()
                if rec is None:
                    return
                rec.status = "open"
                rec.exchange_order_id = exchange_order_id
                rec.avg_price = avg_price
                rec.filled_amount = filled_amount
                rec.fee_cost = fee_cost
                rec.fee_currency = fee_currency
                rec.raw_payload_json = raw_response
                s.commit()
        await asyncio.to_thread(_do)

    async def record_failed(self, *, order_id: int, reason: str) -> None:
        def _do():
            with self._factory() as s:
                rec = s.query(OrderRecord).filter(OrderRecord.id == order_id).first()
                if rec is None:
                    return
                rec.status = "canceled"
                rec.error_reason = reason
                s.commit()
        await asyncio.to_thread(_do)

    async def record_poll_result(self, *, order_id: int,
                                 status: str,
                                 filled_amount: float,
                                 avg_price: float | None,
                                 fee_cost: float | None,
                                 fee_currency: str | None,
                                 new_fills: list[dict],
                                 task_id: int,
                                 ) -> None:
        def _do():
            with self._factory() as s:
                rec = s.query(OrderRecord).filter(OrderRecord.id == order_id).first()
                if rec is None:
                    return
                rec.status = status
                rec.filled_amount = filled_amount
                rec.avg_price = avg_price
                rec.fee_cost = fee_cost
                rec.fee_currency = fee_currency
                for fill in new_fills:
                    s.add(FillRecord(
                        task_id=task_id, order_id=order_id,
                        leg_type=rec.leg_type, exchange=rec.exchange,
                        side=rec.side, symbol=rec.symbol,
                        fill_price=fill["price"], fill_amount=fill["amount"],
                        fill_cost=fill["cost"],
                        fee_cost=fill.get("fee_cost"),
                        fee_currency=fill.get("fee_currency"),
                        exchange_trade_id=fill.get("trade_id"),
                        filled_at=datetime.utcnow(),
                    ))
                s.commit()
        await asyncio.to_thread(_do)

    async def record_fills(self, *, order_id: int, task_id: int,
                           fills: list[dict]) -> None:
        def _do():
            with self._factory() as s:
                rec = s.query(OrderRecord).filter(OrderRecord.id == order_id).first()
                if rec is None:
                    return
                for fill in fills:
                    s.add(FillRecord(
                        task_id=task_id, order_id=order_id,
                        leg_type=rec.leg_type, exchange=rec.exchange,
                        side=rec.side, symbol=rec.symbol,
                        fill_price=fill["price"], fill_amount=fill["amount"],
                        fill_cost=fill["cost"],
                        fee_cost=fill.get("fee_cost"),
                        fee_currency=fill.get("fee_currency"),
                        exchange_trade_id=fill.get("trade_id"),
                        filled_at=datetime.utcnow(),
                    ))
                s.commit()
        await asyncio.to_thread(_do)

    def get_session(self) -> Session:
        return self._factory()
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 3: Commit**

```bash
git add app/trading/order_recorder.py
git commit -m "feat: OrderRecorder for async order/fill record writes"
```

---

### Task 3: OrderPoller — 后台轮询订单状态

**Files:**
- Create: `app/trading/order_poller.py`

- [ ] **Step 1: 创建 app/trading/order_poller.py**

```python
import asyncio
from dataclasses import dataclass


@dataclass(slots=True)
class PollResult:
    all_closed: bool
    new_fills: list[dict]
    status: str
    filled_amount: float
    avg_price: float | None
    fee_cost: float | None
    fee_currency: str | None


class OrderPoller:
    def __init__(self, *, order_recorder=None, adapter_factory=None,
                 timeout: float = 300.0, interval: float = 2.0) -> None:
        self.order_recorder = order_recorder
        self.adapter_factory = adapter_factory
        self.timeout = timeout
        self.interval = interval

    async def poll_until_closed(self, *,
                                order_id: int,
                                exchange: str,
                                exchange_order_id: str,
                                symbol: str,
                                task_id: int,
                                current_filled: float = 0.0,
                                ) -> PollResult:
        start = asyncio.get_event_loop().time()
        total_filled = current_filled
        new_fills_all: list[dict] = []
        last_status = "open"

        while True:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > self.timeout:
                return PollResult(all_closed=False, new_fills=[], status="expired",
                                  filled_amount=total_filled, avg_price=None,
                                  fee_cost=None, fee_currency=None)

            try:
                adapter = self.adapter_factory[exchange]
                order_info = await adapter.session.client.fetch_order(
                    exchange_order_id, symbol
                )
                last_status = order_info.get("status", "open")
                filled = float(order_info.get("filled", 0) or 0)

                if filled > total_filled:
                    delta = filled - total_filled
                    avg = float(order_info.get("average", 0) or 0)
                    fee = order_info.get("fee")
                    fee_cost = float(fee.get("cost", 0)) if isinstance(fee, dict) else 0.0
                    fee_currency = str(fee.get("currency", "")) if isinstance(fee, dict) else ""
                    trade_id = order_info.get("id", exchange_order_id)

                    fill = {
                        "price": avg, "amount": delta, "cost": avg * delta,
                        "fee_cost": None, "fee_currency": None, "trade_id": trade_id,
                    }
                    new_fills_all.append(fill)
                    total_filled = filled

                    if self.order_recorder is not None:
                        await self.order_recorder.record_poll_result(
                            order_id=order_id, status=last_status,
                            filled_amount=total_filled,
                            avg_price=avg if total_filled > 0 else None,
                            fee_cost=fee_cost if fee_cost else None,
                            fee_currency=fee_currency if fee_currency else None,
                            new_fills=[fill], task_id=task_id,
                        )

                if last_status in ("closed", "canceled", "expired"):
                    return PollResult(
                        all_closed=(last_status == "closed"),
                        new_fills=new_fills_all, status=last_status,
                        filled_amount=total_filled,
                        avg_price=float(order_info.get("average", 0)) if total_filled > 0 else None,
                        fee_cost=fee_cost if fee_cost else None,
                        fee_currency=fee_currency if fee_currency else None,
                    )

            except Exception:
                pass

            await asyncio.sleep(self.interval)
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 3: Commit**

```bash
git add app/trading/order_poller.py
git commit -m "feat: OrderPoller for background ccxt fetch_order polling"
```

---

### Task 4: 改造 TradeExecutor — 注入 OrderRecorder + 写 record

**Files:**
- Modify: `app/trading/executor.py`

- [ ] **Step 1: 重写 TradeExecutor**

替换整个 executor.py：

```python
import asyncio
import uuid
from dataclasses import dataclass

from app.exchanges.adapters import OrderRequest
from app.trading.tasks import ExecutionTask


@dataclass(slots=True)
class ExecutionResult:
    status: str
    filled_exchanges: list[str]
    failed_exchanges: list[str]
    failed_errors: list[str] | None = None
    order_ids: dict[str, int] | None = None


class TradeExecutor:
    def __init__(self, *, adapter_factory: dict[str, object],
                 order_recorder=None) -> None:
        self.adapter_factory = adapter_factory
        self.order_recorder = order_recorder

    async def execute_open(self, task: ExecutionTask) -> ExecutionResult:
        tasks = []
        exchanges = []
        record_ids: dict[str, int] = {}
        order_info_by_exchange: dict[str, dict] = {}

        for leg in task.open_legs:
            exchanges.append(leg.exchange)
            client_id = f"{task.task_id}_{leg.exchange}_{uuid.uuid4().hex[:8]}"
            market_type = getattr(leg, "market_type", "spot")

            if self.order_recorder is not None:
                oid = await self.order_recorder.record_submit(
                    task_id=int(task.task_id) if task.task_id.isdigit() else 0,
                    leg_type="spot" if market_type == "spot" else "derivative",
                    exchange=leg.exchange, side=leg.side,
                    market_type=market_type, client_order_id=client_id,
                    symbol=task.symbol, order_type=leg.order_type,
                    price=leg.price, amount=leg.amount,
                )
                record_ids[leg.exchange] = oid

            adapter = self.adapter_factory[leg.exchange]
            tasks.append(
                _place_order(
                    adapter, task, leg, market_type, self.order_recorder,
                    record_ids.get(leg.exchange),
                )
            )

        responses = await asyncio.gather(*tasks, return_exceptions=True)
        filled_exchanges: list[str] = []
        failed_exchanges: list[str] = []
        failed_errors: list[str] | None = None

        for exchange, result in zip(exchanges, responses):
            if isinstance(result, Exception):
                failed_exchanges.append(exchange)
                error_str = f"{exchange}: {result}"
                if failed_errors is None:
                    failed_errors = []
                failed_errors.append(error_str)
            else:
                filled_exchanges.append(exchange)
                order_info_by_exchange[exchange] = result

        status = "OPEN_HEDGED" if not failed_exchanges else "OPEN_PARTIAL"
        return ExecutionResult(
            status=status,
            filled_exchanges=filled_exchanges,
            failed_exchanges=failed_exchanges,
            failed_errors=failed_errors,
            order_ids=record_ids if record_ids else None,
        )


async def _place_order(adapter, task, leg, market_type, recorder, record_id):
    try:
        result = await adapter.create_order(
            OrderRequest(
                symbol=task.symbol, side=leg.side,
                order_type=leg.order_type, amount=leg.amount,
                price=leg.price, market_type=market_type,
            )
        )
    except Exception:
        if recorder is not None and record_id is not None:
            await recorder.record_failed(order_id=record_id, reason=str(Exception))
        raise

    if recorder is not None and record_id is not None:
        filled = float(result.get("filled", 0) or 0)
        fee = result.get("fee")
        fee_cost = float(fee.get("cost", 0)) if isinstance(fee, dict) else None
        fee_currency = str(fee.get("currency", "")) if isinstance(fee, dict) else None
        await recorder.record_open(
            order_id=record_id,
            exchange_order_id=str(result.get("id", "")),
            avg_price=float(result.get("average", 0) or 0) if filled > 0 else None,
            filled_amount=filled,
            fee_cost=fee_cost,
            fee_currency=fee_currency,
            raw_response=result,
        )
    return result
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 3: Commit**

```bash
git add app/trading/executor.py
git commit -m "feat: inject OrderRecorder into TradeExecutor, write order records on submit/fill/fail"
```

---

### Task 5: 改造 trade_execution_service — 传递 order_recorder

**Files:**
- Modify: `app/runtime/trade_execution_service.py`

- [ ] **Step 1: 在 RuntimeTradeExecutionService 中注入 order_recorder 并构建 TradeExecutor 时传入**

读取文件找到 `RuntimeTradeExecutionService.__init__` 和 `TradeExecutor(...)` 构造处。修改 `__init__` 加 `order_recorder=None` 参数，在构造 `TradeExecutor` 时传入：

```python
# 在 __init__ 中新增参数
def __init__(self, ..., order_recorder=None):
    ...
    self.order_recorder = order_recorder

# 在构造 TradeExecutor 时:
self.executor = TradeExecutor(
    adapter_factory=...,
    order_recorder=self.order_recorder,
)
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/ --tb=short -q
```

Expected: 346 passed

- [ ] **Step 3: Commit**

```bash
git add app/runtime/trade_execution_service.py
git commit -m "feat: pass OrderRecorder through trade_execution_service to executor"
```

---

### Task 6: 改造 repair_execution_service — 注入 order_recorder

**Files:**
- Modify: `app/runtime/repair_execution_service.py`

- [ ] **Step 1: 在 __init__ 加 order_recorder 参数**

```python
def __init__(self, ..., order_recorder=None):
    ...
    self.order_recorder = order_recorder
```

- [ ] **Step 2: 在 adapter.create_order 调用前写 submitting record，后写 open/failed**

找到 `adapter.create_order(...)` 调用处（约第 66 行），在调用前：

```python
repair_order_id = None
if self.order_recorder is not None:
    repair_order_id = await self.order_recorder.record_submit(
        task_id=db_task_id, leg_type="spot", exchange=exchange,
        side=side, market_type="spot", client_order_id=f"repair_{db_task_id}_{exchange}",
        symbol=symbol, order_type="market", price=None, amount=qty,
    )
```

调用后：
```python
try:
    result = await adapter.create_order(...)
    if self.order_recorder is not None and repair_order_id is not None:
        filled = float(result.get("filled", 0) or 0)
        await self.order_recorder.record_open(
            order_id=repair_order_id, exchange_order_id=str(result.get("id", "")),
            avg_price=float(result.get("average", 0) or 0) if filled > 0 else None,
            filled_amount=filled, fee_cost=None, fee_currency=None,
            raw_response=result,
        )
except Exception:
    if self.order_recorder is not None and repair_order_id is not None:
        await self.order_recorder.record_failed(order_id=repair_order_id, reason=str(e))
    raise
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/ --tb=short -q
```

Expected: 346 passed

- [ ] **Step 4: Commit**

```bash
git add app/runtime/repair_execution_service.py
git commit -m "feat: OrderRecorder in repair path for repair order tracking"
```

---

### Task 7: 改造 live_workers — 构建 order_recorder 注入链路

**Files:**
- Modify: `app/runtime/live_workers.py`

- [ ] **Step 1: 找到 ArbitrageExecutionTaskConsumer.__init__ 和 run_once**

在 `__init__` 中加入：
```python
from app.trading.order_recorder import OrderRecorder
from app.db.session import build_session_factory
import os

self.order_recorder = OrderRecorder(
    build_session_factory(os.getenv("DATABASE_URL", "sqlite:///./furun.db"))
)
```

然后在构造 `trade_execution_service` 和 `repair_execution_service` 时传入 `order_recorder=self.order_recorder`。

- [ ] **Step 2: 找到 run_once 中调用 execution_adapter.execute_task 的地方**

在 execution_result 返回后，如果有 `order_ids`，启动 OrderPoller：

```python
from app.trading.order_poller import OrderPoller

if hasattr(result, 'order_ids') and result.order_ids:
    asyncio.create_task(self._poll_orders(task, result.order_ids))
```

新增 `_poll_orders` 方法：
```python
async def _poll_orders(self, task, order_ids: dict[str, int]):
    poller = OrderPoller(
        order_recorder=self.order_recorder,
        adapter_factory=self.execution_adapter.adapter_factory,
    )
    for exchange, oid in order_ids.items():
        asyncio.create_task(
            poller.poll_until_closed(
                order_id=oid, exchange=exchange,
                exchange_order_id="...", symbol=task.symbol,
                task_id=task.id, current_filled=0.0,
            )
        )
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/ --tb=short -q
```

Expected: 346 passed

- [ ] **Step 4: Commit**

```bash
git add app/runtime/live_workers.py
git commit -m "feat: build OrderRecorder in ArbitrageExecutionTaskConsumer, inject into execution chain"
```

---

### Task 8: 改造 positions API — 返回 fill 汇总数据

**Files:**
- Modify: `app/api/positions.py`

- [ ] **Step 1: 在 list_positions 中加入 fill 汇总**

在 `app/api/positions.py` 的 `list_positions` 函数中，返回每个 task 时附带 `order_records` + `fill_records` + `position_snapshots` 汇总：

```python
from models import OrderRecord, FillRecord, PositionSnapshot

# 在 items 构建循环中加入:
for task in items:
    orders = db.query(OrderRecord).filter(OrderRecord.task_id == task.id).all()
    fills = db.query(FillRecord).filter(FillRecord.task_id == task.id).all()
    snap = db.query(PositionSnapshot).filter(
        PositionSnapshot.task_id == task.id
    ).order_by(PositionSnapshot.created_at.desc()).first()

    total_fill_cost = sum(f.fill_cost for f in fills)
    total_fee = sum(f.fee_cost or 0 for f in fills)

    result_items.append({
        "id": task.id,
        "task_uuid": task.task_uuid,
        "symbol": task.symbol,
        "spot_exchange": task.spot_exchange,
        "derivative_exchange": task.derivative_exchange,
        "task_type": task.task_type,
        "target_notional": task.target_notional,
        "filled_notional": total_fill_cost,
        "expected_spread_bps": task.expected_spread_bps,
        "status": task.status,
        "execution_status": task.execution_status,
        "auto_recovery_status": task.auto_recovery_status,
        "realized_pnl": snap.realized_pnl if snap else task.realized_pnl,
        "unrealized_pnl": snap.unrealized_pnl if snap else None,
        "total_fee": total_fee,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    })
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/ --tb=short -q
```

Expected: 346 passed

- [ ] **Step 3: Commit**

```bash
git add app/api/positions.py
git commit -m "feat: positions API returns fill/pnl data from order/fill records"
```

---

### Task 9: 前端 — api.ts 类型扩展 + PositionsPage 改造

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/pages/PositionsPage.tsx`

- [ ] **Step 1: 扩展 PositionItem 类型**

在 `web/src/api.ts` 中找到 `PositionItem` 接口，追加字段：

```typescript
export interface PositionItem {
  id: number
  task_uuid: string
  task_type: string
  symbol: string
  spot_exchange: string
  derivative_exchange: string
  target_notional: number
  expected_spread_bps: number
  expected_funding_bps: number
  status: string
  execution_status: string | null
  auto_recovery_status: string
  failure_reason: string | null
  filled_notional: number
  realized_pnl: number | null
  unrealized_pnl: number | null
  total_fee: number
  created_at: string | null
  finished_at: string | null
}
```

- [ ] **Step 2: 更新 PositionsPage 表格**

在 `PositionsPage.tsx` 中，把表格列改为显示 `filled_notional`、`realized_pnl`、`unrealized_pnl`、`total_fee`：

将原来的"名义金额"列改为 `item.filled_notional || item.target_notional`，新增"已实现盈亏"、"未实现盈亏"、"手续费"三列。

- [ ] **Step 3: Build**

Run: `cd web && npm run build`
Expected: 0 TypeScript errors

- [ ] **Step 4: Commit**

```bash
git add web/src/api.ts web/src/pages/PositionsPage.tsx
git commit -m "feat: positions page shows real pnl from order/fill records"
```

---

### Task 10: 部署 + testnet 验证

**Files:**
- All changed files

- [ ] **Step 1: 部署到服务器**

```bash
$sshKey = "d:\old\FuRunSystemV4\.tmp-ssh\futunsystemv3_deploy_ed25519"
# scp models.py, executor.py, order_recorder.py, order_poller.py, etc.
# ssh: sudo systemctl restart furun-api
```

- [ ] **Step 2: PG 数据库迁移**

在服务器上运行迁移脚本，新增三张表 + arbitrage_tasks 补充字段。

- [ ] **Step 3: 查策略 + 余额 + Redis**

验证 testnet 环境有可用策略、账户余额充足、Redis 有行情数据。

- [ ] **Step 4: 重启 arbitrage workers**

```bash
sudo systemctl restart furun-arb-dispatcher furun-arb-executor
```

- [ ] **Step 5: 观察日志**

```bash
journalctl -u furun-arb-executor -f | grep -i 'create_order\|order_record\|filled'
```

- [ ] **Step 6: 查 DB 记录**

验证 `order_records` 表有新记录写入，`fill_records` 有对应成交，`position_snapshots` 有 snapshot。

- [ ] **Step 7: Commit + push**

```bash
git add -A
git commit -m "chore: deploy trading records closed-loop + testnet verification"
git push
```
