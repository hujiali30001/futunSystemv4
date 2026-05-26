# Cross-Exchange Arbitrage Opportunity Semantics Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated cross-exchange arbitrage opportunity model, Redis publishing path, and minimal runtime entry without breaking the existing `spot` opportunity pipeline.

**Architecture:** Keep `SpotOpportunity` and the current `arb:zset:spot` / `stream:spot_opps` path unchanged. Add a parallel `ArbitrageOpportunity` boundary with its own payload helper and `ArbitrageOpportunityPublisher`, then add a minimal runtime flow that turns normalized spot/derivative snapshots into `OPEN` and `CLOSE` opportunity events on the new Redis keys.

**Tech Stack:** Python 3.10, `asyncio`, `pytest`, Redis ZSET / Stream publishing, existing runtime service patterns in `app/market` and `app/runtime`.

---

### Task 1: Add Arbitrage Opportunity Primitives

**Files:**
- Modify: `app/market/opportunity.py`
- Modify: `market_scanner.py`
- Test: `tests/test_market_scanner.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_market_scanner.py`:

```python
from app.market.opportunity import (
    ArbitrageOpportunity,
    OpportunityCalculator,
    OrderbookSnapshot,
    arbitrage_opportunity_to_payload,
)
from market_scanner import ArbitrageOpportunity as ExportedArbitrageOpportunity


def test_arbitrage_opportunity_to_payload_contains_open_close_boundary_fields():
    opportunity = ArbitrageOpportunity(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        opportunity_type="OPEN",
        open_spread_bps=120.5,
        close_spread_bps=90.25,
        funding_rate=0.0004,
        annualized_bps=150.0,
        redis_member="binance:okx:BTC/USDT:OPEN:1",
        timestamp=123.0,
    )

    payload = arbitrage_opportunity_to_payload(opportunity)

    assert payload == {
        "symbol": "BTC/USDT",
        "spot_exchange": "binance",
        "derivative_exchange": "okx",
        "opportunity_type": "OPEN",
        "open_spread_bps": 120.5,
        "close_spread_bps": 90.25,
        "funding_rate": 0.0004,
        "annualized_bps": 150.0,
        "redis_member": "binance:okx:BTC/USDT:OPEN:1",
        "timestamp": 123.0,
    }


def test_calculator_builds_arbitrage_opportunity_with_explicit_type():
    calculator = OpportunityCalculator()
    spot = OrderbookSnapshot(
        best_bid=100.0,
        best_ask=101.0,
        bids=[[100.0, 5.0]],
        asks=[[101.0, 5.0]],
    )
    derivative = OrderbookSnapshot(
        best_bid=104.0,
        best_ask=105.0,
        bids=[[104.0, 4.0]],
        asks=[[105.0, 4.0]],
    )

    result = calculator.build_arbitrage_opportunity(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        spot=spot,
        derivative=derivative,
        funding_rate=0.0005,
        opportunity_type="CLOSE",
    )

    assert result.opportunity_type == "CLOSE"
    assert round(result.open_spread_bps, 2) == round(((105.0 - 101.0) / 101.0) * 10000, 2)
    assert round(result.close_spread_bps, 2) == round(((104.0 - 100.0) / 100.0) * 10000, 2)
    assert result.redis_member.startswith("binance:okx:BTC/USDT:CLOSE:")


def test_market_scanner_exports_arbitrage_opportunity_name():
    assert ExportedArbitrageOpportunity.__name__ == "ArbitrageOpportunity"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_market_scanner.py -q
```

Expected:

```text
FAIL tests/test_market_scanner.py::test_arbitrage_opportunity_to_payload_contains_open_close_boundary_fields
FAIL tests/test_market_scanner.py::test_calculator_builds_arbitrage_opportunity_with_explicit_type
FAIL tests/test_market_scanner.py::test_market_scanner_exports_arbitrage_opportunity_name
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/market/opportunity.py` to add the new dataclass, payload helper, and calculator method:

