from dataclasses import dataclass

from app.exchanges.session_manager import ExchangeAccountSession


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: str
    order_type: str
    amount: float
    price: float | None = None
    reduce_only: bool = False
    post_only: bool = False
    market_type: str = "spot"


class ExchangeAdapter:
    def __init__(self, session: ExchangeAccountSession) -> None:
        self.session = session

    async def create_order(self, request: OrderRequest) -> dict:
        if self.session.client is not None and hasattr(self.session.client, "create_order"):
            params: dict = {}
            client = self.session.client
            saved_default_type = client.options.get("defaultType", "spot") if hasattr(client, "options") else "spot"
            try:
                if hasattr(client, "options") and request.market_type in ("spot", "swap", "future", "margin"):
                    client.options["defaultType"] = request.market_type
                if request.reduce_only:
                    params["reduceOnly"] = True
                if request.post_only:
                    params["postOnly"] = True
                return await client.create_order(
                    request.symbol,
                    request.order_type,
                    request.side,
                    request.amount,
                    request.price,
                    params,
                )
            finally:
                if hasattr(client, "options"):
                    client.options["defaultType"] = saved_default_type
        return {
            "id": "simulated-order",
            "symbol": request.symbol,
            "side": request.side,
            "amount": request.amount,
            "status": "open",
        }

    async def fetch_balance(self) -> dict:
        if self.session.client is not None and hasattr(self.session.client, "fetch_balance"):
            return await self.session.client.fetch_balance()
        return {"total": {}}

    async def fetch_usdt_balance(self, market_type: str = "spot") -> float:
        try:
            client = self.session.client
            if client is not None and hasattr(client, "options") and market_type:
                client.options["defaultType"] = market_type
            raw = await self.fetch_balance()
            free_balance = raw.get("free", {}) if isinstance(raw, dict) else {}
            return float(free_balance.get("USDT", 0) or 0)
        except Exception:
            return 0.0

    async def fetch_ticker(self, symbol: str) -> dict:
        if self.session.client is not None and hasattr(self.session.client, "fetch_ticker"):
            return await self.session.client.fetch_ticker(symbol)
        return {"symbol": symbol, "bid": None, "ask": None, "last": None}

    async def fetch_orderbook(self, symbol: str, limit: int = 5) -> dict:
        if self.session.client is not None and hasattr(self.session.client, "fetch_order_book"):
            return await self.session.client.fetch_order_book(symbol, limit=limit)
        return {"symbol": symbol, "bids": [], "asks": []}

    async def fetch_order(self, order_id: str, symbol: str) -> dict:
        if self.session.client is not None and hasattr(self.session.client, "fetch_order"):
            return await self.session.client.fetch_order(order_id, symbol)
        return {"id": order_id, "symbol": symbol, "status": "open"}

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        if self.session.client is not None and hasattr(self.session.client, "cancel_order"):
            return await self.session.client.cancel_order(order_id, symbol)
        return {"id": order_id, "symbol": symbol, "status": "canceled"}

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        client = self.session.client
        if client is not None and hasattr(client, "amount_to_precision"):
            return float(client.amount_to_precision(symbol, amount))
        return amount

    def price_to_precision(self, symbol: str, price: float) -> float:
        client = self.session.client
        if client is not None and hasattr(client, "price_to_precision"):
            return float(client.price_to_precision(symbol, price))
        return price

    async def close(self) -> None:
        await self.session.close()
