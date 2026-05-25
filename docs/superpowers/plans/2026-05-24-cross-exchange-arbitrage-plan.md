# Cross-Exchange Arbitrage System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-oriented Python arbitrage service that scans shared market data, enforces admin controls, and executes per-user hedged spot/futures orders through isolated proxy-backed exchange sessions.

**Architecture:** Keep the user-facing entry files `config.py`, `models.py`, `market_scanner.py`, `trading_engine.py`, and `main.py`, but push most logic into focused package modules under `app/`. The first implementation ships a single deployable codebase with clear internal boundaries for market scanning, control-plane checks, account session management, execution, and automatic risk repair.

**Tech Stack:** Python 3.10+, asyncio, SQLAlchemy 2.x, redis.asyncio, ccxt, ccxt.pro, pydantic-settings, pytest, pytest-asyncio

---

## Planned File Structure

**Create**
- `requirements.txt`
- `app/__init__.py`
- `app/core/settings.py`
- `app/core/types.py`
- `app/market/opportunity.py`
- `app/admin/control_plane.py`
- `app/admin/notifier.py`
- `app/exchanges/session_manager.py`
- `app/exchanges/adapters.py`
- `app/trading/tasks.py`
- `app/trading/executor.py`
- `app/trading/risk_manager.py`
- `app/runtime/router.py`
- `app/runtime/bootstrap.py`
- `app/monitoring/metrics.py`
- `app/audit/logger.py`
- `tests/test_config.py`
- `tests/test_models.py`
- `tests/test_market_scanner.py`
- `tests/test_control_plane.py`
- `tests/test_sessions.py`
- `tests/test_trading_engine.py`
- `tests/test_runtime.py`
- `tests/test_monitoring.py`

**Modify**
- `config.py`
- `models.py`
- `market_scanner.py`
- `trading_engine.py`
- `main.py`

## Task 1: Bootstrap Settings And Project Skeleton

**Files:**
- Create: `requirements.txt`
- Create: `app/__init__.py`
- Create: `app/core/settings.py`
- Modify: `config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from config import Settings, get_settings


def test_settings_parse_exchange_and_region_lists():
    settings = Settings(
        database_url="sqlite+aiosqlite:///unit.db",
        redis_url="redis://localhost:6379/0",
        enabled_exchanges=["binance", "okx"],
        enabled_regions=["sg", "hk"],
    )

    assert settings.enabled_exchanges == ["binance", "okx"]
    assert settings.enabled_regions == ["sg", "hk"]


def test_get_settings_returns_cached_instance():
    first = get_settings()
    second = get_settings()

    assert first is second
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` because `Settings` and `get_settings` do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/settings.py
from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "cross-exchange-arbitrage"
    env: str = "dev"
    database_url: str = "sqlite+aiosqlite:///local.db"
    redis_url: str = "redis://localhost:6379/0"
    enabled_exchanges: List[str] = Field(default_factory=lambda: ["okx", "binance", "bybit", "bitget", "gate"])
    enabled_regions: List[str] = Field(default_factory=lambda: ["default"])
    default_region: str = "default"
    redis_opportunity_key: str = "arb:zset:open"
    redis_close_key: str = "arb:zset:close"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

```python
# config.py
from app.core.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
```

```text
# requirements.txt
sqlalchemy>=2.0
aiosqlite>=0.20
redis>=5.0
pydantic>=2.8
pydantic-settings>=2.4
ccxt>=4.4
pytest>=8.0
pytest-asyncio>=0.23
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -q`
Expected: PASS with `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt app/__init__.py app/core/settings.py config.py tests/test_config.py
git commit -m "feat: bootstrap settings and project skeleton"
```

## Task 2: Implement ORM Models For Users, Accounts, Admin, And Tasks

**Files:**
- Modify: `models.py`
- Create: `app/core/types.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from sqlalchemy import inspect

from models import Base, User, ExchangeAccount, RiskLimitRule, Announcement


def test_core_tables_expose_expected_columns():
    user_columns = {column.key for column in inspect(User).columns}
    account_columns = {column.key for column in inspect(ExchangeAccount).columns}
    limit_columns = {column.key for column in inspect(RiskLimitRule).columns}
    announcement_columns = {column.key for column in inspect(Announcement).columns}

    assert {"id", "username", "home_region", "is_trading_enabled"} <= user_columns
    assert {"id", "user_id", "exchange", "proxy_id", "env_mode"} <= account_columns
    assert {"scope_type", "limit_type", "limit_value", "priority"} <= limit_columns
    assert {"title", "content", "channels_json", "status"} <= announcement_columns
    assert Base.metadata.tables["arbitrage_tasks"].name == "arbitrage_tasks"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -q`
