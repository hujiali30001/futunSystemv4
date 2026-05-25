from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.account_repository import AccountRepository
from models import Base, ExchangeAccount, Proxy, User


def test_account_repository_returns_enabled_accounts_with_proxy():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.add(Proxy(id=7, host="1.2.3.4", port=8080, region="sg"))
    session.add(
        ExchangeAccount(
            user_id=42,
            exchange="okx",
            account_label="default",
            env_mode="testnet",
            api_key_ciphertext="ak",
            secret_ciphertext="sk",
            proxy_id=7,
            is_enabled=True,
        )
    )
    session.add(
        ExchangeAccount(
            user_id=42,
            exchange="gate",
            account_label="disabled",
            env_mode="testnet",
            api_key_ciphertext="ak2",
            secret_ciphertext="sk2",
            is_enabled=False,
        )
    )
    session.commit()

    repository = AccountRepository(session)
    accounts = repository.list_enabled_accounts(user_id=42, env_mode="testnet")

    assert len(accounts) == 1
    assert accounts[0].exchange == "okx"
    assert accounts[0].proxy is not None
    assert accounts[0].proxy.host == "1.2.3.4"


def test_account_repository_returns_empty_when_no_enabled_accounts_match():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.add(
        ExchangeAccount(
            user_id=42,
            exchange="okx",
            account_label="mainnet-only",
            env_mode="mainnet",
            api_key_ciphertext="ak",
            secret_ciphertext="sk",
            is_enabled=True,
        )
    )
    session.commit()

    repository = AccountRepository(session)

    assert repository.list_enabled_accounts(user_id=42, env_mode="testnet") == []
