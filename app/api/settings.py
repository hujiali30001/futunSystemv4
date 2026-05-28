import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.api.deps import get_cipher, get_db, get_current_user
from app.exchanges.session_manager import ExchangeClientFactory, ExchangeCredentials
from app.runtime.executor_account_truth import SecretCipher
from models import User, ExchangeAccount

EXCHANGES = ["binance", "okx", "bybit", "gate", "bitget"]
BALANCE_TIMEOUT = 15

router = APIRouter()


class SmtpConfig(BaseModel):
    host: str = ""
    port: int = 465
    username: str = ""
    password: str = ""


class ProfileUpdate(BaseModel):
    email: str | None = None
    feishu_webhook_url: str | None = None
    smtp: SmtpConfig | None = None


class ExchangeAccountCreate(BaseModel):
    exchange: str
    api_key: str
    secret: str
    passphrase: str | None = None
    account_label: str = "default"
    env_mode: str = "testnet"


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    u = db.query(User).filter(User.id == user["user_id"]).first()
    accounts = (
        db.query(ExchangeAccount)
        .filter(ExchangeAccount.user_id == user["user_id"])
        .all()
    )
    return {
        "email": u.email,
        "feishu_webhook_url": u.feishu_webhook_url,
        "smtp": u.smtp_config_json or {},
        "exchange_accounts": [
            {
                "id": a.id,
                "exchange": a.exchange,
                "account_label": a.account_label,
                "env_mode": a.env_mode,
                "api_key_masked": a.api_key_ciphertext[:6] + "***" + a.api_key_ciphertext[-3:] if a.api_key_ciphertext else "",
                "secret_masked": "***" if a.secret_ciphertext else "",
                "passphrase_masked": "***" if a.passphrase_ciphertext else "",
                "secret_set": bool(a.secret_ciphertext),
                "passphrase_set": bool(a.passphrase_ciphertext),
            }
            for a in accounts
        ],
    }


@router.put("/settings/profile")
def update_profile(
    body: ProfileUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    u = db.query(User).filter(User.id == user["user_id"]).first()
    if body.email is not None:
        u.email = body.email
    if body.feishu_webhook_url is not None:
        u.feishu_webhook_url = body.feishu_webhook_url
    if body.smtp is not None:
        u.smtp_config_json = body.smtp.model_dump()
    db.commit()
    return {"ok": True}


@router.post("/settings/exchange")
def create_exchange_account(
    body: ExchangeAccountCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
    cipher: SecretCipher = Depends(get_cipher),
):
    acct = ExchangeAccount(
        user_id=user["user_id"],
        exchange=body.exchange,
        account_label=body.account_label,
        env_mode=body.env_mode,
        api_key_ciphertext=cipher.encrypt(body.api_key),
        secret_ciphertext=cipher.encrypt(body.secret),
        passphrase_ciphertext=cipher.encrypt(body.passphrase),
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return {"ok": True, "account": acct}


@router.put("/settings/exchange/{account_id}")
def update_exchange_account(
    account_id: int,
    body: ExchangeAccountCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
    cipher: SecretCipher = Depends(get_cipher),
):
    acct = (
        db.query(ExchangeAccount)
        .filter(
            ExchangeAccount.id == account_id,
            ExchangeAccount.user_id == user["user_id"],
        )
        .first()
    )
    if not acct:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    acct.api_key_ciphertext = cipher.encrypt(body.api_key)
    acct.secret_ciphertext = cipher.encrypt(body.secret)
    acct.passphrase_ciphertext = cipher.encrypt(body.passphrase)
    acct.exchange = body.exchange
    acct.account_label = body.account_label
    db.commit()
    return {"ok": True}


@router.delete("/settings/exchange/{account_id}")
def delete_exchange_account(
    account_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    acct = (
        db.query(ExchangeAccount)
        .filter(
            ExchangeAccount.id == account_id,
            ExchangeAccount.user_id == user["user_id"],
        )
        .first()
    )
    if not acct:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    db.delete(acct)
    db.commit()
    return {"ok": True}


@router.get("/settings/balances")
async def get_balances(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
    cipher: SecretCipher = Depends(get_cipher),
):
    accounts = (
        db.query(ExchangeAccount)
        .filter(ExchangeAccount.user_id == user["user_id"])
        .all()
    )
    if not accounts:
        return {"exchanges": [], "total_usdt": 0}

    factory = ExchangeClientFactory()

    async def _fetch_one(acct) -> dict:
        try:
            api_key = cipher.decrypt(acct.api_key_ciphertext)
            secret = cipher.decrypt(acct.secret_ciphertext)
            if not api_key or not secret:
                return {"exchange": acct.exchange, "env_mode": acct.env_mode, "error": "missing credentials", "assets": [], "total_usdt": 0}

            creds = ExchangeCredentials(
                api_key=api_key, secret=secret,
                password=cipher.decrypt(acct.passphrase_ciphertext),
            )
            session = factory.create_session(
                exchange=acct.exchange, env_mode=acct.env_mode,
                proxies={}, credentials=creds,
            )
            try:
                balance = await asyncio.wait_for(
                    session.client.fetch_balance(),
                    timeout=BALANCE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                await session.close()
                return {"exchange": acct.exchange, "env_mode": acct.env_mode, "error": "timeout", "assets": [], "total_usdt": 0}
            except Exception:
                await session.close()
                raise

            non_usdt: list[str] = []
            assets_raw: list[dict] = []
            for currency, info in (balance.get("total") or {}).items():
                if isinstance(info, (int, float)):
                    total_amount = float(info)
                    free = total_amount
                    used = 0.0
                else:
                    free = float(info.get("free", 0) or 0)
                    used = float(info.get("used", 0) or 0)
                    total_amount = free + used
                if total_amount <= 1e-10:
                    continue
                if currency in ("USDT", "USD"):
                    usdt_value = total_amount
                else:
                    usdt_value = 0.0
                    if total_amount > 0:
                        non_usdt.append(currency)
                assets_raw.append({"currency": currency, "free": round(free, 8), "used": round(used, 8), "total": round(total_amount, 8), "usdt_value": round(usdt_value, 2)})

            if non_usdt:
                try:
                    symbols = [f"{c}/USDT" for c in non_usdt]
                    tickers = await asyncio.wait_for(
                        session.client.fetch_tickers(symbols),
                        timeout=BALANCE_TIMEOUT,
                    )
                    for a in assets_raw:
                        if a["currency"] in ("USDT", "USD"):
                            continue
                        ticker = tickers.get(f"{a['currency']}/USDT", {})
                        price = float(ticker.get("last", 0) or 0)
                        a["usdt_value"] = round(price * a["total"], 2)
                except Exception:
                    pass

            await session.close()

            assets = [a for a in assets_raw if a["usdt_value"] > 0.005]
            assets.sort(key=lambda a: a["usdt_value"], reverse=True)
            ex_total = sum(a["usdt_value"] for a in assets)
            return {"exchange": acct.exchange, "env_mode": acct.env_mode, "error": None, "assets": assets, "total_usdt": round(ex_total, 2)}
        except Exception as exc:
            return {"exchange": acct.exchange, "env_mode": acct.env_mode, "error": str(exc)[:200], "assets": [], "total_usdt": 0}

    results = await asyncio.gather(*[_fetch_one(a) for a in accounts])

    exchange_balances = sorted(results, key=lambda e: e["total_usdt"], reverse=True)
    total_usdt = sum(e["total_usdt"] for e in exchange_balances)
    return {"exchanges": exchange_balances, "total_usdt": round(total_usdt, 2)}
