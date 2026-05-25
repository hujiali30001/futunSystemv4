# SpotOpportunity Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the `SpotOpportunity` `.get()` runtime error by making the live spot flow return a consistent dataclass and explicitly serializing it only at the runtime boundaries.

**Architecture:** Keep `SpotOpportunity` as the internal return type for live spot opportunity discovery. Add one explicit payload conversion helper, update `LiveSpotFlowService` to use that helper at dispatcher boundaries, and update `ContinuousSpotScanner` to consume the dataclass via attribute access instead of treating it like a dictionary.

**Tech Stack:** Python 3.10+, dataclasses, asyncio, pytest, pytest-asyncio, redis.asyncio, ccxt async adapters

---

## Planned File Structure

**Modify**
- `app/market/opportunity.py`
- `app/runtime/live_spot_flow.py`
- `app/runtime/live_workers.py`
- `tests/test_live_spot_flow.py`
- `tests/test_live_workers.py`
- `tests/test_live_worker_alerts.py`

## Task 1: Add A Single SpotOpportunity Payload Conversion Boundary

**Files:**
- Modify: `app/market/opportunity.py`
- Modify: `tests/test_live_spot_flow.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_live_spot_flow.py
import pytest

from app.exchanges.session_manager import ExchangeAccountSession, ExchangeCredentials
from app.market.opportunity import SpotOpportunity, spot_opportunity_to_payload
from app.runtime.live_spot_flow import LiveSpotFlowService


class FakeRedis:
    def __init__(self):
        self.zadds = []
        self.xadds = []

    async def zadd(self, key, mapping):
        self.zadds.append((key, mapping))
        return 1

    async def xadd(self, key, fields):
        self.xadds.append((key, fields))
        return "1-0"


class FakeClient:
    def __init__(self, bid, ask):
        self.bid = bid
        self.ask = ask

    async def load_markets(self):
        return {"BTC/USDT": {"limits": {"amount": {"min": 0.001}}}}

    async def fetch_ticker(self, symbol):
        return {
            "symbol": symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": (self.bid + self.ask) / 2,
        }

    async def close(self):
        return None


class FakeFactory:
    def __init__(self):
        self.tickers = {
            "okx": (100.0, 101.0),
            "bitget": (99.0, 100.0),
            "gate": (102.0, 103.0),
        }

    def create_session(self, exchange, env_mode, proxies, credentials):
        bid, ask = self.tickers[exchange]
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=FakeClient(bid, ask),
        )


class FakeSpotService:
    def __init__(self):
        self.calls = []

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "message": "triggered"}


def test_spot_opportunity_to_payload_contains_runtime_boundary_fields():
    opportunity = SpotOpportunity(
        symbol="BTC/USDT",
        buy_exchange="bitget",
        sell_exchange="gate",
        buy_ask=100.0,
        sell_bid=102.0,
        spread_bps=200.0,
        redis_member="bitget:gate:BTC/USDT:1",
        timestamp=123.0,
    )

    payload = spot_opportunity_to_payload(opportunity)

    assert payload == {
        "symbol": "BTC/USDT",
        "buy_exchange": "bitget",
        "sell_exchange": "gate",
        "buy_ask": 100.0,
        "sell_bid": 102.0,
        "spread_bps": 200.0,
        "redis_member": "bitget:gate:BTC/USDT:1",
        "timestamp": 123.0,
    }


@pytest.mark.asyncio
async def test_live_flow_returns_spot_opportunity_and_dispatches_payload_from_helper():
    redis_client = FakeRedis()
    service = FakeSpotService()
    flow = LiveSpotFlowService(
        redis_client=redis_client,
        session_factory=FakeFactory(),
        spot_service=service,
    )

    result = await flow.run_once(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={
            "okx": ExchangeCredentials(api_key="a", secret="b"),
            "bitget": ExchangeCredentials(api_key="a", secret="b"),
            "gate": ExchangeCredentials(api_key="a", secret="b"),
        },
        symbol="BTC/USDT",
    )

    assert isinstance(result, SpotOpportunity)
    assert result.buy_exchange == "bitget"
    assert result.sell_exchange == "gate"
    assert service.calls[0]["exchanges"] == ["bitget", "gate"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_live_spot_flow.py -q
```

Expected: FAIL with `ImportError` because `spot_opportunity_to_payload` does not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
# app/market/opportunity.py
from dataclasses import dataclass
from time import time


