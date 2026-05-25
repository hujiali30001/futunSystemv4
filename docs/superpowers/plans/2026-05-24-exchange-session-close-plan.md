# Exchange Session Close Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `ExchangeAccountSession` 建立统一、幂等的关闭协议，并让 `live_spot_flow`、`sandbox_probe`、`spot_arbitrage_probe` 通过同一套关闭链路释放 ccxt 资源。

**Architecture:** 以 `ExchangeAccountSession` 作为底层交易所 client 的唯一资源所有者，在 session 层新增异步 `close()`，由 `ExchangeAdapter.close()` 做兼容性转调。现有业务层继续保留 `finally` 收口，但通过补充成功路径和异常路径测试，确保 flow 与 probe 都统一走 session 关闭协议。

**Tech Stack:** Python 3.10+, asyncio, pytest, pytest-asyncio, ccxt async adapter pattern

---

## 文件结构与职责

- `app/exchanges/session_manager.py`
  - 为 `ExchangeAccountSession` 增加 `closed` 状态与幂等 `close()`
- `app/exchanges/adapters.py`
  - 将 `ExchangeAdapter.close()` 改为转调 `self.session.close()`
- `app/runtime/live_spot_flow.py`
  - 保持 `finally` 收口，继续批量关闭 adapter，依赖 session 统一关闭
- `app/runtime/sandbox_probe.py`
  - 保持两个 probe 方法的 `finally`，统一复用 session 关闭协议
- `app/runtime/spot_arbitrage_probe.py`
  - 保持批量关闭结构，统一复用 session 关闭协议
- `tests/test_sessions.py`
  - 新增 session `close()` 的幂等与兼容行为测试
- `tests/test_live_spot_flow.py`
  - 新增 `run_once()` 成功/异常路径都会关闭 session 的测试
- `tests/test_sandbox_probe.py`
  - 新增 sandbox probe 成功/异常路径关闭 session 的测试
- `tests/test_spot_arbitrage_probe.py`
  - 新增 spot arbitrage probe 成功/异常路径关闭 session 的测试

### Task 1: Session Close 契约

**Files:**
- Modify: `app/exchanges/session_manager.py`
- Modify: `app/exchanges/adapters.py`
- Test: `tests/test_sessions.py`

- [ ] **Step 1: 写 session 关闭行为的失败测试**

```python
import pytest

from app.exchanges.session_manager import ExchangeAccountSession


class FakeClosableClient:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_session_close_is_idempotent_and_clears_client_state():
    client = FakeClosableClient()
    session = ExchangeAccountSession(
        exchange="okx",
        env_mode="testnet",
        proxies={},
        client=client,
        markets_loaded=True,
        markets={"BTC/USDT": {}},
    )

    await session.close()
    await session.close()

    assert client.close_calls == 1
    assert session.closed is True
    assert session.client is None
    assert session.markets == {}
    assert session.markets_loaded is False


@pytest.mark.asyncio
async def test_session_close_is_safe_when_client_missing_or_not_closable():
    session_without_client = ExchangeAccountSession(
        exchange="okx",
        env_mode="testnet",
        proxies={},
    )
    session_without_close = ExchangeAccountSession(
        exchange="okx",
        env_mode="testnet",
        proxies={},
        client=object(),
    )

    await session_without_client.close()
    await session_without_close.close()

    assert session_without_client.closed is True
    assert session_without_close.closed is True
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest tests/test_sessions.py -v`
Expected: FAIL，报 `ExchangeAccountSession` 没有 `close` 或没有 `closed`

- [ ] **Step 3: 实现 session 幂等关闭与 adapter 转调**

```python
@dataclass(slots=True)
class ExchangeAccountSession:
    exchange: str
    env_mode: str
    proxies: dict[str, str]
    markets_loaded: bool = False
    markets: dict[str, Any] = field(default_factory=dict)
    client: Any = field(default=None)
    closed: bool = False

    async def close(self) -> None:
        if self.closed:
            return

        client = self.client
        try:
            if client is not None and hasattr(client, "close"):
                await client.close()
        finally:
            self.closed = True
            self.client = None
            self.markets = {}
            self.markets_loaded = False
```

```python
class ExchangeAdapter:
    async def close(self) -> None:
        await self.session.close()
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `pytest tests/test_sessions.py -v`
Expected: PASS，包含新加的两个异步测试

- [ ] **Step 5: 提交这一小步**

```bash
git add app/exchanges/session_manager.py app/exchanges/adapters.py tests/test_sessions.py
git commit -m "fix: add idempotent exchange session close"
```

### Task 2: Live Spot Flow 回归

**Files:**
- Modify: `tests/test_live_spot_flow.py`
- Modify: `app/runtime/live_spot_flow.py`

- [ ] **Step 1: 补 `run_once()` 成功与异常路径关闭测试**

```python
class FakeClient:
    def __init__(self, orderbook, fail_on_fetch=False):
        self.orderbook = orderbook
        self.orderbook_calls = []
        self.fail_on_fetch = fail_on_fetch
        self.closed = False

    async def fetch_order_book(self, symbol, limit=5):
        if self.fail_on_fetch:
            raise RuntimeError("fetch orderbook failed")
        self.orderbook_calls.append((symbol, limit))
        return self.orderbook

    async def close(self):
        self.closed = True
