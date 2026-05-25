import asyncio
from dataclasses import dataclass

from app.exchanges.adapters import ExchangeAdapter, OrderRequest
from app.exchanges.session_manager import ExchangeClientFactory, ExchangeCredentials


@dataclass(slots=True)
class SpotArbitrageTaskResult:
    ok: bool
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_order_id: str | None
    sell_order_id: str | None
    buy_final_status: str | None
    sell_final_status: str | None
    message: str
    execution_status: str | None = None
    filled_exchanges: list[str] | None = None
    failed_exchanges: list[str] | None = None


class SpotArbitrageProbeService:
    def __init__(self, session_factory: ExchangeClientFactory | None = None) -> None:
        self.session_factory = session_factory or ExchangeClientFactory()

    async def run_task(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict[str, ExchangeCredentials],
        symbol: str,
        target_quote_amount: float = 15.0,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> SpotArbitrageTaskResult:
        sessions = {}
        adapters = {}
        unique_exchanges = list(dict.fromkeys(exchanges))
        filled_exchanges: list[str] = []
        failed_exchanges: list[str] = []
        buy_exchange = ""
        sell_exchange = ""
        buy_order = None
        sell_order = None
        try:
            for exchange in unique_exchanges:
                session = self.session_factory.create_session(
                    exchange=exchange,
                    env_mode=env_mode,
                    proxies=(proxies_by_exchange or {}).get(exchange, {}),
                    credentials=credentials_by_exchange[exchange],
                )
                await session.mark_ready()
                sessions[exchange] = session
                adapters[exchange] = ExchangeAdapter(session)

            tickers = {
                exchange: await adapters[exchange].fetch_ticker(symbol)
                for exchange in exchanges
            }
            buy_exchange = min(exchanges, key=lambda name: tickers[name]["ask"])
            sell_exchange = max(exchanges, key=lambda name: tickers[name]["bid"])

            buy_market = sessions[buy_exchange].markets[symbol]
            sell_market = sessions[sell_exchange].markets[symbol]
            buy_amount = adapters[buy_exchange].amount_to_precision(
                symbol,
                self._build_safe_amount(
                    buy_market,
                    tickers[buy_exchange],
                    target_quote_amount=target_quote_amount,
                ),
            )
            sell_amount = adapters[sell_exchange].amount_to_precision(
                symbol,
                self._build_safe_amount(
                    sell_market,
                    tickers[sell_exchange],
                    target_quote_amount=target_quote_amount,
                ),
            )
            buy_price = adapters[buy_exchange].price_to_precision(
                symbol,
                float(tickers[buy_exchange]["bid"]) * 0.95,
            )
            sell_price = adapters[sell_exchange].price_to_precision(
                symbol,
                float(tickers[sell_exchange]["ask"]) * 1.05,
            )

            buy_request = OrderRequest(
                symbol=symbol,
                side="buy",
                order_type="limit",
                amount=buy_amount,
                price=buy_price,
                post_only=buy_exchange in {"okx", "gate", "gateio"},
            )
            sell_request = OrderRequest(
                symbol=symbol,
                side="sell",
                order_type="limit",
                amount=sell_amount,
                price=sell_price,
                post_only=sell_exchange in {"okx", "gate", "gateio"},
            )

            buy_order = await adapters[buy_exchange].create_order(buy_request)
            filled_exchanges.append(buy_exchange)
            sell_order = await adapters[sell_exchange].create_order(sell_request)
            filled_exchanges.append(sell_exchange)
            await adapters[buy_exchange].fetch_order(buy_order["id"], symbol)
            await adapters[sell_exchange].fetch_order(sell_order["id"], symbol)
            await adapters[buy_exchange].cancel_order(buy_order["id"], symbol)
            await adapters[sell_exchange].cancel_order(sell_order["id"], symbol)
            buy_final = await adapters[buy_exchange].fetch_order(buy_order["id"], symbol)
            sell_final = await adapters[sell_exchange].fetch_order(
                sell_order["id"], symbol
            )
            return SpotArbitrageTaskResult(
                ok=True,
                symbol=symbol,
                buy_exchange=buy_exchange,
                sell_exchange=sell_exchange,
                buy_order_id=buy_order.get("id"),
                sell_order_id=sell_order.get("id"),
                buy_final_status=buy_final.get("status"),
                sell_final_status=sell_final.get("status"),
                message="spot_arbitrage_task_ok",
                execution_status="OPEN_HEDGED",
                filled_exchanges=filled_exchanges,
                failed_exchanges=[],
            )
        except Exception as exc:
            if buy_exchange and buy_order is not None and buy_exchange not in filled_exchanges:
                filled_exchanges.append(buy_exchange)
            if sell_exchange and sell_order is None and sell_exchange not in failed_exchanges:
                failed_exchanges.append(sell_exchange)
            return SpotArbitrageTaskResult(
                ok=False,
                symbol=symbol,
                buy_exchange=buy_exchange,
                sell_exchange=sell_exchange,
                buy_order_id=None if buy_order is None else buy_order.get("id"),
                sell_order_id=None if sell_order is None else sell_order.get("id"),
                buy_final_status=None,
                sell_final_status=None,
                message=str(exc),
                execution_status="OPEN_PARTIAL" if filled_exchanges and failed_exchanges else None,
                filled_exchanges=filled_exchanges,
                failed_exchanges=failed_exchanges,
            )
        finally:
            await asyncio.gather(
                *[adapter.close() for adapter in adapters.values()],
                return_exceptions=True,
            )
            # Give aiohttp/ccxt a brief grace window to finish async connector cleanup.
            await asyncio.sleep(0.05)

    @staticmethod
    def _build_safe_amount(
        market: dict,
        ticker: dict,
        *,
        target_quote_amount: float,
    ) -> float:
        min_amount = market.get("limits", {}).get("amount", {}).get("min") or 0.0001
        reference_price = ticker.get("bid") or ticker.get("last") or ticker.get("ask") or 1.0
        requested_amount = float(target_quote_amount) / float(reference_price)
        return max(float(min_amount), requested_amount)
