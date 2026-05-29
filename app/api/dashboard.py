from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from models import ArbitrageTask, StrategyConfig, FillRecord, PositionSnapshot

router = APIRouter()


def _mk_db():
    from app.api.deps import _session_factory
    return _session_factory()


@router.get("/ping")
def dashboard_ping():
    try:
        db = _mk_db()
        db.execute(db.query(StrategyConfig).exists().select())
        db.close()
        return {"db": "ok"}
    except Exception as e:
        return {"db": str(e)[:200]}


@router.get("/summary")
def dashboard_summary(
    user: dict = Depends(get_current_user),
):
    user_id = user["user_id"]
    result = {
        "stats": {"active_strategies": 0, "open_positions": 0, "today_trades": 0, "total_trades": 0},
        "pnl": {"today": 0.0, "week": 0.0, "today_fees": 0.0, "today_net": 0.0},
        "checked_at": datetime.utcnow().isoformat(),
    }

    try:
        db = _mk_db()
        try:
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

            result["stats"]["active_strategies"] = sum(
                1 for s in db.query(StrategyConfig).filter(StrategyConfig.user_id == user_id).all()
                if s.is_enabled
            )
            result["stats"]["open_positions"] = db.query(ArbitrageTask).filter(
                ArbitrageTask.user_id == user_id,
                ArbitrageTask.status.in_(["HOLDING", "OPEN_HEDGED", "OPEN_PARTIAL", "RUNNING"]),
            ).count()
            result["stats"]["total_trades"] = db.query(ArbitrageTask).filter(
                ArbitrageTask.user_id == user_id).count()

            today_tasks = db.query(ArbitrageTask).filter(
                ArbitrageTask.user_id == user_id,
                ArbitrageTask.finished_at >= today,
                ArbitrageTask.status.in_(["CLOSED", "SUCCEEDED"]),
            ).all()
            result["stats"]["today_trades"] = len(today_tasks)

            closed_ids = [t.id for t in today_tasks]
            if closed_ids:
                result["pnl"]["today"] = round(sum(float(r.realized_pnl or 0) for r in
                    db.query(PositionSnapshot).filter(
                        PositionSnapshot.user_id == user_id,
                        PositionSnapshot.task_id.in_(closed_ids),
                        PositionSnapshot.snapshot_type.in_(["close", "partial_close"]),
                    ).all()), 2)

            fee_ids = [t.id for t in db.query(ArbitrageTask).filter(
                ArbitrageTask.user_id == user_id,
                ArbitrageTask.finished_at >= today).all()]
            if fee_ids:
                result["pnl"]["today_fees"] = round(sum(float(f.fee_cost or 0) for f in
                    db.query(FillRecord).filter(FillRecord.task_id.in_(fee_ids)).all()), 2)

            result["pnl"]["today_net"] = round(result["pnl"]["today"] - result["pnl"]["today_fees"], 2)
        finally:
            db.close()
    except Exception:
        pass

    return result
