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
    reason: str | None = None
    failed_errors: list[str] | None = None


class RuntimeTradeExecutionService:
    def __init__(self, session_factory: ExchangeClientFactory | None = None) -> None:
        self.session_factory = session_factory or ExchangeClientFactory()

    async def run_task(
        self,
        *,
        exchanges: list[str],
        buy_exchange: str | None = None,
        sell_exchange: str | None = None,
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
            selected_buy_exchange = buy_exchange or min(
                unique_exchanges, key=lambda name: tickers[name]["ask"]
            )
            selected_sell_exchange = sell_exchange or max(
                unique_exchanges, key=lambda name: tickers[name]["bid"]
            )

            buy_amount = adapters[selected_buy_exchange].amount_to_precision(
                symbol,
                self._build_safe_amount(
                    sessions[selected_buy_exchange].markets[symbol],
                    tickers[selected_buy_exchange],
                    target_quote_amount=target_quote_amount,
                ),
            )
            sell_amount = adapters[selected_sell_exchange].amount_to_precision(
                symbol,
                self._build_safe_amount(
                    sessions[selected_sell_exchange].markets[symbol],
                    tickers[selected_sell_exchange],
                    target_quote_amount=target_quote_amount,
                ),
            )
            buy_price = adapters[selected_buy_exchange].price_to_precision(
                symbol,
                float(tickers[selected_buy_exchange]["bid"]) * 0.95,
            )
            sell_price = adapters[selected_sell_exchange].price_to_precision(
                symbol,
                float(tickers[selected_sell_exchange]["ask"]) * 1.05,
            )

            buy_usdt = await adapters[selected_buy_exchange].fetch_usdt_balance()
            sell_usdt = await adapters[selected_sell_exchange].fetch_usdt_balance()
            if selected_buy_exchange == selected_sell_exchange:
                min_required = target_quote_amount * 2
                if buy_usdt < min_required:
                    return RuntimeExecutionResult(
                        ok=False, execution_status="SKIPPED",
                        filled_exchanges=[], failed_exchanges=[selected_buy_exchange],
                        reason=f"InsufficientFunds: {selected_buy_exchange} USDT={buy_usdt:.2f} need={min_required:.2f}",
                    )
            else:
                if buy_usdt < target_quote_amount:
                    return RuntimeExecutionResult(
                        ok=False, execution_status="SKIPPED",
                        filled_exchanges=[], failed_exchanges=[selected_buy_exchange],
                        reason=f"InsufficientFunds: {selected_buy_exchange} USDT={buy_usdt:.2f} need={target_quote_amount:.0f}",
                    )
                if sell_usdt < target_quote_amount:
                    return RuntimeExecutionResult(
                        ok=False, execution_status="SKIPPED",
                        filled_exchanges=[], failed_exchanges=[selected_sell_exchange],
                        reason=f"InsufficientFunds: {selected_sell_exchange} USDT={sell_usdt:.2f} need={target_quote_amount:.0f}",
                    )

            task = ExecutionTask(
                task_id=f"{selected_buy_exchange}:{selected_sell_exchange}:{symbol}",
                symbol=symbol,
                open_legs=[
                    ExecutionLeg(
                        exchange=selected_buy_exchange,
                        side="buy",
                        order_type="limit",
                        amount=float(buy_amount),
                        price=float(buy_price),
                    ),
                    ExecutionLeg(
                        exchange=selected_sell_exchange,
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
                failed_errors=list(getattr(result, "failed_errors", None) or []) or None,
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
