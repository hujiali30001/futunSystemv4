from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.strategy_config_repository import StrategyConfigRepository
from models import Base, StrategyConfig, User


def test_strategy_config_repository_returns_enabled_spot_futures_strategies_only():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.add_all(
        [
            StrategyConfig(
                id=1,
                user_id=42,
                strategy_type="spot_futures",
                name="btc-primary",
                symbol_scope_json=["BTC/USDT"],
                exchange_scope_json=["bitget", "gate"],
                target_quote_amount=80.0,
                open_spread_bps_threshold=15.0,
                is_enabled=True,
            ),
            StrategyConfig(
                id=2,
                user_id=42,
                strategy_type="spot_futures",
                name="disabled",
                target_quote_amount=60.0,
                open_spread_bps_threshold=10.0,
                is_enabled=False,
            ),
            StrategyConfig(
                id=3,
                user_id=42,
                strategy_type="grid",
                name="other-type",
                target_quote_amount=50.0,
                open_spread_bps_threshold=10.0,
                is_enabled=True,
            ),
        ]
    )
    session.commit()

    repository = StrategyConfigRepository(session)

    strategies = repository.list_enabled_for_user(user_id=42)

    assert [strategy.id for strategy in strategies] == [1]
    assert strategies[0].name == "btc-primary"