```

```python
@pytest.mark.asyncio
async def test_live_flow_closes_all_sessions_after_success():
    redis_client = FakeRedis()
    service = FakeSpotService()
    factory = FakeFactory()
    flow = LiveSpotFlowService(
        redis_client=redis_client,
        session_factory=factory,
        spot_service=service,
    )

    await flow.run_once(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={
            "okx": ExchangeCredentials(api_key="a", secret="b"),
            "bitget": ExchangeCredentials(api_key="a", secret="b"),
            "gate": ExchangeCredentials(api_key="a", secret="b"),
        },
        symbol="BTC/USDT",
    )

    assert factory.clients["okx"].closed is True
    assert factory.clients["bitget"].closed is True
    assert factory.clients["gate"].closed is True


@pytest.mark.asyncio
async def test_live_flow_closes_created_sessions_when_fetch_fails():
    redis_client = FakeRedis()
    service = FakeSpotService()
    factory = FakeFactory()
    factory.clients["okx"].fail_on_fetch = True
    flow = LiveSpotFlowService(
        redis_client=redis_client,
        session_factory=factory,
        spot_service=service,
    )

    with pytest.raises(RuntimeError, match="fetch orderbook failed"):
        await flow.run_once(
            exchanges=["okx", "bitget", "gate"],
            credentials_by_exchange={
                "okx": ExchangeCredentials(api_key="a", secret="b"),
                "bitget": ExchangeCredentials(api_key="a", secret="b"),
                "gate": ExchangeCredentials(api_key="a", secret="b"),
            },
            symbol="BTC/USDT",
        )

    assert factory.clients["okx"].closed is True
    assert factory.clients["bitget"].closed is True
    assert factory.clients["gate"].closed is True
```

- [ ] **Step 2: 运行定向测试并确认至少一个失败**

Run: `pytest tests/test_live_spot_flow.py -v`
Expected: FAIL，新增关闭断言未满足或异常路径未覆盖

- [ ] **Step 3: 最小化修正 flow 关闭实现**

```python
finally:
    await asyncio.gather(
        *[adapter.close() for adapter in adapters.values()],
        return_exceptions=True,
    )
```

```python
for exchange in exchanges:
    session = self.session_factory.create_session(
        exchange=exchange,
        env_mode=env_mode,
        proxies=(proxies_by_exchange or {}).get(exchange, {}),
        credentials=credentials_by_exchange[exchange],
    )
    await session.mark_ready()
    sessions[exchange] = session
    adapters[exchange] = ExchangeAdapter(session)
```

- [ ] **Step 4: 重新运行 flow 测试**

Run: `pytest tests/test_live_spot_flow.py -v`
Expected: PASS，原有机会发现测试与新增关闭测试都通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/live_spot_flow.py tests/test_live_spot_flow.py
git commit -m "test: verify live flow closes exchange sessions"
```

### Task 3: Sandbox Probe 回归

**Files:**
- Modify: `tests/test_sandbox_probe.py`
- Modify: `app/runtime/sandbox_probe.py`

- [ ] **Step 1: 给 sandbox probe 的假 client 暴露关闭状态并补测试**

```python
class FakeClient:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.closed = False

    async def close(self):
        self.closed = True
```

```python
@pytest.mark.asyncio
async def test_probe_service_closes_session_after_success():
    factory = FakeFactory()
    service = SandboxProbeService(session_factory=factory)

    result = await service.probe_exchange(
        exchange="binance",
        credentials=ExchangeCredentials(api_key="k", secret="s"),
        env_mode="testnet",
        proxies={},
    )

    assert result.ok is True
    session = factory.last_session
    assert session.closed is True


@pytest.mark.asyncio
async def test_order_probe_closes_session_after_failure():
    factory = FakeFactory(should_fail=True)
    service = SandboxProbeService(session_factory=factory)

    result = await service.probe_order_lifecycle(
        exchange="gate",
        credentials=ExchangeCredentials(api_key="k", secret="s"),
        symbol="BTC/USDT",
        env_mode="testnet",
        proxies={},
    )

    assert result.ok is False
    session = factory.last_session
    assert session.closed is True
```

- [ ] **Step 2: 运行 sandbox probe 测试并确认失败**

