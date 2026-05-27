import os
import sqlalchemy.orm

from app.db.session import build_engine
from models import Base, User, ExchangeAccount, StrategyConfig

DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./furun.db")

engine = build_engine(DB_URL)
Base.metadata.create_all(engine, checkfirst=True)
print("[OK] tables created")

with sqlalchemy.orm.Session(engine) as sess:
    if sess.query(User).filter(User.id == 1).first():
        print("[SKIP] user id=1 already exists")
    else:
        user = User(
            id=1,
            username="test",
            status="active",
            risk_level="standard",
            home_region="main",
            is_trading_enabled=True,
        )
        sess.add(user)
        sess.flush()

        for exchange in ["okx", "binance", "bybit", "bitget", "gate"]:
            sess.add(
                ExchangeAccount(
                    user_id=1,
                    exchange=exchange,
                    account_label="default",
                    market_type_scope="spot,swap",
                    env_mode="testnet",
                    api_key_ciphertext=f"enc:{exchange}:key",
                    secret_ciphertext=f"enc:{exchange}:secret",
                    is_enabled=True,
                    is_auto_trade_enabled=True,
                    account_region="main",
                )
            )

        sess.add(
            StrategyConfig(
                user_id=1,
                strategy_type="spot_futures",
                name="default",
                symbol_scope_json=[],
                exchange_scope_json=[],
                target_quote_amount=15.0,
                open_spread_bps_threshold=0.0,
                close_spread_bps_threshold=0.0,
                max_single_task_notional=50.0,
                is_enabled=True,
            )
        )
        sess.commit()
        print("[OK] 5 exchange_accounts + 1 strategy created")

print("[DONE]")
