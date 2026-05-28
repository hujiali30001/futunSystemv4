import asyncio
from dataclasses import dataclass


@dataclass(slots=True)
class PollResult:
    all_closed: bool
    new_fills: list[dict]
    status: str
    filled_amount: float
    avg_price: float | None
    fee_cost: float | None
    fee_currency: str | None


class OrderPoller:
    def __init__(self, *,
                 order_recorder=None,
                 adapter_factory=None,
                 timeout: float = 300.0,
                 interval: float = 2.0) -> None:
        self.order_recorder = order_recorder
        self.adapter_factory = adapter_factory or {}
        self.timeout = timeout
        self.interval = interval

    async def poll_until_closed(self, *,
                                order_id: int,
                                exchange: str,
                                exchange_order_id: str,
                                symbol: str,
                                task_id: int,
                                current_filled: float = 0.0,
                                ) -> PollResult:
        loop = asyncio.get_running_loop()
        start = loop.time()
        total_filled = current_filled
        new_fills_all: list[dict] = []
        last_status = "open"

        while True:
            elapsed = loop.time() - start
            if elapsed > self.timeout:
                return PollResult(
                    all_closed=False,
                    new_fills=new_fills_all,
                    status="expired",
                    filled_amount=total_filled,
                    avg_price=None,
                    fee_cost=None,
                    fee_currency=None,
                )

            try:
                adapter = self.adapter_factory.get(exchange)
                if adapter is None or adapter.session is None:
                    await asyncio.sleep(self.interval)
                    continue

                client = adapter.session.client
                if client is None or not hasattr(client, "fetch_order"):
                    await asyncio.sleep(self.interval)
                    continue

                order_info = await client.fetch_order(exchange_order_id, symbol)
                last_status = (order_info or {}).get("status", "open")
                filled = float((order_info or {}).get("filled", 0) or 0)

                if filled > total_filled:
                    delta = filled - total_filled
                    avg = float((order_info or {}).get("average", 0) or 0)
                    fee = (order_info or {}).get("fee")
                    fee_cost_val = float(fee.get("cost", 0)) if isinstance(fee, dict) else 0.0
                    fee_currency_val = str(fee.get("currency", "")) if isinstance(fee, dict) else ""
                    trade_id = (order_info or {}).get("id", exchange_order_id)

                    fill = {
                        "price": avg,
                        "amount": delta,
                        "cost": avg * delta,
                        "fee_cost": fee_cost_val if fee_cost_val else None,
                        "fee_currency": fee_currency_val if fee_currency_val else None,
                        "trade_id": trade_id,
                    }
                    new_fills_all.append(fill)
                    total_filled = filled

                    if self.order_recorder is not None:
                        await self.order_recorder.record_poll_result(
                            order_id=order_id,
                            status=last_status,
                            filled_amount=total_filled,
                            avg_price=avg if total_filled > 0 else None,
                            fee_cost=fee_cost_val if fee_cost_val else None,
                            fee_currency=fee_currency_val if fee_currency_val else None,
                            new_fills=[fill],
                            task_id=task_id,
                        )

                if last_status in ("closed", "canceled", "expired"):
                    return PollResult(
                        all_closed=(last_status == "closed"),
                        new_fills=new_fills_all,
                        status=last_status,
                        filled_amount=total_filled,
                        avg_price=float((order_info or {}).get("average", 0)) if total_filled > 0 else None,
                        fee_cost=fee_cost_val if fee_cost_val else None,
                        fee_currency=fee_currency_val if fee_currency_val else None,
                    )

            except Exception:
                pass

            await asyncio.sleep(self.interval)
