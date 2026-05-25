from sqlalchemy import inspect

from models import (
    Announcement,
    ArbitrageTask,
    Base,
    ExchangeAccount,
    RiskLimitRule,
    StrategyConfig,
    User,
)


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


def test_strategy_config_and_arbitrage_task_expose_expected_columns():
    strategy_columns = {column.key for column in inspect(StrategyConfig).columns}
    task_columns = {column.key for column in inspect(ArbitrageTask).columns}

    assert {
        "id",
        "user_id",
        "strategy_type",
        "name",
        "target_quote_amount",
        "open_spread_bps_threshold",
        "is_enabled",
    } <= strategy_columns
    assert {
        "task_uuid",
        "status",
        "status_reason",
        "worker_node_id",
        "dispatched_at",
        "started_at",
        "finished_at",
    } <= task_columns
    assert Base.metadata.tables["strategy_configs"].name == "strategy_configs"
