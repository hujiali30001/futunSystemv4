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
                balance = await asyncio.wait_for(session.client.fetch_balance(), timeout=BALANCE_TIMEOUT)
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
                    non_usdt.append(currency)
                assets_raw.append({"currency": currency, "free": round(free, 8), "used": round(used, 8), "total": round(total_amount, 8), "usdt_value": round(usdt_value, 2)})

            if non_usdt:
                symbols = [f"{c}/USDT" for c in non_usdt]
                try:
                    tickers = await asyncio.wait_for(session.client.fetch_tickers(symbols), timeout=BALANCE_TIMEOUT)
                    for a in assets_raw:
                        if a["currency"] in ("USDT", "USD"):
                            continue
                        t = tickers.get(f"{a['currency']}/USDT", {})
                        p = float(t.get("last", 0) or 0)
                        a["usdt_value"] = round(p * a["total"], 2)
                except Exception:
                    sem = asyncio.Semaphore(5)
                    async def _price_one(cur):
                        async with sem:
                            try:
                                t = await asyncio.wait_for(session.client.fetch_ticker(f"{cur}/USDT"), timeout=8)
                                return float(t.get("last", 0) or 0)
                            except Exception:
                                return 0.0
                    prices = dict(zip(non_usdt, await asyncio.gather(*[_price_one(c) for c in non_usdt])))
                    for a in assets_raw:
                        if a["currency"] in ("USDT", "USD"):
                            continue
                        a["usdt_value"] = round(prices.get(a["currency"], 0) * a["total"], 2)

            await session.close()

            assets = [a for a in assets_raw if a["usdt_value"] >= 1.0]
            assets.sort(key=lambda a: a["usdt_value"], reverse=True)
            ex_total = sum(a["usdt_value"] for a in assets)
            return {"exchange": acct.exchange, "env_mode": acct.env_mode, "error": None, "assets": assets, "total_usdt": round(ex_total, 2)}
        except asyncio.TimeoutError:
            return {"exchange": acct.exchange, "env_mode": acct.env_mode, "error": "timeout", "assets": [], "total_usdt": 0}
        except Exception as exc:
            return {"exchange": acct.exchange, "env_mode": acct.env_mode, "error": str(exc)[:200], "assets": [], "total_usdt": 0}

    results = await asyncio.gather(*[_fetch_one(a) for a in accounts])

    exchange_balances = sorted(results, key=lambda e: e["total_usdt"], reverse=True)
    total_usdt = sum(e["total_usdt"] for e in exchange_balances)
    return {"exchanges": exchange_balances, "total_usdt": round(total_usdt, 2)}


class LiquidateRequest(BaseModel):
    exchanges: list[str] | None = None


@router.post("/settings/liquidate")
async def liquidate_all(
    body: LiquidateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
    cipher: SecretCipher = Depends(get_cipher),
):
    accounts = db.query(ExchangeAccount).filter(ExchangeAccount.user_id == user["user_id"]).all()
    if not accounts:
        return {"orders": [], "summary": {"total_sold_usdt": 0, "orders_placed": 0, "errors": 0}}

    factory = ExchangeClientFactory()
    LIQUIDATE_TIMEOUT = 30

    async def _liquidate_one(acct) -> dict:
        ex_results: list[dict] = []
        ex_name = acct.exchange
        try:
            api_key = cipher.decrypt(acct.api_key_ciphertext)
            secret = cipher.decrypt(acct.secret_ciphertext)
            if not api_key or not secret:
                return {"exchange": ex_name, "env_mode": acct.env_mode, "error": "missing credentials", "orders": []}

            creds = ExchangeCredentials(api_key=api_key, secret=secret, password=cipher.decrypt(acct.passphrase_ciphertext))
            session = factory.create_session(exchange=ex_name, env_mode=acct.env_mode, proxies={}, credentials=creds)

            markets_session = factory.create_session(exchange=ex_name, env_mode=acct.env_mode, proxies={})
            try:
                await asyncio.wait_for(markets_session.client.load_markets(), timeout=LIQUIDATE_TIMEOUT)
                markets = dict(markets_session.client.markets)
            finally:
                await markets_session.close()

            try:
                bal = await asyncio.wait_for(session.client.fetch_balance(), timeout=LIQUIDATE_TIMEOUT)
            except asyncio.TimeoutError:
                await session.close()
                return {"exchange": ex_name, "env_mode": acct.env_mode, "error": "timeout", "orders": []}

            holdings: list[tuple[str, float, float]] = []
            for currency, info in (bal.get("total") or {}).items():
                if isinstance(info, (int, float)):
                    total_amount = float(info)
                    free_amount = total_amount
                else:
                    total_amount = float(info.get("total", 0) or 0)
                    free_amount = float(info.get("free", total_amount) or 0)
                if total_amount <= 1e-8 or currency in ("USDT", "USD"):
                    continue
                sell_amount = free_amount if free_amount > 1e-8 else total_amount
                holdings.append((currency, total_amount, sell_amount))

            if not holdings:
                await session.close()
                return {"exchange": ex_name, "env_mode": acct.env_mode, "error": None, "orders": []}

            session.client.markets = markets

            for currency, total_amount, sell_amount in holdings:
                symbol = f"{currency}/USDT"
                market = markets.get(symbol)
                if market is None:
                    ex_results.append({"symbol": symbol, "status": "skipped", "reason": "no market"})
                    continue

                limits = market.get("limits", {}).get("amount", {})
                min_amount = float(limits.get("min", 0) or 0)
                if sell_amount < min_amount and min_amount > 0:
                    ex_results.append({"symbol": symbol, "status": "skipped", "reason": f"{sell_amount:.8f} < min {min_amount}"})
                    continue

                try:
                    ticker = await asyncio.wait_for(session.client.fetch_ticker(symbol), timeout=10)
                    price = float(ticker.get("last", 0) or 0)
                    if price <= 0:
                        ex_results.append({"symbol": symbol, "status": "skipped", "reason": "no price"})
                        continue

                    amount_str = session.client.amount_to_precision(symbol, sell_amount)
                    order = await asyncio.wait_for(
                        session.client.create_market_sell_order(symbol, amount_str),
                        timeout=20,
                    )
                    filled = float(order.get("filled", 0) or 0)
                    cost = float(order.get("cost", 0) or 0)
                    ex_results.append({"symbol": symbol, "status": order.get("status", "unknown"), "filled": filled, "cost": round(cost, 2), "price": round(price, 6), "order_id": order.get("id", "")})
                except Exception as exc:
                    ex_results.append({"symbol": symbol, "status": "error", "reason": str(exc)[:150]})

            await session.close()
            return {"exchange": ex_name, "env_mode": acct.env_mode, "error": None, "orders": ex_results}
        except Exception as exc:
            return {"exchange": ex_name, "env_mode": acct.env_mode, "error": str(exc)[:200], "orders": []}

    target = [a for a in accounts if body.exchanges is None or a.exchange in body.exchanges]
    all_results = await asyncio.gather(*[_liquidate_one(a) for a in target])

    total_sold = 0.0
    orders_placed = 0
    errors = 0
    for r in all_results:
        for o in r.get("orders", []):
            if o.get("status") in ("closed", "open"):
                total_sold += o.get("cost", 0)
                orders_placed += 1
            elif o.get("status") == "error":
                errors += 1

    return {"exchanges": all_results, "summary": {"total_sold_usdt": round(total_sold, 2), "orders_placed": orders_placed, "errors": errors}}