Run: `pytest tests/test_sandbox_probe.py -v`
Expected: FAIL，`FakeFactory` 暂无 `last_session` 或 session 未标记关闭

- [ ] **Step 3: 最小化补齐测试夹具并保持业务层 finally 不变**

```python
class FakeFactory:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.last_session = None

    def create_session(self, exchange, env_mode, proxies, credentials):
        session = ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=FakeClient(should_fail=self.should_fail),
        )
        self.last_session = session
        return session
```

```python
finally:
    await adapter.close()
```

- [ ] **Step 4: 重新运行 sandbox probe 测试**

Run: `pytest tests/test_sandbox_probe.py -v`
Expected: PASS，连接探测和订单生命周期探测都验证关闭行为

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/sandbox_probe.py tests/test_sandbox_probe.py
git commit -m "test: cover sandbox probe session closing"
```

### Task 4: Spot Arbitrage Probe 回归与总验收

**Files:**
- Modify: `tests/test_spot_arbitrage_probe.py`
- Modify: `app/runtime/spot_arbitrage_probe.py`

- [ ] **Step 1: 补成功/异常路径关闭测试**

```python
class FakeClient:
    def __init__(self, exchange, bid, ask, fail_on_create=False):
        self.exchange = exchange
        self.bid = bid
        self.ask = ask
        self.fail_on_create = fail_on_create
        self.closed = False

    async def create_order(self, symbol, order_type, side, amount, price, params):
        if self.fail_on_create:
            raise RuntimeError("create order failed")
        return {
            "id": f"{self.exchange}-{side}-1",
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "params": params,
        }

    async def close(self):
        self.closed = True
```

```python
@pytest.mark.asyncio
async def test_spot_arbitrage_probe_closes_all_sessions_after_success():
    factory = FakeFactory()
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.ok is True
    assert factory.clients["okx"].closed is True
    assert factory.clients["bitget"].closed is True
    assert factory.clients["gate"].closed is True


@pytest.mark.asyncio
async def test_spot_arbitrage_probe_closes_all_sessions_after_order_failure():
    factory = FakeFactory()
    factory.clients["gate"].fail_on_create = True
    service = SpotArbitrageProbeService(session_factory=factory)
    credentials = {
        "okx": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "bitget": ExchangeCredentials(api_key="a", secret="b", password="c"),
        "gate": ExchangeCredentials(api_key="a", secret="b"),
    }

    result = await service.run_task(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange=credentials,
        symbol="BTC/USDT",
        env_mode="testnet",
    )

    assert result.ok is False
    assert factory.clients["okx"].closed is True
    assert factory.clients["bitget"].closed is True
    assert factory.clients["gate"].closed is True
```

- [ ] **Step 2: 运行 probe 测试并确认失败**

Run: `pytest tests/test_spot_arbitrage_probe.py -v`
Expected: FAIL，新增 `closed` 断言未满足或异常路径夹具未补齐

- [ ] **Step 3: 保持批量 finally，依赖 session 统一关闭**

```python
finally:
    await asyncio.gather(
        *[adapter.close() for adapter in adapters.values()],
        return_exceptions=True,
    )
```

```python
class FakeFactory:
    def __init__(self):
        self.clients = {
            "okx": FakeClient("okx", 100.0, 101.0),
            "bitget": FakeClient("bitget", 99.0, 100.0),
            "gate": FakeClient("gate", 102.0, 103.0),
        }
```

- [ ] **Step 4: 跑总回归并确认通过**

Run: `pytest tests/test_sessions.py tests/test_live_spot_flow.py tests/test_sandbox_probe.py tests/test_spot_arbitrage_probe.py -v`
Expected: PASS，全部关闭相关测试通过

- [ ] **Step 5: 完成最终提交**

```bash
git add app/exchanges/session_manager.py app/exchanges/adapters.py app/runtime/live_spot_flow.py app/runtime/sandbox_probe.py app/runtime/spot_arbitrage_probe.py tests/test_sessions.py tests/test_live_spot_flow.py tests/test_sandbox_probe.py tests/test_spot_arbitrage_probe.py
git commit -m "fix: unify exchange session close lifecycle"
```

## 自检结果

- Spec coverage:
  - session 资源所有权与幂等关闭：`Task 1`
  - adapter 转调兼容：`Task 1`
  - `live_spot_flow` 成功/异常路径收口：`Task 2`
  - `sandbox_probe` 成功/异常路径收口：`Task 3`
  - `spot_arbitrage_probe` 成功/异常路径收口：`Task 4`
  - 远端前的本地回归命令：`Task 4`
- Placeholder scan:
  - 未保留 `TODO`、`TBD`、"类似 Task N" 之类占位描述
- Type consistency:
  - 统一使用 `ExchangeAccountSession.close()`、`ExchangeAdapter.close()`、`closed` 状态字段