Expected: FAIL because SQLAlchemy models are not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/types.py
from enum import StrEnum


class EnvironmentMode(StrEnum):
    TESTNET = "testnet"
    MAINNET = "mainnet"


class ScopeType(StrEnum):
    PLATFORM = "platform"
    USER = "user"
    EXCHANGE = "exchange"
    SYMBOL = "symbol"
    STRATEGY = "strategy"


class LimitType(StrEnum):
    TOTAL_NOTIONAL = "total_notional"
    SINGLE_TASK_NOTIONAL = "single_task_notional"
    EXCHANGE_NOTIONAL = "exchange_notional"
    SYMBOL_NOTIONAL = "symbol_notional"
```

```python
# models.py
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.types import EnvironmentMode


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    risk_level: Mapped[str] = mapped_column(String(32), default="standard")
    home_region: Mapped[str] = mapped_column(String(32), default="default")
    is_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ExchangeAccount(TimestampMixin, Base):
    __tablename__ = "exchange_accounts"
    __table_args__ = (UniqueConstraint("user_id", "exchange", "account_label", "env_mode", name="uq_exchange_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    account_label: Mapped[str] = mapped_column(String(64), default="default")
    market_type_scope: Mapped[str] = mapped_column(String(32), default="spot,swap")
    env_mode: Mapped[str] = mapped_column(String(16), default=EnvironmentMode.TESTNET.value)
    api_key_ciphertext: Mapped[str] = mapped_column(Text)
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    passphrase_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id"), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_auto_trade_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    account_region: Mapped[str] = mapped_column(String(32), default="default")

    user: Mapped["User"] = relationship()


class Proxy(TimestampMixin, Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proxy_type: Mapped[str] = mapped_column(String(16), default="http")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(String(32), default="default")
    provider: Mapped[str] = mapped_column(String(64), default="manual")
    health_status: Mapped[str] = mapped_column(String(32), default="unknown")


class ArbitrageTask(TimestampMixin, Base):
    __tablename__ = "arbitrage_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opportunity_id: Mapped[str] = mapped_column(String(128), index=True)
    env_mode: Mapped[str] = mapped_column(String(16), default=EnvironmentMode.TESTNET.value)
    task_type: Mapped[str] = mapped_column(String(32), default="open")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    spot_exchange: Mapped[str] = mapped_column(String(32))
    derivative_exchange: Mapped[str] = mapped_column(String(32))
    target_notional: Mapped[float] = mapped_column(Float)
    expected_spread_bps: Mapped[float] = mapped_column(Float)
    expected_funding_bps: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="CREATED")
    status_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    home_region: Mapped[str] = mapped_column(String(32), default="default")


class RiskLimitRule(TimestampMixin, Base):
    __tablename__ = "risk_limit_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32), index=True)
    scope_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_type: Mapped[str] = mapped_column(String(32))
    limit_value: Mapped[float] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)


class Announcement(TimestampMixin, Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    audience_type: Mapped[str] = mapped_column(String(32), default="all")
    audience_filter_json: Mapped[dict] = mapped_column(JSON, default=dict)
    channels_json: Mapped[list] = mapped_column(JSON, default=list)
    requires_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="draft")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -q`
Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/core/types.py models.py tests/test_models.py
git commit -m "feat: add core database models"
```

## Task 3: Build Market Opportunity Calculation And Redis Payloads

**Files:**
- Create: `app/market/opportunity.py`
- Modify: `market_scanner.py`
- Test: `tests/test_market_scanner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_market_scanner.py
from app.market.opportunity import OpportunityCalculator, OrderbookSnapshot


def test_calculator_returns_open_and_close_spread():
    calculator = OpportunityCalculator()
    spot = OrderbookSnapshot(best_bid=100.0, best_ask=101.0, bids=[[100.0, 5.0]], asks=[[101.0, 5.0]])
    future = OrderbookSnapshot(best_bid=104.0, best_ask=105.0, bids=[[104.0, 4.0]], asks=[[105.0, 4.0]])

    result = calculator.build_opportunity(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        spot=spot,
        derivative=future,
        funding_rate=0.0005,
    )

    assert round(result.open_spread_bps, 2) == round(((105.0 - 101.0) / 101.0) * 10000, 2)
    assert round(result.close_spread_bps, 2) == round(((104.0 - 100.0) / 100.0) * 10000, 2)
    assert result.redis_member.startswith("binance:okx:BTC/USDT")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_market_scanner.py -q`
Expected: FAIL because `OpportunityCalculator` and `OrderbookSnapshot` do not exist.

- [ ] **Step 3: Write minimal implementation**

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
        annualized_bps = (open_spread + funding_rate) * 365 * 10000
        member = f"{spot_exchange}:{derivative_exchange}:{symbol}:{int(time() * 1000)}"
        return Opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            open_spread_bps=open_spread * 10000,
            close_spread_bps=close_spread * 10000,
            funding_rate=funding_rate,
            annualized_bps=annualized_bps,
            redis_member=member,
            timestamp=time(),
        )