```python
@dataclass(slots=True)
class ArbitrageOpportunity:
    symbol: str
    spot_exchange: str
    derivative_exchange: str
    opportunity_type: str
    open_spread_bps: float
    close_spread_bps: float
    funding_rate: float
    annualized_bps: float
    redis_member: str
    timestamp: float


def arbitrage_opportunity_to_payload(
    opportunity: ArbitrageOpportunity,
) -> dict[str, object]:
    return {
        "symbol": opportunity.symbol,
        "spot_exchange": opportunity.spot_exchange,
        "derivative_exchange": opportunity.derivative_exchange,
        "opportunity_type": opportunity.opportunity_type,
        "open_spread_bps": opportunity.open_spread_bps,
        "close_spread_bps": opportunity.close_spread_bps,
        "funding_rate": opportunity.funding_rate,
        "annualized_bps": opportunity.annualized_bps,
        "redis_member": opportunity.redis_member,
        "timestamp": opportunity.timestamp,
    }


class OpportunityCalculator:
    def build_arbitrage_opportunity(
        self,
        *,
        symbol: str,
        spot_exchange: str,
        derivative_exchange: str,
        spot: OrderbookSnapshot,
        derivative: OrderbookSnapshot,
        funding_rate: float,
        opportunity_type: str,
    ) -> ArbitrageOpportunity:
        if opportunity_type not in {"OPEN", "CLOSE"}:
            raise ValueError(f"unsupported opportunity_type: {opportunity_type}")

        open_spread = (derivative.best_ask - spot.best_ask) / spot.best_ask
        close_spread = (derivative.best_bid - spot.best_bid) / spot.best_bid
        current_time = time()
        return ArbitrageOpportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            opportunity_type=opportunity_type,
            open_spread_bps=open_spread * 10000,
            close_spread_bps=close_spread * 10000,
            funding_rate=funding_rate,
            annualized_bps=(open_spread + funding_rate) * 365 * 10000,
            redis_member=(
                f"{spot_exchange}:{derivative_exchange}:{symbol}:"
                f"{opportunity_type}:{int(current_time * 1000)}"
            ),
            timestamp=current_time,
        )

    def build_opportunity(
        self,
        *,
        symbol: str,
        spot_exchange: str,
        derivative_exchange: str,
        spot: OrderbookSnapshot,
        derivative: OrderbookSnapshot,
        funding_rate: float,
    ) -> ArbitrageOpportunity:
        return self.build_arbitrage_opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            spot=spot,
            derivative=derivative,
            funding_rate=funding_rate,
            opportunity_type="OPEN",
        )
```

Update `market_scanner.py` exports:

```python
from app.market.opportunity import (
    ArbitrageOpportunity,
    Opportunity,
    OpportunityCalculator,
    OrderbookSnapshot,
    SpotOpportunity,
)

__all__ = [
    "ArbitrageOpportunity",
    "Opportunity",
    "OpportunityCalculator",
    "OrderbookSnapshot",
    "SpotOpportunity",
]
```

Keep `Opportunity = ArbitrageOpportunity` as a backward-compatibility alias in `app/market/opportunity.py` so the older `tests/test_market_scanner.py` imports still pass while new code moves to the explicit name.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_market_scanner.py -q
```

Expected:

```text
8 passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/market/opportunity.py market_scanner.py tests/test_market_scanner.py
git commit -m "feat: add arbitrage opportunity primitives"
```

### Task 2: Add Redis Publisher For OPEN/CLOSE Opportunities

**Files:**
- Modify: `app/runtime/redis_flow.py`
- Test: `tests/test_redis_opportunity_flow.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_redis_opportunity_flow.py`:

```python
from app.market.opportunity import ArbitrageOpportunity
from app.runtime.redis_flow import ArbitrageOpportunityPublisher


@pytest.mark.asyncio
async def test_arbitrage_publisher_writes_open_opportunity_to_open_zset_and_stream():
    redis_client = FakeRedis()
    publisher = ArbitrageOpportunityPublisher(redis_client)
    opportunity = ArbitrageOpportunity(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        opportunity_type="OPEN",
        open_spread_bps=120.5,
        close_spread_bps=90.25,
        funding_rate=0.0004,
        annualized_bps=150.0,
        redis_member="binance:okx:BTC/USDT:OPEN:1",
        timestamp=1.0,
    )

    await publisher.publish(opportunity)

    assert redis_client.zadds[0] == (
        "arb:zset:open",
        {"binance:okx:BTC/USDT:OPEN:1": 120.5},
    )
    assert redis_client.xadds[0][0] == "stream:opportunities"
    assert redis_client.xadds[0][1]["opportunity_type"] == "OPEN"
    assert redis_client.xadds[0][1]["annualized_bps"] == "150.0"


@pytest.mark.asyncio
async def test_arbitrage_publisher_writes_close_opportunity_to_close_zset_and_stream():
    redis_client = FakeRedis()
    publisher = ArbitrageOpportunityPublisher(redis_client)
    opportunity = ArbitrageOpportunity(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        opportunity_type="CLOSE",
        open_spread_bps=120.5,
        close_spread_bps=90.25,
        funding_rate=0.0004,
        annualized_bps=150.0,
        redis_member="binance:okx:BTC/USDT:CLOSE:1",
        timestamp=1.0,
    )

    await publisher.publish(opportunity)

    assert redis_client.zadds[0] == (
        "arb:zset:close",
        {"binance:okx:BTC/USDT:CLOSE:1": 90.25},
    )
    assert redis_client.xadds[0][0] == "stream:opportunities"
    assert redis_client.xadds[0][1]["opportunity_type"] == "CLOSE"