@dataclass(slots=True)
class OrderbookSnapshot:
    best_bid: float
    best_ask: float
    bids: list[list[float]]
    asks: list[list[float]]


@dataclass(slots=True)
class Opportunity:
    symbol: str
    spot_exchange: str
    derivative_exchange: str
    open_spread_bps: float
    close_spread_bps: float
    funding_rate: float
    annualized_bps: float
    redis_member: str
    timestamp: float


@dataclass(slots=True)
class SpotOpportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_ask: float
    sell_bid: float
    spread_bps: float
    redis_member: str
    timestamp: float


def spot_opportunity_to_payload(opportunity: SpotOpportunity) -> dict[str, object]:
    return {
        "symbol": opportunity.symbol,
        "buy_exchange": opportunity.buy_exchange,
        "sell_exchange": opportunity.sell_exchange,
        "buy_ask": opportunity.buy_ask,
        "sell_bid": opportunity.sell_bid,
        "spread_bps": opportunity.spread_bps,
        "redis_member": opportunity.redis_member,
        "timestamp": opportunity.timestamp,
    }


class OpportunityCalculator:
    def build_opportunity(
        self,
        *,
        symbol: str,
        spot_exchange: str,
        derivative_exchange: str,
        spot: OrderbookSnapshot,
        derivative: OrderbookSnapshot,
        funding_rate: float,
    ) -> Opportunity:
        open_spread = (derivative.best_ask - spot.best_ask) / spot.best_ask
        close_spread = (derivative.best_bid - spot.best_bid) / spot.best_bid
        current_time = time()
        return Opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            open_spread_bps=open_spread * 10000,
            close_spread_bps=close_spread * 10000,
            funding_rate=funding_rate,
            annualized_bps=(open_spread + funding_rate) * 365 * 10000,
            redis_member=f"{spot_exchange}:{derivative_exchange}:{symbol}:{int(current_time * 1000)}",
            timestamp=current_time,
        )

    def build_spot_opportunity(
        self,
        *,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        buy_ask: float,
        sell_bid: float,
    ) -> SpotOpportunity:
        current_time = time()
        spread = (sell_bid - buy_ask) / buy_ask
        return SpotOpportunity(
            symbol=symbol,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            buy_ask=buy_ask,
            sell_bid=sell_bid,
            spread_bps=spread * 10000,
            redis_member=f"{buy_exchange}:{sell_exchange}:{symbol}:{int(current_time * 1000)}",
            timestamp=current_time,
        )
```

```python
# app/runtime/live_spot_flow.py
import asyncio

from app.exchanges.adapters import ExchangeAdapter
from app.exchanges.session_manager import ExchangeClientFactory
from app.market.opportunity import OpportunityCalculator, SpotOpportunity, spot_opportunity_to_payload
from app.runtime.redis_flow import MarketOpportunityPublisher, RedisOpportunityDispatcher