```

```python
# market_scanner.py
from app.market.opportunity import OpportunityCalculator, Opportunity, OrderbookSnapshot

__all__ = ["OpportunityCalculator", "Opportunity", "OrderbookSnapshot"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_market_scanner.py -q`
Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/market/opportunity.py market_scanner.py tests/test_market_scanner.py
git commit -m "feat: add opportunity calculation primitives"
```

## Task 4: Implement Admin Control Plane, Limits, And Announcements

**Files:**
- Create: `app/admin/control_plane.py`
- Create: `app/admin/notifier.py`
- Test: `tests/test_control_plane.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control_plane.py
from app.admin.control_plane import ControlPlane, LimitRule, PlatformSwitch


def test_control_plane_returns_smallest_allowed_notional():
    plane = ControlPlane(
        switches=[],
        limit_rules=[
            LimitRule(scope="platform", scope_id="global", limit_value=100000.0),
            LimitRule(scope="user", scope_id="42", limit_value=1200.0),
            LimitRule(scope="strategy", scope_id="7", limit_value=800.0),
        ],
    )

    decision = plane.evaluate_open_request(
        user_id=42,
        strategy_id=7,
        symbol="BTC/USDT",
        exchange="okx",
        requested_notional=1500.0,
    )

    assert decision.allowed is True
    assert decision.approved_notional == 800.0


def test_control_plane_blocks_when_only_reduce_mode_is_active():
    plane = ControlPlane(
        switches=[PlatformSwitch(key="platform.reduce_only", enabled=True, scope="platform", scope_id="global")],
        limit_rules=[],
    )

    decision = plane.evaluate_open_request(
        user_id=1,
        strategy_id=1,
        symbol="ETH/USDT",
        exchange="binance",
        requested_notional=100.0,
    )

    assert decision.allowed is False
    assert decision.reason == "reduce_only"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_control_plane.py -q`
Expected: FAIL because `ControlPlane`, `LimitRule`, and `PlatformSwitch` are not implemented.

- [ ] **Step 3: Write minimal implementation**

```python
# app/admin/control_plane.py
from dataclasses import dataclass


@dataclass(slots=True)
class LimitRule:
    scope: str
    scope_id: str
    limit_value: float
    symbol: str | None = None
    exchange: str | None = None
    strategy_id: int | None = None


@dataclass(slots=True)
class PlatformSwitch:
    key: str
    enabled: bool
    scope: str
    scope_id: str


@dataclass(slots=True)
class ControlDecision:
    allowed: bool
    approved_notional: float
    reason: str | None = None


class ControlPlane:
    def __init__(self, *, switches: list[PlatformSwitch], limit_rules: list[LimitRule]) -> None:
        self.switches = switches
        self.limit_rules = limit_rules

    def evaluate_open_request(
        self,
        *,
        user_id: int,
        strategy_id: int | None,
        symbol: str,
        exchange: str,
        requested_notional: float,
    ) -> ControlDecision:
        if any(switch.enabled and switch.key == "platform.reduce_only" for switch in self.switches):
            return ControlDecision(allowed=False, approved_notional=0.0, reason="reduce_only")

        applicable_limits = [requested_notional]
        for rule in self.limit_rules:
            if rule.scope == "platform":
                applicable_limits.append(rule.limit_value)
            elif rule.scope == "user" and rule.scope_id == str(user_id):
                applicable_limits.append(rule.limit_value)
            elif rule.scope == "strategy" and rule.scope_id == str(strategy_id):
                applicable_limits.append(rule.limit_value)
            elif rule.scope == "symbol" and rule.symbol == symbol:
                applicable_limits.append(rule.limit_value)
            elif rule.scope == "exchange" and rule.exchange == exchange:
                applicable_limits.append(rule.limit_value)

        approved = min(applicable_limits)
        return ControlDecision(allowed=approved > 0, approved_notional=approved, reason=None)
```

```python
# app/admin/notifier.py
from dataclasses import dataclass


@dataclass(slots=True)
class AnnouncementMessage:
    title: str
    content: str
    channels: list[str]


class AnnouncementNotifier:
    async def publish(self, message: AnnouncementMessage) -> dict[str, str]:
        return {channel: "queued" for channel in message.channels}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_control_plane.py -q`
Expected: PASS with `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/admin/control_plane.py app/admin/notifier.py tests/test_control_plane.py
git commit -m "feat: add admin control plane and notifier primitives"
```

## Task 5: Add Account Session Manager And Proxy Injection

**Files:**
- Create: `app/exchanges/session_manager.py`
- Create: `app/exchanges/adapters.py`
- Test: `tests/test_sessions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sessions.py
from app.exchanges.session_manager import ExchangeAccountSession, build_proxy_urls


def test_build_proxy_urls_supports_http_auth():
    urls = build_proxy_urls(
        proxy_type="http",
        host="10.0.0.8",
        port=8080,
        username="alice",
        password="secret",
    )

    assert urls["http"] == "http://alice:secret@10.0.0.8:8080"
    assert urls["https"] == "http://alice:secret@10.0.0.8:8080"


def test_session_keeps_exchange_and_env_mode():
    session = ExchangeAccountSession(
        exchange="binance",
        env_mode="testnet",
        proxies={"http": "http://127.0.0.1:9000", "https": "http://127.0.0.1:9000"},
    )

    assert session.exchange == "binance"
    assert session.env_mode == "testnet"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sessions.py -q`
Expected: FAIL because proxy helpers and session container do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# app/exchanges/session_manager.py
from dataclasses import dataclass, field
from typing import Any


def build_proxy_urls(*, proxy_type: str, host: str, port: int, username: str | None = None, password: str | None = None) -> dict[str, str]:
    auth = f"{username}:{password}@" if username and password else ""
    prefix = "socks5" if proxy_type.startswith("socks") else "http"
    url = f"{prefix}://{auth}{host}:{port}"
    return {"http": url, "https": url}


@dataclass(slots=True)
class ExchangeAccountSession:
    exchange: str
    env_mode: str
    proxies: dict[str, str]
    markets_loaded: bool = False
    client: Any = field(default=None)

    async def mark_ready(self) -> None:
        self.markets_loaded = True
```

```python
# app/exchanges/adapters.py
from dataclasses import dataclass

from app.exchanges.session_manager import ExchangeAccountSession


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: str
    order_type: str
    amount: float
    price: float | None = None
    reduce_only: bool = False


class ExchangeAdapter:
    def __init__(self, session: ExchangeAccountSession) -> None:
        self.session = session

    async def create_order(self, request: OrderRequest) -> dict:
        return {
            "id": "simulated-order",
            "symbol": request.symbol,
            "side": request.side,
            "amount": request.amount,
            "status": "open",
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sessions.py -q`
Expected: PASS with `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/exchanges/session_manager.py app/exchanges/adapters.py tests/test_sessions.py
git commit -m "feat: add exchange session manager and proxy injection helpers"
```

## Task 6: Implement Concurrent Trade Execution And Automatic Repair

**Files:**
- Create: `app/trading/tasks.py`
- Create: `app/trading/executor.py`
- Create: `app/trading/risk_manager.py`
- Modify: `trading_engine.py`
- Test: `tests/test_trading_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_engine.py
import pytest

from app.trading.executor import TradeExecutor
from app.trading.tasks import ExecutionLeg, ExecutionTask


class FakeAdapter:
    def __init__(self, name: str, should_fail: bool = False) -> None:
        self.name = name
        self.should_fail = should_fail

    async def create_order(self, request):
        if self.should_fail:
            raise RuntimeError(f"{self.name} failed")
        return {"id": f"{self.name}-1", "status": "filled", "amount": request.amount}


@pytest.mark.asyncio
async def test_execute_open_submits_both_legs_and_reports_partial_failure():
    task = ExecutionTask(
        task_id="task-1",
        symbol="BTC/USDT",
        open_legs=[
            ExecutionLeg(exchange="binance", side="buy", order_type="market", amount=1.0),
            ExecutionLeg(exchange="okx", side="sell", order_type="market", amount=1.0),
        ],
    )

    executor = TradeExecutor(
        adapter_factory={
            "binance": FakeAdapter("binance"),
            "okx": FakeAdapter("okx", should_fail=True),
        }
    )

    result = await executor.execute_open(task)

    assert result.status == "OPEN_PARTIAL"
    assert result.filled_exchanges == ["binance"]
    assert result.failed_exchanges == ["okx"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading_engine.py -q`
Expected: FAIL because execution task and executor classes are not implemented.

- [ ] **Step 3: Write minimal implementation**

```python
# app/trading/tasks.py
from dataclasses import dataclass, field


@dataclass(slots=True)
class ExecutionLeg:
    exchange: str
    side: str
    order_type: str
    amount: float
    price: float | None = None


@dataclass(slots=True)
class ExecutionTask:
    task_id: str
    symbol: str
    open_legs: list[ExecutionLeg] = field(default_factory=list)
```

```python
# app/trading/executor.py
import asyncio
from dataclasses import dataclass

from app.exchanges.adapters import OrderRequest
from app.trading.tasks import ExecutionTask


@dataclass(slots=True)
class ExecutionResult:
    status: str
    filled_exchanges: list[str]
    failed_exchanges: list[str]


class TradeExecutor:
    def __init__(self, *, adapter_factory: dict[str, object]) -> None:
        self.adapter_factory = adapter_factory

    async def execute_open(self, task: ExecutionTask) -> ExecutionResult:
        coroutines = []
        exchanges = []
        for leg in task.open_legs:
            exchanges.append(leg.exchange)
            adapter = self.adapter_factory[leg.exchange]
            coroutines.append(
                adapter.create_order(
                    OrderRequest(
                        symbol=task.symbol,
                        side=leg.side,
                        order_type=leg.order_type,
                        amount=leg.amount,
                        price=leg.price,
                    )
                )
            )

        responses = await asyncio.gather(*coroutines, return_exceptions=True)
        filled = [exchange for exchange, result in zip(exchanges, responses) if not isinstance(result, Exception)]
        failed = [exchange for exchange, result in zip(exchanges, responses) if isinstance(result, Exception)]
        status = "OPEN_HEDGED" if not failed else "OPEN_PARTIAL"
        return ExecutionResult(status=status, filled_exchanges=filled, failed_exchanges=failed)
```

```python
# app/trading/risk_manager.py
from dataclasses import dataclass

from app.trading.executor import ExecutionResult


@dataclass(slots=True)
class RepairPlan:
    action: str
    reason: str


class RiskManager:
    def build_repair_plan(self, result: ExecutionResult) -> RepairPlan:
        if result.status == "OPEN_PARTIAL":
            return RepairPlan(action="AUTO_HEDGE_REPAIRING", reason="one_leg_failed")
        return RepairPlan(action="NONE", reason="fully_hedged")
```

```python
# trading_engine.py
from app.trading.executor import ExecutionResult, TradeExecutor
from app.trading.risk_manager import RepairPlan, RiskManager
from app.trading.tasks import ExecutionLeg, ExecutionTask

__all__ = [
    "ExecutionLeg",
    "ExecutionResult",
    "ExecutionTask",
    "RepairPlan",
    "RiskManager",
    "TradeExecutor",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trading_engine.py -q`
Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/trading/tasks.py app/trading/executor.py app/trading/risk_manager.py trading_engine.py tests/test_trading_engine.py
git commit -m "feat: add concurrent execution and repair planning"
```

## Task 7: Wire Region Routing, Scanner Runtime, And Service Bootstrap

**Files:**
- Create: `app/runtime/router.py`
- Create: `app/runtime/bootstrap.py`
- Modify: `main.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runtime.py
from app.runtime.router import TaskRoute, route_task


def test_route_task_uses_home_region():
    route = route_task(TaskRoute(task_id="task-1", user_id=7, home_region="sg", fallback_region="hk"))

    assert route.primary_region == "sg"
    assert route.fallback_region == "hk"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime.py -q`
Expected: FAIL because region router and bootstrap helpers are not implemented.

- [ ] **Step 3: Write minimal implementation**

```python
# app/runtime/router.py
from dataclasses import dataclass


@dataclass(slots=True)
class TaskRoute:
    task_id: str
    user_id: int
    home_region: str
    fallback_region: str


@dataclass(slots=True)
class ResolvedRoute:
    primary_region: str
    fallback_region: str


def route_task(task: TaskRoute) -> ResolvedRoute:
    return ResolvedRoute(primary_region=task.home_region, fallback_region=task.fallback_region)
```

```python
# app/runtime/bootstrap.py
from dataclasses import dataclass

from config import get_settings


@dataclass(slots=True)
class RuntimeApp:
    service_name: str
    region: str


def build_runtime(service_name: str, region: str | None = None) -> RuntimeApp:
    settings = get_settings()
    return RuntimeApp(service_name=service_name, region=region or settings.default_region)
```

```python
# main.py
import argparse

from app.runtime.bootstrap import build_runtime


def main() -> RuntimeApp:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", default="all", choices=["all", "scanner", "trader"])
    parser.add_argument("--region", default=None)
    args = parser.parse_args()
    return build_runtime(service_name=args.service, region=args.region)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_runtime.py -q`
Expected: PASS with `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/router.py app/runtime/bootstrap.py main.py tests/test_runtime.py
git commit -m "feat: add runtime bootstrap and region routing"
```

## Task 8: Add Metrics, Audit Hooks, And Integration Coverage For Admin Controls

**Files:**
- Create: `app/monitoring/metrics.py`
- Create: `app/audit/logger.py`
- Test: `tests/test_monitoring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_monitoring.py
from app.audit.logger import AuditLogger
from app.monitoring.metrics import MetricsRegistry


def test_metrics_and_audit_capture_admin_actions():
    metrics = MetricsRegistry()
    audit = AuditLogger()

    metrics.increment("tasks.rejected_by_limits")
    audit.record("admin.limit.updated", {"scope": "user", "scope_id": "42"})

    assert metrics.counters["tasks.rejected_by_limits"] == 1
    assert audit.entries[0]["event_type"] == "admin.limit.updated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_monitoring.py -q`
Expected: FAIL because metrics and audit helpers are not implemented.

- [ ] **Step 3: Write minimal implementation**

```python
# app/monitoring/metrics.py
from collections import defaultdict


class MetricsRegistry:
    def __init__(self) -> None:
        self.counters = defaultdict(int)

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] += value
```

```python
# app/audit/logger.py
from datetime import datetime


class AuditLogger:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, event_type: str, payload: dict) -> None:
        self.entries.append(
            {
                "event_type": event_type,
                "payload": payload,
                "created_at": datetime.utcnow().isoformat(),
            }
        )
```

- [ ] **Step 4: Run the focused suite and then the whole suite**

Run: `python -m pytest tests/test_monitoring.py -q`
Expected: PASS with `1 passed`

Run: `python -m pytest tests -q`
Expected: PASS with all task tests green.

- [ ] **Step 5: Commit**

```bash
git add app/monitoring/metrics.py app/audit/logger.py tests/test_monitoring.py
git commit -m "feat: add audit and metrics hooks"
```

## Coverage Check

- Shared market scanning and spread calculation: Task 3
- Per-user proxy-backed exchange sessions: Task 5
- Concurrent hedged execution and automatic repair path: Task 6
- Admin limits, platform switches, and announcements: Task 4
- Region-aware routing and bootstrapping: Task 7
- Audit and monitoring hooks: Task 8
- Core persistence models for users, accounts, admin rules, and tasks: Task 2

## Execution Notes

- Keep `market_scanner.py` and `trading_engine.py` as thin orchestration files; do not re-expand business logic into those entrypoints.
- Prefer pure functions and dataclasses around calculators and decisions so unit tests stay cheap.
- When ccxt/ccxt.pro wiring is added after the minimal green tests, keep all exchange-specific branching inside `app/exchanges/adapters.py`.
- Add integration tests only after the unit-level contracts above are green; do not start with live exchange calls.
- Use fake adapters and in-memory sqlite for tests until the first end-to-end dry run.
