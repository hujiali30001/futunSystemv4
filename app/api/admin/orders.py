from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_admin
from models import OrderRecord, FillRecord

router = APIRouter()


@router.get("/orders")
def admin_orders(
    task_id: int = Query(..., description="task id"),
    db: Session = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    orders = (
        db.query(OrderRecord)
        .filter(OrderRecord.task_id == task_id)
        .order_by(OrderRecord.id)
        .all()
    )

    order_ids = [o.id for o in orders]
    fills_by_order: dict[int, list] = {}
    if order_ids:
        fills = (
            db.query(FillRecord)
            .filter(FillRecord.order_id.in_(order_ids))
            .order_by(FillRecord.id)
            .all()
        )
        for f in fills:
            fills_by_order.setdefault(f.order_id, []).append(f)

    return {
        "task_id": task_id,
        "orders": [
            {
                "id": o.id,
                "leg_type": o.leg_type,
                "exchange": o.exchange,
                "side": o.side,
                "symbol": o.symbol,
                "order_type": o.order_type,
                "price": o.price,
                "amount": o.amount,
                "status": o.status,
                "avg_price": o.avg_price,
                "filled_amount": o.filled_amount,
                "fee_cost": o.fee_cost,
                "fee_currency": o.fee_currency,
                "client_order_id": o.client_order_id,
                "exchange_order_id": o.exchange_order_id,
                "error_reason": o.error_reason,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "fills": [
                    {
                        "id": f.id,
                        "fill_price": f.fill_price,
                        "fill_amount": f.fill_amount,
                        "fill_cost": f.fill_cost,
                        "fee_cost": f.fee_cost,
                        "fee_currency": f.fee_currency,
                        "exchange_trade_id": f.exchange_trade_id,
                        "filled_at": f.filled_at.isoformat() if f.filled_at else None,
                        "side": f.side,
                    }
                    for f in fills_by_order.get(o.id, [])
                ],
            }
            for o in orders
        ],
    }
