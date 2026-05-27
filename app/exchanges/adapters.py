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


class ExchangeAdapter:
    def __init__(self, session: ExchangeAccountSession) -> None:
        self.session = session

    async def create_order(self, request: OrderRequest) -> dict:
        if self.session.client is not None and hasattr(self.session.client, "create_order"):
            params: dict = {}
            if request.reduce_only:
                params["reduceOnly"] = True
            if request.post_only:
                params["postOnly"] = True
            return await self.session.client.create_order(
                request.symbol,
                request.order_type,
                request.side,
                request.amount,
                request.price,
                params if params else None,
            )
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
