from fastapi import APIRouter, Depends, Query

from app.api.deps import get_redis

router = APIRouter()


@router.get("")
async def list_opportunities(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("open_spread_bps", pattern="^(open_spread_bps|close_spread_bps)$"),
    redis=Depends(get_redis),
):
    zset_key = "arb:zset:open" if sort_by == "open_spread_bps" else "arb:zset:close"
    start = (page - 1) * page_size
    stop = start + page_size - 1

    members = await redis.zrevrange(zset_key, start, stop, withscores=True)

    total = await redis.zcard(zset_key)

    items = []
    for member, score in members:
        member_str = str(member)
        parts = member_str.split(":")
        if len(parts) >= 3:
            symbol = parts[2]
            spot_exchange = parts[0] if parts[0] else ""
            derivative_exchange = parts[1] if parts[1] else ""
        else:
            symbol = member_str
            spot_exchange = ""
            derivative_exchange = ""

        items.append({
            "symbol": symbol,
            "spot_exchange": spot_exchange,
            "derivative_exchange": derivative_exchange,
            "open_spread_bps": score if sort_by == "open_spread_bps" else 0.0,
            "close_spread_bps": score if sort_by == "close_spread_bps" else 0.0,
            "funding_rate": 0.0,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
