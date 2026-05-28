"""
Seed script: bind credentials + API keys + node routing to user "huhuhu"

Reads local-secrets/*.txt and inserts into the database.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt
from sqlalchemy import inspect

from app.db.session import build_session_factory
from app.runtime.executor_account_truth import SecretCipher
from models import Base, User, ExchangeAccount

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./furun.db")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", os.getenv("JWT_SECRET_KEY", "furun-dev-secret-change-in-production"))

USERNAME = "huhuhu"
PASSWORD = "huhuhu123"
EMAIL = "79893530@qq.com"
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/6da6d851-e5c2-4705-bd39-39f5e0e32966"

EXCHANGE_KEYS = [
    {
        "exchange": "binance",
        "api_key": "2AqMAGiK7L67m10KmAOZvoib3rps68DNIELYWRdDDmZtsFVDhrW2Ks7vSjMV4d44",
        "secret": "Dv5v8FVP2BCJejImEXDPRupv5B73IN8vrrBhYpPmOtkIRT128Hf1KQD2y7SXMrdR",
        "passphrase": None,
        "env_mode": "testnet",
    },
    {
        "exchange": "okx",
        "api_key": "1ba19f59-977b-4a6a-ada1-622a60e2f4b3",
        "secret": "E3E6AD0306F8F60B75D12D548B9E5C75",
        "passphrase": "Hu402811492@",
        "env_mode": "testnet",
    },
    {
        "exchange": "bybit",
        "api_key": "ex0I1phD66RyooLrYJ",
        "secret": "Fhifj1jqHPBpm9AbZvHoCOOtg16cuUmu5WGO",
        "passphrase": None,
        "env_mode": "testnet",
    },
    {
        "exchange": "bitget",
        "api_key": "bg_59fbd42ac2425bb8938303833321e7b4",
        "secret": "81d037759abcd647e55d87101d322b4e94b6d29ab6e6d0c642ab59dbc1aaf0ec",
        "passphrase": "Hu402811492",
        "env_mode": "testnet",
    },
    {
        "exchange": "gate",
        "api_key": "45045e4600b733a1d0b6770d45ca9637",
        "secret": "30091a718a1f106ce8e9b9d2f02b602149c9b6715819bfa9f9d6ff90caf0ce3d",
        "passphrase": None,
        "env_mode": "testnet",
    },
]


def main():
    cipher = SecretCipher(ENCRYPTION_KEY)
    session_factory = build_session_factory(DATABASE_URL)
    session = session_factory()

    engine = session.get_bind()
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        Base.metadata.create_all(engine)
        print("[+] Created database tables")

    user = session.query(User).filter(User.username == USERNAME).first()
    if user is None:
        user = User(
            username=USERNAME,
            password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
            home_region="cn",
            node_id="node1",
            is_trading_enabled=True,
            email=EMAIL,
            feishu_webhook_url=FEISHU_WEBHOOK,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        print(f"[+] User created: id={user.id}  username={USERNAME}")
    else:
        user.email = EMAIL
        user.feishu_webhook_url = FEISHU_WEBHOOK
        user.node_id = "node1"
        user.is_trading_enabled = True
        session.commit()
        print(f"[~] User updated: id={user.id}  username={USERNAME}")

    user_id = user.id
    added = 0
    updated = 0

    for entry in EXCHANGE_KEYS:
        existing = (
            session.query(ExchangeAccount)
            .filter(
                ExchangeAccount.user_id == user_id,
                ExchangeAccount.exchange == entry["exchange"],
                ExchangeAccount.env_mode == entry["env_mode"],
            )
            .first()
        )
        encrypted_api_key = cipher.encrypt(entry["api_key"])
        encrypted_secret = cipher.encrypt(entry["secret"])
        encrypted_passphrase = cipher.encrypt(entry["passphrase"])

        if existing:
            existing.api_key_ciphertext = encrypted_api_key
            existing.secret_ciphertext = encrypted_secret
            existing.passphrase_ciphertext = encrypted_passphrase
            existing.is_enabled = True
            updated += 1
            print(f"[~] Updated {entry['exchange']} ({entry['env_mode']})")
        else:
            acct = ExchangeAccount(
                user_id=user_id,
                exchange=entry["exchange"],
                account_label="default",
                env_mode=entry["env_mode"],
                api_key_ciphertext=encrypted_api_key,
                secret_ciphertext=encrypted_secret,
                passphrase_ciphertext=encrypted_passphrase,
                is_enabled=True,
                is_auto_trade_enabled=True,
                account_region="cn",
            )
            session.add(acct)
            added += 1
            print(f"[+] Added {entry['exchange']} ({entry['env_mode']})")

    session.commit()
    session.close()

    print()
    print(f"  User        : {USERNAME} (id={user_id})")
    print(f"  Password    : {PASSWORD}")
    print(f"  Email       : {EMAIL}")
    print(f"  Feishu      : {FEISHU_WEBHOOK[:50]}...")
    print(f"  Node        : node1")
    print(f"  API Keys    : {added} added, {updated} updated")
    print()
    print("=== .env entries for dispatcher ===")
    print(f"DISPATCH_USER_IDS={user_id}")
    print(f"USER_NODE_ROUTES={user_id}:node1")
    print()
    print("=== Redis setup (on main server) ===")
    print(f"redis-cli SET route:user_node:{user_id} node1")
    print(f"redis-cli SADD route:user_node:index {user_id}")


if __name__ == "__main__":
    main()
