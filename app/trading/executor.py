import asyncio
import uuid
from dataclasses import dataclass

from app.exchanges.adapters import OrderRequest
from app.trading.tasks import ExecutionTask


@dataclass(slots=True)
class ExecutionResult:
    status: str
    filled_exchanges: list[str]
    failed_exchanges: list[str]
    failed_errors: list[str] | None = None
    order_ids: dict[str, int] | None = None


class TradeExecutor:
    def __init__(self, *, adapter_factory: dict[str, object],
                 order_recorder=None) -> None:
        self.adapter_factory = adapter_factory
        self.order_recorder = order_recorder

    async def execute_open(self, task: ExecutionTask) -> ExecutionResult:
        coroutines = []
        exchanges = []
        record_ids: dict[str, int] = {}

        for leg in task.open_legs:
            exchanges.append(leg.exchange)
            market_type = getattr(leg, "market_type", "spot")
            client_id = f"{task.task_id}_{leg.exchange}_{uuid.uuid4().hex[:8]}"

            if self.order_recorder is not None:
                try:
                    tid = int(task.task_id)
                except (ValueError, TypeError):
                    tid = 0
                oid = await self.order_recorder.record_submit(
                    task_id=tid,
                    leg_type="spot" if market_type == "spot" else "derivative",
                    exchange=leg.exchange,
                    side=leg.side,
                    market_type=market_type,
                    client_order_id=client_id,
                    symbol=task.symbol,
                    order_type=leg.order_type,
                    price=leg.price,
                    amount=leg.amount,
                )
                record_ids[leg.exchange] = oid

            adapter = self.adapter_factory[leg.exchange]
            coroutines.append(
                _place_order(
                    adapter, task, leg, market_type,
                    self.order_recorder, record_ids.get(leg.exchange),
                )
            )

        responses = await asyncio.gather(*coroutines, return_exceptions=True)
        filled_exchanges: list[str] = []
        failed_exchanges: list[str] = []
        failed_errors: list[str] = []

        for exchange, result in zip(exchanges, responses):
            if isinstance(result, Exception):
                failed_exchanges.append(exchange)
                failed_errors.append(f"{exchange}: {result}")
            else:
                filled_exchanges.append(exchange)

        status = "OPEN_HEDGED" if not failed_exchanges else "OPEN_PARTIAL"
        return ExecutionResult(
            status=status,
            filled_exchanges=filled_exchanges,
            failed_exchanges=failed_exchanges,
            failed_errors=failed_errors if failed_errors else None,
            order_ids=record_ids if record_ids else None,
        )


async def _place_order(adapter, task, leg, market_type, recorder, record_id):
    try:
        result = await adapter.create_order(
            OrderRequest(
                symbol=task.symbol,
                side=leg.side,
                order_type=leg.order_type,
                amount=leg.amount,
                price=leg.price,
                market_type=market_type,
            )
        )
    except Exception as exc:
        if recorder is not None and record_id is not None:
            await recorder.record_failed(order_id=record_id, reason=str(exc))
        raise

    if recorder is not None and record_id is not None:
        filled = float(result.get("filled", 0) or 0)
        fee = result.get("fee")
        if isinstance(fee, dict):
            fee_cost = float(fee.get("cost", 0)) if fee.get("cost") else None
            fee_currency = str(fee.get("currency", "")) if fee.get("currency") else None
        else:
            fee_cost = None
            fee_currency = None
        await recorder.record_open(
            order_id=record_id,
            exchange_order_id=str(result.get("id", "")),
            avg_price=float(result.get("average", 0) or 0) if filled > 0 else None,
            filled_amount=filled,
            fee_cost=fee_cost,
            fee_currency=fee_currency,
            raw_response=result,
        )
    return result
