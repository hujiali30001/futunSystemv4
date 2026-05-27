import asyncio
from dataclasses import dataclass

from app.exchanges.adapters import OrderRequest
from app.trading.tasks import ExecutionTask


@dataclass(slots=True)
class ExecutionResult:
    status: str
    filled_exchanges: list[str]
    failed_exchanges: list[str]
    failed_errors: list[str] | None = None


class TradeExecutor:
    def __init__(self, *, adapter_factory: dict[str, object]) -> None:
        self.adapter_factory = adapter_factory

    async def execute_open(self, task: ExecutionTask) -> ExecutionResult:
        coroutines = []
        exchanges = []
        for leg in task.open_legs:
            exchanges.append(leg.exchange)
            adapter = self.adapter_factory[leg.exchange]
            coroutines.append(
                adapter.create_order(
                    OrderRequest(
                        symbol=task.symbol,
                        side=leg.side,
                        order_type=leg.order_type,
                        amount=leg.amount,
                        price=leg.price,
                        market_type=getattr(leg, "market_type", "spot"),
                    )
                )
            )

        responses = await asyncio.gather(*coroutines, return_exceptions=True)
        filled = [exchange for exchange, result in zip(exchanges, responses) if not isinstance(result, Exception)]
        failed = [exchange for exchange, result in zip(exchanges, responses) if isinstance(result, Exception)]
        failed_errors = [f"{exchange}: {result}" for exchange, result in zip(exchanges, responses) if isinstance(result, Exception)] or None
        status = "OPEN_HEDGED" if not failed else "OPEN_PARTIAL"
        return ExecutionResult(status=status, filled_exchanges=filled, failed_exchanges=failed, failed_errors=failed_errors)
