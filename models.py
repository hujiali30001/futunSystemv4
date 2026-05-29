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
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    risk_level: Mapped[str] = mapped_column(String(32), default="standard")
    home_region: Mapped[str] = mapped_column(String(32), default="default")
    is_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feishu_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    node_id: Mapped[str] = mapped_column(String(64), default="main")


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
    open_tiers_json: Mapped[list] = mapped_column(JSON, default=list)
    close_tiers_json: Mapped[list] = mapped_column(JSON, default=list)
    max_single_task_notional: Mapped[float] = mapped_column(Float, default=100.0)
    max_loss_usdt: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ArbitrageTask(TimestampMixin, Base):
    __tablename__ = "arbitrage_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_configs.id", ondelete="SET NULL"),
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
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retry_count: Mapped[int] = mapped_column(Integer, default=2)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auto_recovery_status: Mapped[str] = mapped_column(String(32), default="NONE")
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
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_fee: Mapped[float | None] = mapped_column(Float, nullable=True)


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


class AdminUser(TimestampMixin, Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="ops_admin")


class AdminActionLog(TimestampMixin, Base):
    __tablename__ = "admin_action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    admin_user: Mapped["AdminUser"] = relationship()


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
    filled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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


class PlatformConfig(TimestampMixin, Base):
    __tablename__ = "platform_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    config_value: Mapped[str] = mapped_column(Text, nullable=False)
    config_type: Mapped[str] = mapped_column(String(32), default="string")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
