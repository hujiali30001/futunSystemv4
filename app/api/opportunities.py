from fastapi import APIRouter, Depends, Query

from app.api.deps import get_redis

router = APIRouter()


def _compute_annualized_pct(spread_bps: float, funding_rate: float) -> float:
    return (spread_bps / 10000 + funding_rate) * 365 * 100


def _funding_interval_h(fr_pct: float) -> int:
    return 4 if abs(fr_pct) > 0.05 else 8


def _format_volume(quote_volume: float) -> str:
    if quote_volume <= 0:
        return "--"
    if quote_volume >= 1_000_000:
        return f"{quote_volume / 1_000_000:.1f}M"
    if quote_volume >= 1_000:
        return f"{quote_volume / 1_000:.1f}K"
    return f"{quote_volume:.0f}"


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
        fr_display = (
            f"{fr_pct:+.4f}%/h/{_funding_interval_h(fr_pct)}"
            if abs(fr_pct) < 1
            else f"{fr_pct:+.2f}%/h/{_funding_interval_h(fr_pct)}"
        )

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
            "index_spread_pct": 0.0,
            "spot_volume": "",
            "deriv_volume": "",
        })

    rows.sort(key=lambda r: r["sort_value"], reverse=True)

    total = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = rows[start:end]

    ticker_keys = []
    key_to_row_idx: dict[str, int] = {}
    for i, row in enumerate(page_items):
        spot_key = f"md:ticker:{row['spot_exchange']}:{row['full_symbol']}"
        deriv_key = f"md:ticker:{row['derivative_exchange']}:swap:{row['full_symbol']}"
        ticker_keys.append(spot_key)
        key_to_row_idx[spot_key] = i
        ticker_keys.append(deriv_key)
        key_to_row_idx[deriv_key] = i

    if ticker_keys:
        ticker_vals = await redis.mget(ticker_keys)
        spot_last: dict[int, float] = {}
        deriv_last: dict[int, float] = {}
        spot_vol: dict[int, float] = {}
        deriv_vol: dict[int, float] = {}

        for key, val in zip(ticker_keys, ticker_vals):
            row_idx = key_to_row_idx.get(key)
            if row_idx is None or val is None:
                continue
            parts = str(val).split("|")
            if len(parts) < 2:
                continue
            try:
                volume = float(parts[0])
                last_price = float(parts[1])
            except (ValueError, TypeError):
                continue

            is_spot = ":swap:" not in key
            if is_spot:
                spot_last[row_idx] = last_price
                spot_vol[row_idx] = volume
            else:
                deriv_last[row_idx] = last_price
                deriv_vol[row_idx] = volume

        for i, row in enumerate(page_items):
            sl = spot_last.get(i, 0)
            dl = deriv_last.get(i, 0)
            if sl > 0 and dl > 0:
                row["index_spread_pct"] = round((dl - sl) / sl * 100, 3)
            row["spot_volume"] = _format_volume(spot_vol.get(i, 0))
            row["deriv_volume"] = _format_volume(deriv_vol.get(i, 0))

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
