from fastapi import APIRouter, Depends, Query

from app.api.deps import get_redis

router = APIRouter()

FUNDING_INTERVALS_PER_YEAR = 365 * 3


def _compute_annualized_pct(spread_bps: float, funding_rate: float) -> float:
    return (spread_bps / 10000 + funding_rate) * 365 * 100


@router.get("/leaderboard")
async def leaderboard(
    direction: str = Query("spot_futures", pattern="^(spot_futures|futures_spot)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    redis=Depends(get_redis),
):
    raw = await redis.xrevrange("stream:opportunities", "+", "-", count=8000)

    latest: dict[tuple[str, str, str, str], dict] = {}
    for msg_id, fields in raw:
        symbol = fields.get("symbol", "")
        spot = fields.get("spot_exchange", "")
        deriv = fields.get("derivative_exchange", "")
        otype = fields.get("opportunity_type", "OPEN")
        key = (symbol, spot, deriv, otype)
        if key not in latest:
            try:
                latest[key] = {
                    "symbol": symbol,
                    "spot_exchange": spot,
                    "derivative_exchange": deriv,
                    "opportunity_type": otype,
                    "open_spread_bps": float(fields.get("open_spread_bps", 0)),
                    "close_spread_bps": float(fields.get("close_spread_bps", 0)),
                    "funding_rate": float(fields.get("funding_rate", 0)),
                }
            except (ValueError, TypeError):
                pass

    paired: dict[tuple[str, str, str], dict] = {}
    for (symbol, spot, deriv, otype), entry in latest.items():
        pk = (symbol, spot, deriv)
        if pk not in paired:
            paired[pk] = {}
        paired[pk][otype] = entry

    rows = []
    for (symbol, spot, deriv), entry_by_type in paired.items():
        open_entry = entry_by_type.get("OPEN")
        close_entry = entry_by_type.get("CLOSE", open_entry)
        if open_entry is None:
            continue

        fr = open_entry["funding_rate"]
        open_bps = open_entry["open_spread_bps"]
        close_bps = (close_entry or open_entry)["close_spread_bps"]

        open_yield_pct = _compute_annualized_pct(open_bps, fr)
        close_yield_pct = _compute_annualized_pct(close_bps, fr)

        base_symbol = symbol.replace("/USDT", "")

        fr_pct = fr * 100
        fr_display = f"{abs(fr_pct):.4f}%/{_funding_interval_h(fr_pct)}h/1.5"

        if direction == "spot_futures":
            sort_val = open_yield_pct
        else:
            sort_val = _compute_annualized_pct(open_bps, -fr)

        rows.append({
            "symbol": base_symbol,
            "full_symbol": symbol,
            "spot_exchange": spot,
            "derivative_exchange": deriv,
            "open_yield_pct": round(open_yield_pct, 2),
            "close_yield_pct": round(close_yield_pct, 2),
            "funding_rate_display": fr_display,
            "funding_rate_raw": fr,
            "sort_value": round(sort_val, 2),
        })

    rows.sort(key=lambda r: r["sort_value"], reverse=True)

    total = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = rows[start:end]

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _funding_interval_h(fr_pct: float) -> int:
    return 4 if abs(fr_pct) > 0.05 else 8
