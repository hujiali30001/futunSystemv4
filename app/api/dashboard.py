from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.api.deps import get_current_user

router = APIRouter()


def _db():
    from app.api.deps import _session_factory
    return _session_factory()


@router.get("/ping")
def dashboard_ping():
    try:
        db = _db()
        db.execute(text("SELECT 1"))
        db.close()
        return {"db": "ok"}
    except Exception as e:
        return {"db": str(e)[:200]}


@router.get("/summary")
def dashboard_summary(user: dict = Depends(get_current_user)):
    uid = user["user_id"]
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ago = now - timedelta(days=7)

    try:
        db = _db()
    except Exception:
        return _empty(now)

    try:
        result = {
            "stats": {"active_strategies": 0, "open_positions": 0, "today_trades": 0, "total_trades": 0},
            "pnl": {"today": 0.0, "week": 0.0, "today_fees": 0.0, "today_net": 0.0},
            "checked_at": now.isoformat(),
        }

        result["stats"]["active_strategies"] = db.execute(
            text("SELECT count(*) FROM strategy_configs WHERE user_id=:uid AND is_enabled=true"),
            {"uid": uid},
        ).fetchone()[0]

        result["stats"]["open_positions"] = db.execute(
            text("SELECT count(*) FROM arbitrage_tasks WHERE user_id=:uid AND status IN ('HOLDING','OPEN_HEDGED','OPEN_PARTIAL','RUNNING')"),
            {"uid": uid},
        ).fetchone()[0]

        result["stats"]["total_trades"] = db.execute(
            text("SELECT count(*) FROM arbitrage_tasks WHERE user_id=:uid"),
            {"uid": uid},
        ).fetchone()[0]

        result["stats"]["today_trades"] = db.execute(
            text("SELECT count(*) FROM arbitrage_tasks WHERE user_id=:uid AND finished_at>=:t AND status IN ('CLOSED','SUCCEEDED')"),
            {"uid": uid, "t": today},
        ).fetchone()[0]

        row = db.execute(text(
            "SELECT coalesce(sum(realized_pnl),0) FROM position_snapshots WHERE user_id=:uid AND snapshot_type IN ('close','partial_close')"
            " AND task_id IN (SELECT id FROM arbitrage_tasks WHERE user_id=:uid AND finished_at>=:t AND status IN ('CLOSED','SUCCEEDED'))"
        ), {"uid": uid, "t": today}).fetchone()
        result["pnl"]["today"] = round(float(row[0] or 0), 2)

        row = db.execute(text(
            "SELECT coalesce(sum(fee_cost),0) FROM fill_records WHERE task_id IN"
            " (SELECT id FROM arbitrage_tasks WHERE user_id=:uid AND finished_at>=:t)"
        ), {"uid": uid, "t": today}).fetchone()
        result["pnl"]["today_fees"] = round(float(row[0] or 0), 2)
        result["pnl"]["today_net"] = round(result["pnl"]["today"] - result["pnl"]["today_fees"], 2)

        row = db.execute(text(
            "SELECT coalesce(sum(realized_pnl),0) FROM position_snapshots WHERE user_id=:uid AND snapshot_type IN ('close','partial_close')"
            " AND task_id IN (SELECT id FROM arbitrage_tasks WHERE user_id=:uid AND finished_at>=:t AND status IN ('CLOSED','SUCCEEDED'))"
        ), {"uid": uid, "t": ago}).fetchone()
        result["pnl"]["week"] = round(float(row[0] or 0), 2)

        db.close()
        return result
    except Exception:
        try:
            db.close()
        except Exception:
            pass
        return _empty(now)


def _empty(now):
    return {
        "stats": {"active_strategies": 0, "open_positions": 0, "today_trades": 0, "total_trades": 0},
        "pnl": {"today": 0.0, "week": 0.0, "today_fees": 0.0, "today_net": 0.0},
        "checked_at": now.isoformat(),
    }
