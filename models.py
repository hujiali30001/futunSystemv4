from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.types import EnvironmentMode


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    risk_level: Mapped[str] = mapped_column(String(32), default="standard")
    home_region: Mapped[str] = mapped_column(String(32), default="default")
    is_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


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


class ExchangeAccount(TimestampMixin, Base):
    __tablename__ = "exchange_accounts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "exchange",
            "account_label",
            "env_mode",
            name="uq_exchange_account",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    account_label: Mapped[str] = mapped_column(String(64), default="default")
    market_type_scope: Mapped[str] = mapped_column(String(32), default="spot,swap")
    env_mode: Mapped[str] = mapped_column(
        String(16), default=EnvironmentMode.TESTNET.value
    )
    api_key_ciphertext: Mapped[str] = mapped_column(Text)
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    passphrase_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id"), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_auto_trade_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    account_region: Mapped[str] = mapped_column(String(32), default="default")

    user: Mapped["User"] = relationship()
    proxy: Mapped["Proxy | None"] = relationship()


class StrategyConfig(TimestampMixin, Base):
    __tablename__ = "strategy_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_type: Mapped[str] = mapped_column(String(32), default="spot_futures")
    name: Mapped[str] = mapped_column(String(128))
    symbol_scope_json: Mapped[list] = mapped_column(JSON, default=list)
    exchange_scope_json: Mapped[list] = mapped_column(JSON, default=list)
    target_quote_amount: Mapped[float] = mapped_column(Float, default=100.0)
    open_spread_bps_threshold: Mapped[float] = mapped_column(Float, default=0.0)
    close_spread_bps_threshold: Mapped[float] = mapped_column(Float, default=0.0)
    max_single_task_notional: Mapped[float] = mapped_column(Float, default=100.0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ArbitrageTask(TimestampMixin, Base):
    __tablename__ = "arbitrage_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_configs.id"),
        nullable=True,
    )
    buy_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sell_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opportunity_id: Mapped[str] = mapped_column(String(128), index=True)
    env_mode: Mapped[str] = mapped_column(
        String(16), default=EnvironmentMode.TESTNET.value
    )
    task_type: Mapped[str] = mapped_column(String(32), default="open")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    spot_exchange: Mapped[str] = mapped_column(String(32))
    derivative_exchange: Mapped[str] = mapped_column(String(32))
    target_notional: Mapped[float] = mapped_column(Float)
    expected_spread_bps: Mapped[float] = mapped_column(Float)
    expected_funding_bps: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="CREATED")
    status_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    filled_exchanges_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    failed_exchanges_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    repair_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repair_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    home_region: Mapped[str] = mapped_column(String(32), default="default")
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


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
