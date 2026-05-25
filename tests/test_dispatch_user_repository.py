from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.dispatch_user_repository import DispatchUserRepository
from models import Base, ExchangeAccount, StrategyConfig, User


def test_dispatch_user_repository_returns_only_dispatchable_users_for_env_mode():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            User(id=1, username="u1", is_trading_enabled=True),
            User(id=2, username="u2", is_trading_enabled=False),
            User(id=3, username="u3", is_trading_enabled=True),
            User(id=4, username="u4", is_trading_enabled=True),
        ]
    )
    session.add_all(
        [
            ExchangeAccount(
                user_id=1,
                exchange="bitget",
                account_label="default",
                env_mode="testnet",
                api_key_ciphertext="ak1",
                secret_ciphertext="sk1",
                is_enabled=True,
            ),
            ExchangeAccount(
                user_id=2,
                exchange="bitget",
                account_label="default",
                env_mode="testnet",
                api_key_ciphertext="ak2",
                secret_ciphertext="sk2",
                is_enabled=True,
            ),
            ExchangeAccount(
                user_id=3,
                exchange="bitget",
                account_label="default",
                env_mode="mainnet",
                api_key_ciphertext="ak3",
                secret_ciphertext="sk3",
                is_enabled=True,
            ),
            ExchangeAccount(
                user_id=4,
                exchange="bitget",
                account_label="default",
                env_mode="testnet",
                api_key_ciphertext="ak4",
                secret_ciphertext="sk4",
                is_enabled=True,
            ),
        ]
    )
    session.add_all(
        [
            StrategyConfig(
                user_id=1,
                strategy_type="spot_futures",
                name="s1",
                target_quote_amount=80.0,
                open_spread_bps_threshold=10.0,
                is_enabled=True,
            ),
            StrategyConfig(
                user_id=2,
                strategy_type="spot_futures",
                name="s2",
                target_quote_amount=80.0,
                open_spread_bps_threshold=10.0,
                is_enabled=True,
            ),
            StrategyConfig(
                user_id=3,
                strategy_type="spot_futures",
                name="s3",
                target_quote_amount=80.0,
                open_spread_bps_threshold=10.0,
                is_enabled=True,
            ),
        ]
    )
    session.commit()

    repository = DispatchUserRepository(session)

    user_ids = repository.list_dispatchable_user_ids(env_mode="testnet")

    assert user_ids == ["1"]


def test_dispatch_user_repository_filters_out_accounts_from_other_env_mode():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42", is_trading_enabled=True))
    session.add(
        ExchangeAccount(
            user_id=42,
            exchange="bitget",
            account_label="default",
            env_mode="mainnet",
            api_key_ciphertext="ak",
            secret_ciphertext="sk",
            is_enabled=True,
        )
    )
    session.add(
        StrategyConfig(
            user_id=42,
            strategy_type="spot_futures",
            name="s1",
            target_quote_amount=80.0,
            open_spread_bps_threshold=10.0,
            is_enabled=True,
        )
    )
    session.commit()

    repository = DispatchUserRepository(session)

    assert repository.list_dispatchable_user_ids(env_mode="testnet") == []
