import asyncio
from datetime import datetime
from sqlalchemy.orm import sessionmaker, Session

from models import OrderRecord, FillRecord


class OrderRecorder:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    async def record_submit(self, *,
                            task_id: int,
                            leg_type: str,
                            exchange: str,
                            side: str,
                            market_type: str,
                            client_order_id: str,
                            symbol: str,
                            order_type: str,
                            price: float | None,
                            amount: float,
                            ) -> int:
        def _do():
            with self._factory() as s:
                existing = s.query(OrderRecord).filter(
                    OrderRecord.client_order_id == client_order_id
                ).first()
                if existing is not None:
                    return existing.id
                rec = OrderRecord(
                    task_id=task_id,
                    leg_type=leg_type,
                    exchange=exchange,
                    side=side,
                    market_type=market_type,
                    client_order_id=client_order_id,
                    symbol=symbol,
                    order_type=order_type,
                    price=price,
                    amount=amount,
                    status="submitting",
                )
                s.add(rec)
                s.commit()
                return rec.id
        return await asyncio.to_thread(_do)

    async def record_open(self, *,
                          order_id: int,
                          exchange_order_id: str,
                          avg_price: float | None,
                          filled_amount: float,
                          fee_cost: float | None,
                          fee_currency: str | None,
                          raw_response: dict | None,
                          ) -> None:
        def _do():
            with self._factory() as s:
                rec = s.query(OrderRecord).filter(OrderRecord.id == order_id).first()
                if rec is None:
                    return
                rec.status = "open"
                rec.exchange_order_id = exchange_order_id
                rec.avg_price = avg_price
                rec.filled_amount = filled_amount
                rec.fee_cost = fee_cost
                rec.fee_currency = fee_currency
                rec.raw_payload_json = raw_response
                s.commit()
        await asyncio.to_thread(_do)

    async def record_failed(self, *, order_id: int, reason: str) -> None:
        def _do():
            with self._factory() as s:
                rec = s.query(OrderRecord).filter(OrderRecord.id == order_id).first()
                if rec is None:
                    return
                rec.status = "canceled"
                rec.error_reason = reason
                s.commit()
        await asyncio.to_thread(_do)

    async def record_poll_result(self, *,
                                 order_id: int,
                                 status: str,
                                 filled_amount: float,
                                 avg_price: float | None,
                                 fee_cost: float | None,
                                 fee_currency: str | None,
                                 new_fills: list[dict],
                                 task_id: int,
                                 ) -> None:
        def _do():
            with self._factory() as s:
                rec = s.query(OrderRecord).filter(OrderRecord.id == order_id).first()
                if rec is None:
                    return
                rec.status = status
                rec.filled_amount = filled_amount
                rec.avg_price = avg_price
                rec.fee_cost = fee_cost
                rec.fee_currency = fee_currency
                for fill in new_fills:
                    s.add(FillRecord(
                        task_id=task_id,
                        order_id=order_id,
                        leg_type=rec.leg_type,
                        exchange=rec.exchange,
                        side=rec.side,
                        symbol=rec.symbol,
                        fill_price=fill["price"],
                        fill_amount=fill["amount"],
                        fill_cost=fill["cost"],
                        fee_cost=fill.get("fee_cost"),
                        fee_currency=fill.get("fee_currency"),
                        exchange_trade_id=fill.get("trade_id"),
                        filled_at=datetime.utcnow(),
                    ))
                s.commit()
        await asyncio.to_thread(_do)

    async def insert_fills(self, *, order_id: int, task_id: int,
                           fills: list[dict]) -> None:
        def _do():
            with self._factory() as s:
                rec = s.query(OrderRecord).filter(OrderRecord.id == order_id).first()
                if rec is None:
                    return
                for fill in fills:
                    s.add(FillRecord(
                        task_id=task_id,
                        order_id=order_id,
                        leg_type=rec.leg_type,
                        exchange=rec.exchange,
                        side=rec.side,
                        symbol=rec.symbol,
                        fill_price=fill["price"],
                        fill_amount=fill["amount"],
                        fill_cost=fill["cost"],
                        fee_cost=fill.get("fee_cost"),
                        fee_currency=fill.get("fee_currency"),
                        exchange_trade_id=fill.get("trade_id"),
                        filled_at=datetime.utcnow(),
                    ))
                s.commit()
        await asyncio.to_thread(_do)

    def get_session(self) -> Session:
        return self._factory()