@pytest.mark.asyncio
async def test_spot_publisher_behavior_stays_unchanged_alongside_arbitrage_publisher():
    redis_client = FakeRedis()
    spot_publisher = MarketOpportunityPublisher(
        redis_client,
        zset_key="arb:zset:spot",
        stream_key="stream:spot_opps",
    )
    arb_publisher = ArbitrageOpportunityPublisher(redis_client)
    spot_opportunity = SpotOpportunity(
        symbol="BTC/USDT",
        buy_exchange="bitget",
        sell_exchange="gate",
        buy_ask=100.0,
        sell_bid=102.0,
        spread_bps=200.0,
        redis_member="bitget:gate:BTC/USDT:1",
        timestamp=1.0,
        effective_buy_price=100.5,
        effective_sell_price=101.5,
        target_quote_amount=100.0,
        buy_depth_levels_used=2,
        sell_depth_levels_used=3,
    )
    arb_opportunity = ArbitrageOpportunity(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        opportunity_type="OPEN",
        open_spread_bps=120.5,
        close_spread_bps=90.25,
        funding_rate=0.0004,
        annualized_bps=150.0,
        redis_member="binance:okx:BTC/USDT:OPEN:1",
        timestamp=1.0,
    )

    await spot_publisher.publish(spot_opportunity)
    await arb_publisher.publish(arb_opportunity)

    assert redis_client.xadds[0][0] == "stream:spot_opps"
    assert redis_client.xadds[1][0] == "stream:opportunities"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_redis_opportunity_flow.py -q
```

Expected:

```text
FAIL tests/test_redis_opportunity_flow.py::test_arbitrage_publisher_writes_open_opportunity_to_open_zset_and_stream
FAIL tests/test_redis_opportunity_flow.py::test_arbitrage_publisher_writes_close_opportunity_to_close_zset_and_stream
```

- [ ] **Step 3: Write the minimal implementation**

Update `app/runtime/redis_flow.py`:

```python
from app.market.opportunity import (
    ArbitrageOpportunity,
    SpotOpportunity,
    arbitrage_opportunity_to_payload,
)


class ArbitrageOpportunityPublisher:
    STREAM_KEY = "stream:opportunities"
    ZSET_BY_TYPE = {
        "OPEN": ("arb:zset:open", "open_spread_bps"),
        "CLOSE": ("arb:zset:close", "close_spread_bps"),
    }

    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    async def publish(self, opportunity: ArbitrageOpportunity) -> None:
        zset_key, score_field = self.ZSET_BY_TYPE[opportunity.opportunity_type]
        score = getattr(opportunity, score_field)
        payload = {
            key: str(value)
            for key, value in arbitrage_opportunity_to_payload(opportunity).items()
        }
        await self.redis_client.zadd(
            zset_key,
            {opportunity.redis_member: score},
        )
        await self.redis_client.xadd(self.STREAM_KEY, payload)
```

Do not change `MarketOpportunityPublisher.publish()` behavior in this task.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_redis_opportunity_flow.py -q
```

Expected:

```text
19 passed
```

- [ ] **Step 5: Commit**

Run:

```bash
git add app/runtime/redis_flow.py tests/test_redis_opportunity_flow.py
git commit -m "feat: add arbitrage opportunity publisher"
```

### Task 3: Add Minimal Runtime Flow For Arbitrage Opportunity Publishing

**Files:**
- Create: `app/runtime/live_arbitrage_flow.py`
- Create: `tests/test_live_arbitrage_flow.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_arbitrage_flow.py`:

