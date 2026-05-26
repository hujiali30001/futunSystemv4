import asyncio
import os
from dataclasses import dataclass, field

from app.exchanges.adapters import ExchangeAdapter
from app.exchanges.session_manager import (
    ExchangeClientFactory,
    ExchangeCredentials,
)
from app.runtime.worker_config import load_exchange_credential_from_env


@dataclass(slots=True)
class SandboxProbeResult:
    exchange: str
    ok: bool
    message: str
    non_zero_assets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OrderLifecycleProbeResult:
    exchange: str
    ok: bool
    message: str
    symbol: str
    order_id: str | None = None
    created_status: str | None = None
    fetched_status: str | None = None
    cancel_status: str | None = None
    final_status: str | None = None


class SandboxProbeService:
    def __init__(self, session_factory: ExchangeClientFactory | None = None) -> None:
        self.session_factory = session_factory or ExchangeClientFactory()

    async def probe_exchange(
        self,
        *,
        exchange: str,
        credentials: ExchangeCredentials,
        env_mode: str = "testnet",
        proxies: dict[str, str] | None = None,
    ) -> SandboxProbeResult:
        session = self.session_factory.create_session(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies or {},
            credentials=credentials,
        )
        adapter = ExchangeAdapter(session)
        try:
            await session.mark_ready()
            balance = await adapter.fetch_balance()
            assets = [
                asset
                for asset, value in balance.get("total", {}).items()
                if isinstance(value, (int, float)) and value > 0
            ]
            return SandboxProbeResult(
                exchange=exchange,
                ok=True,
                message="connected",
                non_zero_assets=assets,
            )
        except Exception as exc:
            return SandboxProbeResult(
                exchange=exchange,
                ok=False,
                message=str(exc),
                non_zero_assets=[],
            )
        finally:
            await adapter.close()

    async def probe_order_lifecycle(
        self,
        *,
        exchange: str,
        credentials: ExchangeCredentials,
        symbol: str,
        env_mode: str = "testnet",
        proxies: dict[str, str] | None = None,
    ) -> OrderLifecycleProbeResult:
        session = self.session_factory.create_session(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies or {},
            credentials=credentials,
        )
        adapter = ExchangeAdapter(session)
        try:
            await session.mark_ready()
            market = session.markets[symbol]
            ticker = await adapter.fetch_ticker(symbol)
            amount = self._build_safe_amount(market=market, ticker=ticker)
            price = self._build_safe_price(ticker=ticker)
            order = await adapter.create_order(
                request=self._build_limit_buy_request(
                    symbol=symbol,
                    amount=adapter.amount_to_precision(symbol, amount),
                    price=adapter.price_to_precision(symbol, price),
                    exchange=exchange,
                )
            )
            fetched = await adapter.fetch_order(order["id"], symbol)
            canceled = await adapter.cancel_order(order["id"], symbol)
            final_state = await adapter.fetch_order(order["id"], symbol)
            return OrderLifecycleProbeResult(
                exchange=exchange,
                ok=True,
                message="order_lifecycle_ok",
                symbol=symbol,
                order_id=order.get("id"),
                created_status=order.get("status"),
                fetched_status=fetched.get("status"),
                cancel_status=canceled.get("status"),
                final_status=final_state.get("status"),
            )
        except Exception as exc:
            return OrderLifecycleProbeResult(
                exchange=exchange,
                ok=False,
                message=str(exc),
                symbol=symbol,
            )
        finally:
            await adapter.close()

    @staticmethod
    def _build_safe_amount(*, market: dict, ticker: dict) -> float:
        min_amount = (
            market.get("limits", {})
            .get("amount", {})
            .get("min")
            or 0.0001
        )
        reference_price = ticker.get("bid") or ticker.get("last") or ticker.get("ask") or 1.0
        quote_budget = 15.0
        return max(float(min_amount), quote_budget / float(reference_price))

    @staticmethod
    def _build_safe_price(*, ticker: dict) -> float:
        bid = ticker.get("bid") or ticker.get("last") or ticker.get("ask")
        if bid is None:
            raise RuntimeError("missing bid price")
        return float(bid) * 0.95

    @staticmethod
    def _build_limit_buy_request(*, symbol: str, amount: float, price: float, exchange: str):
        from app.exchanges.adapters import OrderRequest

        request = OrderRequest(
            symbol=symbol,
            side="buy",
            order_type="limit",
            amount=amount,
            price=price,
        )
        if exchange in {"okx", "binance", "bybit", "bitget", "gate", "gateio"}:
            request.post_only = True
        return request

async def _run_probe() -> None:
    service = SandboxProbeService()
    exchanges = os.getenv(
        "SANDBOX_PROBE_EXCHANGES",
        "binance,okx,bybit,bitget,gate",
    ).split(",")
    order_mode = os.getenv("SANDBOX_ORDER_PROBE", "0") == "1"
    default_symbol = os.getenv("SANDBOX_ORDER_SYMBOL", "BTC/USDT")
    for raw_exchange in exchanges:
        exchange = raw_exchange.strip()
        if not exchange:
            continue
        credentials = load_exchange_credential_from_env(exchange)
        if credentials is None:
            print(f"{exchange}: skipped (missing credentials)")
            continue
        if order_mode:
            result = await service.probe_order_lifecycle(
                exchange=exchange,
                credentials=credentials,
                symbol=default_symbol,
            )
            print(
                f"{result.exchange}: {'ok' if result.ok else 'error'} | "
                f"{result.message} | symbol={result.symbol} | order_id={result.order_id or '-'} | "
                f"created={result.created_status or '-'} | fetched={result.fetched_status or '-'} | "
                f"canceled={result.cancel_status or '-'} | final={result.final_status or '-'}"
            )
        else:
            result = await service.probe_exchange(exchange=exchange, credentials=credentials)
            print(
                f"{result.exchange}: {'ok' if result.ok else 'error'} | "
                f"{result.message} | assets={','.join(result.non_zero_assets) or '-'}"
            )


def main() -> None:
    asyncio.run(_run_probe())


if __name__ == "__main__":
    main()
