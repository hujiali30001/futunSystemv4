from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.exchanges.adapters import ExchangeAdapter
from app.exchanges.session_manager import ExchangeClientFactory, ExchangeCredentials
from app.trading.executor import TradeExecutor
from app.trading.tasks import ExecutionLeg, ExecutionTask


@dataclass(slots=True)
class RuntimeExecutionResult:
    ok: bool
    execution_status: str | None
    filled_exchanges: list[str]
    failed_exchanges: list[str]


class RuntimeTradeExecutionService:
    def __init__(self, session_factory: ExchangeClientFactory | None = None) -> None:
        self.session_factory = session_factory or ExchangeClientFactory()

    async def run_task(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict[str, ExchangeCredentials],
        execution_accounts_by_exchange: dict[str, Any] | None = None,
        symbol: str,
        target_quote_amount: float = 15.0,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> RuntimeExecutionResult:
        _ = execution_accounts_by_exchange
        unique_exchanges = list(dict.fromkeys(exchanges))
        sessions: dict[str, Any] = {}
        adapters: dict[str, ExchangeAdapter] = {}
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
                for exchange in unique_exchanges
            }
            buy_exchange = min(unique_exchanges, key=lambda name: tickers[name]["ask"])
            sell_exchange = max(unique_exchanges, key=lambda name: tickers[name]["bid"])

            buy_amount = adapters[buy_exchange].amount_to_precision(
                symbol,
                self._build_safe_amount(
                    sessions[buy_exchange].markets[symbol],
                    tickers[buy_exchange],
                    target_quote_amount=target_quote_amount,
                ),
            )
            sell_amount = adapters[sell_exchange].amount_to_precision(
                symbol,
                self._build_safe_amount(
                    sessions[sell_exchange].markets[symbol],
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

            task = ExecutionTask(
                task_id=f"{buy_exchange}:{sell_exchange}:{symbol}",
                symbol=symbol,
                open_legs=[
                    ExecutionLeg(
                        exchange=buy_exchange,
                        side="buy",
                        order_type="limit",
                        amount=float(buy_amount),
                        price=float(buy_price),
                    ),
                    ExecutionLeg(
                        exchange=sell_exchange,
                        side="sell",
                        order_type="limit",
                        amount=float(sell_amount),
                        price=float(sell_price),
                    ),
                ],
            )
            executor = TradeExecutor(adapter_factory=adapters)
            result = await executor.execute_open(task)
            return RuntimeExecutionResult(
                ok=result.status == "OPEN_HEDGED",
                execution_status=result.status,
                filled_exchanges=list(result.filled_exchanges),
                failed_exchanges=list(result.failed_exchanges),
            )
        finally:
            for adapter in adapters.values():
                await adapter.close()

    @staticmethod
    def _build_safe_amount(
        market: dict[str, Any],
        ticker: dict[str, Any],
        *,
        target_quote_amount: float,
    ) -> float:
        min_amount = market.get("limits", {}).get("amount", {}).get("min") or 0.0001
        reference_price = ticker.get("bid") or ticker.get("last") or ticker.get("ask") or 1.0
        requested_amount = float(target_quote_amount) / float(reference_price)
        return max(float(min_amount), requested_amount)