```python
import pytest

from app.market.opportunity import OrderbookSnapshot
from app.runtime.live_arbitrage_flow import LiveArbitrageFlowService


class FakePublisher:
    def __init__(self):
        self.published = []

    async def publish(self, opportunity):
        self.published.append(opportunity)


@pytest.mark.asyncio
async def test_live_arbitrage_flow_builds_and_publishes_open_and_close_opportunities():
    publisher = FakePublisher()
    flow = LiveArbitrageFlowService(publisher=publisher)
    spot = OrderbookSnapshot(
        best_bid=100.0,
        best_ask=101.0,
        bids=[[100.0, 5.0]],
        asks=[[101.0, 5.0]],
    )
    derivative = OrderbookSnapshot(
        best_bid=104.0,
        best_ask=105.0,
        bids=[[104.0, 4.0]],
        asks=[[105.0, 4.0]],
    )

    open_opportunity, close_opportunity = await flow.publish_snapshots(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        spot_snapshot=spot,
        derivative_snapshot=derivative,
        funding_rate=0.0005,
    )

    assert open_opportunity.opportunity_type == "OPEN"
    assert close_opportunity.opportunity_type == "CLOSE"
    assert len(publisher.published) == 2
    assert [opp.opportunity_type for opp in publisher.published] == ["OPEN", "CLOSE"]


@pytest.mark.asyncio
async def test_live_arbitrage_flow_returns_published_opportunities_in_order():
    publisher = FakePublisher()
    flow = LiveArbitrageFlowService(publisher=publisher)
    spot = OrderbookSnapshot(
        best_bid=90.0,
        best_ask=91.0,
        bids=[[90.0, 5.0]],
        asks=[[91.0, 5.0]],
    )
    derivative = OrderbookSnapshot(
        best_bid=94.0,
        best_ask=95.0,
        bids=[[94.0, 4.0]],
        asks=[[95.0, 4.0]],
    )

    opportunities = await flow.publish_snapshots(
        symbol="ETH/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        spot_snapshot=spot,
        derivative_snapshot=derivative,
        funding_rate=0.0001,
    )

    assert [opp.opportunity_type for opp in opportunities] == ["OPEN", "CLOSE"]
    assert opportunities[0].symbol == "ETH/USDT"
    assert opportunities[1].symbol == "ETH/USDT"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m pytest tests/test_live_arbitrage_flow.py -q
```

Expected:

```text
ERROR tests/test_live_arbitrage_flow.py - ModuleNotFoundError: No module named 'app.runtime.live_arbitrage_flow'
```

- [ ] **Step 3: Write the minimal implementation**

Create `app/runtime/live_arbitrage_flow.py`:

```python
from app.market.opportunity import OpportunityCalculator


class LiveArbitrageFlowService:
    def __init__(self, *, publisher) -> None:
        self.publisher = publisher
        self.calculator = OpportunityCalculator()

    async def publish_snapshots(
        self,
        *,
        symbol: str,
        spot_exchange: str,
        derivative_exchange: str,
        spot_snapshot,
        derivative_snapshot,
        funding_rate: float,
    ):
        open_opportunity = self.calculator.build_arbitrage_opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            spot=spot_snapshot,
            derivative=derivative_snapshot,
            funding_rate=funding_rate,
            opportunity_type="OPEN",
        )
        close_opportunity = self.calculator.build_arbitrage_opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            spot=spot_snapshot,
            derivative=derivative_snapshot,
            funding_rate=funding_rate,
            opportunity_type="CLOSE",
        )
        await self.publisher.publish(open_opportunity)
        await self.publisher.publish(close_opportunity)
        return open_opportunity, close_opportunity
```

Keep this service intentionally narrow: it accepts normalized snapshots and publishes the two opportunity types. Do not couple it to `dispatcher`, worker routing, or exchange-session setup in this task.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
python -m pytest tests/test_live_arbitrage_flow.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Run the focused regression suite**

Run:

```bash
python -m pytest tests/test_market_scanner.py tests/test_redis_opportunity_flow.py tests/test_live_spot_flow.py tests/test_live_arbitrage_flow.py -q
```

Expected:

```text
34 passed
```

- [ ] **Step 6: Commit**

Run:

```bash
git add app/runtime/live_arbitrage_flow.py tests/test_live_arbitrage_flow.py
git commit -m "feat: add live arbitrage opportunity flow"
```

### Task 4: Final Sanity Check

**Files:**
- Review: `docs/superpowers/specs/2026-05-26-cross-exchange-arbitrage-opportunity-semantics-upgrade-design.md`
- Review: `docs/superpowers/plans/2026-05-26-cross-exchange-arbitrage-opportunity-semantics-upgrade-plan.md`

- [ ] **Step 1: Re-read the spec and confirm plan coverage**

Check that the finished implementation covers:

```text
- ArbitrageOpportunity model
- stream:opportunities publisher
- arb:zset:open / arb:zset:close routing
- spot pipeline compatibility
- minimal runtime publishing entry
```

- [ ] **Step 2: Run git status**

Run:

```bash
git status --short
```

Expected:

```text
working tree clean
```

- [ ] **Step 3: Push only after user approval**

Run:

```bash
git log --oneline -n 3
```

Expected:

```text
shows the three B1 implementation commits at the top
```
