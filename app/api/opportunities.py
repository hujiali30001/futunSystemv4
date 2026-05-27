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
        parts = member.split("|") if "|" in member else [member, "", ""]
        symbol = parts[0] if parts[0] else "UNKNOWN"
        spot_exchange = parts[1] if len(parts) > 1 else ""
        derivative_exchange = parts[2] if len(parts) > 2 else ""
        spread_bps = score

        funding_key = f"md:funding:{spot_exchange}:{symbol}" if spot_exchange else ""
        funding_rate = 0.0
        if funding_key:
            val = await redis.get(funding_key)
            if val is not None:
                try:
                    funding_rate = float(val)
                except (ValueError, TypeError):
                    pass

        items.append({
            "symbol": symbol,
            "spot_exchange": spot_exchange,
            "derivative_exchange": derivative_exchange,
            "open_spread_bps": spread_bps if sort_by == "open_spread_bps" else 0.0,
            "close_spread_bps": spread_bps if sort_by == "close_spread_bps" else 0.0,
            "funding_rate": funding_rate,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