class LiveSpotFlowService:
    def __init__(
        self,
        *,
        redis_client,
        session_factory: ExchangeClientFactory,
        spot_service,
    ) -> None:
        self.redis_client = redis_client
        self.session_factory = session_factory
        self.spot_service = spot_service
        self.calculator = OpportunityCalculator()
        self.publisher = MarketOpportunityPublisher(
            redis_client,
            zset_key="arb:zset:spot",
            stream_key="stream:spot_opps",
        )
        self.dispatcher = RedisOpportunityDispatcher(spot_service)

    async def run_once(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        symbol: str,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> SpotOpportunity:
        sessions = {}
        adapters = {}
        try:
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

            tickers = {
                exchange: await adapters[exchange].fetch_ticker(symbol)
                for exchange in exchanges
            }
            buy_exchange = min(exchanges, key=lambda name: tickers[name]["ask"])
            sell_exchange = max(exchanges, key=lambda name: tickers[name]["bid"])
            opportunity = self.calculator.build_spot_opportunity(
                symbol=symbol,
                buy_exchange=buy_exchange,
                sell_exchange=sell_exchange,
                buy_ask=float(tickers[buy_exchange]["ask"]),
                sell_bid=float(tickers[sell_exchange]["bid"]),
            )
            await self.publisher.publish(opportunity)
            payload = spot_opportunity_to_payload(opportunity)
            await self.dispatcher.dispatch(
                {
                    "symbol": payload["symbol"],
                    "buy_exchange": payload["buy_exchange"],
                    "sell_exchange": payload["sell_exchange"],
                },
                credentials_by_exchange=credentials_by_exchange,
            )
            return opportunity
        finally:
            await asyncio.gather(
                *[adapter.close() for adapter in adapters.values()],
                return_exceptions=True,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_live_spot_flow.py -q
```

Expected: PASS with `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/market/opportunity.py app/runtime/live_spot_flow.py tests/test_live_spot_flow.py
git commit -m "feat: add explicit spot opportunity payload boundary"
```

## Task 2: Make Scanner Consume SpotOpportunity Via Attributes

**Files:**
- Modify: `app/runtime/live_workers.py`
- Modify: `tests/test_live_workers.py`
- Modify: `tests/test_live_worker_alerts.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_live_workers.py
import pytest

from app.market.opportunity import SpotOpportunity
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.live_workers import ContinuousSpotScanner, RedisSpotConsumer


class FakeFlowService:
    def __init__(self):
        self.calls = []

    async def run_once(self, **kwargs):
        self.calls.append(kwargs)
        return SpotOpportunity(
            symbol="BTC/USDT",
            buy_exchange="bitget",
            sell_exchange="gate",
            buy_ask=100.0,
            sell_bid=102.0,
            spread_bps=200.0,
            redis_member="bitget:gate:BTC/USDT:1",
            timestamp=123.0,
        )


class FakeDispatcher:
    def __init__(self):
        self.payloads = []

    async def dispatch(self, payload, *, credentials_by_exchange):
        self.payloads.append((payload, credentials_by_exchange))
        return {"ok": True}


class FakeEventRouter:
    def __init__(self):
        self.events = []

    async def dispatch(self, event: RuntimeEvent):
        self.events.append(event)


class FakeRedis:
    def __init__(self):
        self.read_calls = 0

    async def xread(self, streams, count=1, block=0):
        self.read_calls += 1
        if self.read_calls == 1:
            return [
                (
                    "stream:spot_opps",
                    [
                        (
                            "1-0",
                            {
                                "symbol": "BTC/USDT",
                                "buy_exchange": "bitget",
                                "sell_exchange": "gate",
                            },
                        )
                    ],
                )
            ]
        return []


@pytest.mark.asyncio
async def test_continuous_scanner_emits_event_from_spot_opportunity_attributes():
    service = FakeFlowService()
    router = FakeEventRouter()
    scanner = ContinuousSpotScanner(
        flow_service=service,
        poll_interval_seconds=0.0,
        event_router=router,
        region="default",
    )

    await scanner.run(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "bitget": object(), "gate": object()},
        symbol="BTC/USDT",
        max_iterations=1,
    )

    assert router.events[0].event_type == "opportunity.detected"
    assert router.events[0].payload["buy_exchange"] == "bitget"
    assert router.events[0].payload["sell_exchange"] == "gate"
    assert router.events[0].payload["spread_bps"] == 200.0
```

```python
# tests/test_live_worker_alerts.py
import pytest

from app.market.opportunity import SpotOpportunity
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.live_workers import ContinuousSpotScanner, RedisSpotConsumer


class FakeEventRouter:
    def __init__(self):
        self.events = []

    async def dispatch(self, event: RuntimeEvent):
        self.events.append(event)


class FakeFlowService:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    async def run_once(self, **kwargs):
        if self.should_fail:
            raise RuntimeError("scanner failed")
        return SpotOpportunity(
            symbol=kwargs["symbol"],
            buy_exchange="bitget",
            sell_exchange="gate",
            buy_ask=100.0,
            sell_bid=100.88,
            spread_bps=88.0,
            redis_member="bitget:gate:BTC/USDT:1",
            timestamp=123.0,
        )


class FakeDispatcher:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    async def dispatch(self, payload, *, credentials_by_exchange):
        if self.should_fail:
            raise RuntimeError("dispatch failed")
        return {"ok": True}


class FakeRedis:
    async def xread(self, streams, count=1, block=0):
        return [
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "88.0",
                        },
                    )
                ],
            )
        ]


@pytest.mark.asyncio
async def test_scanner_emits_opportunity_detected_event_from_dataclass_result():
    router = FakeEventRouter()
    scanner = ContinuousSpotScanner(
        flow_service=FakeFlowService(),
        poll_interval_seconds=0.0,
        event_router=router,
        region="default",
    )

    await scanner.run(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "bitget": object(), "gate": object()},
        symbol="BTC/USDT",
        max_iterations=1,
    )

    assert [event.event_type for event in router.events] == [
        "opportunity.detected",
        "scanner.iteration.succeeded",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_live_workers.py tests/test_live_worker_alerts.py -q
```

Expected: FAIL with `'SpotOpportunity' object has no attribute 'get'` because `ContinuousSpotScanner` still treats the result like a dict.

- [ ] **Step 3: Write the minimal implementation**

```python
# app/runtime/live_workers.py
import asyncio

from app.runtime.runtime_events import RuntimeEvent


class ContinuousSpotScanner:
    def __init__(
        self,
        *,
        flow_service,
        poll_interval_seconds: float = 1.0,
        event_router=None,
        region: str = "default",
    ) -> None:
        self.flow_service = flow_service
        self.poll_interval_seconds = poll_interval_seconds
        self.event_router = event_router
        self.region = region

    async def run(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        symbol: str,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
        max_iterations: int | None = None,
    ) -> None:
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            try:
                result = await self.flow_service.run_once(
                    exchanges=exchanges,
                    credentials_by_exchange=credentials_by_exchange,
                    symbol=symbol,
                    env_mode=env_mode,
                    proxies_by_exchange=proxies_by_exchange,
                )
                if self.event_router is not None and result is not None:
                    await self.event_router.dispatch(
                        RuntimeEvent(
                            event_type="opportunity.detected",
                            level="INFO",
                            service="scanner",
                            region=self.region,
                            symbol=symbol,
                            message="opportunity detected",
                            payload={
                                "buy_exchange": result.buy_exchange,
                                "sell_exchange": result.sell_exchange,
                                "spread_bps": result.spread_bps,
                            },
                        )
                    )
                    await self.event_router.dispatch(
                        RuntimeEvent(
                            event_type="scanner.iteration.succeeded",
                            level="INFO",
                            service="scanner",
                            region=self.region,
                            symbol=symbol,
                            message="scanner iteration succeeded",
                            payload={"exchanges": exchanges},
                        )
                    )
            except Exception as exc:
                if self.event_router is not None:
                    await self.event_router.dispatch(
                        RuntimeEvent(
                            event_type="scanner.iteration.failed",
                            level="ERROR",
                            service="scanner",
                            region=self.region,
                            symbol=symbol,
                            message="scanner iteration failed",
                            payload={"error": str(exc)},
                        )
                    )
            iteration += 1
            if max_iterations is None or iteration < max_iterations:
                await asyncio.sleep(self.poll_interval_seconds)


class RedisSpotConsumer:
    def __init__(
        self,
        *,
        redis_client,
        dispatcher,
        stream_key: str,
        block_ms: int = 1000,
        event_router=None,
        region: str = "default",
    ) -> None:
        self.redis_client = redis_client
        self.dispatcher = dispatcher
        self.stream_key = stream_key
        self.block_ms = block_ms
        self.event_router = event_router
        self.region = region
        self.last_id = "0-0"

    async def run(
        self,
        *,
        credentials_by_exchange: dict,
        max_iterations: int | None = None,
    ) -> int:
        iteration = 0
        processed = 0
        while max_iterations is None or iteration < max_iterations:
            entries = await self.redis_client.xread(
                {self.stream_key: self.last_id},
                count=1,
                block=self.block_ms,
            )
            for _, messages in entries:
                for message_id, payload in messages:
                    try:
                        await self.dispatcher.dispatch(
                            payload,
                            credentials_by_exchange=credentials_by_exchange,
                        )
                        self.last_id = message_id
                        processed += 1
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                RuntimeEvent(
                                    event_type="consumer.message.processed",
                                    level="INFO",
                                    service="consumer",
                                    region=self.region,
                                    symbol=payload.get("symbol"),
                                    message="consumer message processed",
                                    payload={
                                        "message_id": message_id,
                                        "buy_exchange": payload.get("buy_exchange"),
                                        "sell_exchange": payload.get("sell_exchange"),
                                        "spread_bps": float(payload.get("spread_bps", 0.0)),
                                    },
                                )
                            )
                    except Exception as exc:
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                RuntimeEvent(
                                    event_type="consumer.message.failed",
                                    level="ERROR",
                                    service="consumer",
                                    region=self.region,
                                    symbol=payload.get("symbol"),
                                    message="consumer message failed",
                                    payload={
                                        "message_id": message_id,
                                        "error": str(exc),
                                    },
                                )
                            )
            iteration += 1
        return processed
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests/test_live_workers.py tests/test_live_worker_alerts.py -q
```

Expected: PASS with `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py tests/test_live_worker_alerts.py
git commit -m "fix: consume spot opportunity dataclass in scanner"
```

## Task 3: Run Full Regression And Validate The Remote Scanner No Longer Throws The `.get()` Error

**Files:**
- Modify: `docs/ops/live-workers-systemd.md`

- [ ] **Step 1: Run the local test suite**

Run:

```powershell
& "C:\Program Files\Python310\python.exe" -m pytest tests -q
```

Expected: PASS with the existing suite plus the new SpotOpportunity boundary tests.

- [ ] **Step 2: Sync the changed files to the remote host**

Run:

```powershell
$keyDir = "d:\old\FuRunSystemV4\.tmp-ssh"
New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
$keyPath = Join-Path $keyDir "futunsystemv3_deploy_ed25519"
Copy-Item -Force "d:\old\FuRunSystemV4\.keys\futunsystemv3_deploy_ed25519" $keyPath
& "C:\Windows\System32\icacls.exe" $keyPath /inheritance:r | Out-Null
& "C:\Windows\System32\icacls.exe" $keyPath /grant:r "${env:USERNAME}:(R)" | Out-Null

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\market\opportunity.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/market/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\runtime\live_spot_flow.py" `
  "d:\old\FuRunSystemV4\app\runtime\live_workers.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/runtime/
```

Expected: upload completes without path errors.

- [ ] **Step 3: Restart the remote workers and verify the scanner error disappears**

Run:

```powershell
& "C:\Windows\System32\OpenSSH\ssh.exe" -o StrictHostKeyChecking=no -i $keyPath ubuntu@43.165.166.57 @"
set -e
cd /home/ubuntu/furunsystemv4/current
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-consumer.service
sleep 5
printf 'scanner='
systemctl is-active furun-spot-scanner.service
printf 'consumer='
systemctl is-active furun-spot-consumer.service
printf 'zcard='
redis-cli ZCARD arb:zset:spot
printf 'xlen='
redis-cli XLEN stream:spot_opps
echo 'scanner_logs'
sudo journalctl -u furun-spot-scanner.service -n 50 --no-pager
"@
```

Expected:
- both workers are `active`
- the recent scanner logs no longer contain `SpotOpportunity object has no attribute 'get'`
- `arb:zset:spot` and `stream:spot_opps` keep growing

- [ ] **Step 4: Update the ops doc if the actual validation steps differ**

```markdown
# docs/ops/live-workers-systemd.md
## Scanner Recovery Notes

- If scanner logs show `SpotOpportunity object has no attribute 'get'`, sync the latest `app/market/opportunity.py`, `app/runtime/live_spot_flow.py`, and `app/runtime/live_workers.py`.
- The scanner success path now treats `LiveSpotFlowService.run_once()` as returning a `SpotOpportunity` dataclass.
- Runtime event payloads are built from dataclass attributes, not from `result.get(...)`.
```

- [ ] **Step 5: Commit**

```bash
git add docs/ops/live-workers-systemd.md
git commit -m "docs: record spot opportunity boundary recovery"
```

## Coverage Check

- Explicit `SpotOpportunity` payload conversion helper: Task 1
- `LiveSpotFlowService.run_once()` stable dataclass return: Task 1
- `ContinuousSpotScanner` dataclass attribute access: Task 2
- Local regression plus remote `.get()` error verification: Task 3

## Self-Review

- Spec coverage: the plan fixes the `.get()` runtime error, keeps the internal chain dataclass-based, adds a single explicit serialization boundary, and validates the remote scanner recovery.
- Placeholder scan: the plan uses exact files, exact test code, and exact remote commands with concrete expectations.
- Type consistency: `SpotOpportunity`, `spot_opportunity_to_payload()`, `LiveSpotFlowService.run_once()`, and `ContinuousSpotScanner` all use the same dataclass boundary throughout the tasks.
