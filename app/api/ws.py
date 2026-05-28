import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.api.deps import get_redis
from app.api.opportunities import _format_volume, _funding_interval_h

router = APIRouter()


@router.websocket("/leaderboard")
async def leaderboard_ws(
    ws: WebSocket,
    direction: str = "spot_futures",
):
    await ws.accept()
    redis = await get_redis()

    async def push_snapshot():
        raw = await redis.xrevrange("stream:opportunities", "+", "-", count=12000)
        latest: dict = {}
        for msg_id, fields in raw:
            symbol = fields.get("symbol", "")
            spot = fields.get("spot_exchange", "")
            deriv = fields.get("derivative_exchange", "")
            otype = fields.get("opportunity_type", "OPEN")
            fdir = fields.get("direction", "spot_futures")
            key = (symbol, spot, deriv, otype, fdir)
            if key not in latest:
                try:
                    latest[key] = {
                        "symbol": symbol,
                        "spot_exchange": spot,
                        "derivative_exchange": deriv,
                        "open_spread_bps": float(fields.get("open_spread_bps", 0)),
                        "close_spread_bps": float(fields.get("close_spread_bps", 0)),
                        "funding_rate": float(fields.get("funding_rate", 0)),
                        "direction": fdir,
                    }
                except (ValueError, TypeError):
                    pass

        paired: dict = {}
        for (symbol, spot, deriv, otype, fdir), entry in latest.items():
            pk = (symbol, spot, deriv, fdir)
            if pk not in paired:
                paired[pk] = {}
            paired[pk][otype] = entry

        rows = []
        for (symbol, spot, deriv, fdir), entry_by_type in paired.items():
            if fdir != direction:
                continue
            open_entry = entry_by_type.get("OPEN")
            close_entry = entry_by_type.get("CLOSE", open_entry)
            if open_entry is None:
                continue
            open_bps = open_entry["open_spread_bps"]
            close_bps = close_entry["close_spread_bps"]
            open_pct = round(open_bps / 100, 2)
            close_pct = round(close_bps / 100, 2)
            if abs(open_pct) >= 500:
                continue

            fr = open_entry["funding_rate"]
            fr_pct = fr * 100
            fr_display = (
                f"{fr_pct:+.4f}%/h/{_funding_interval_h(fr_pct)}"
                if abs(fr_pct) < 1
                else f"{fr_pct:+.2f}%/h/{_funding_interval_h(fr_pct)}"
            )
            fr_label = "收" if fr > 0 else ("付" if fr < 0 else "")

            rows.append({
                "symbol": symbol.replace("/USDT", ""),
                "full_symbol": symbol,
                "spot_exchange": spot,
                "derivative_exchange": deriv,
                "open_spread_pct": open_pct,
                "close_spread_pct": close_pct,
                "funding_rate_display": f"{fr_display} {fr_label}" if fr_label else fr_display,
                "sort_value": round(open_pct, 2),
            })

        rows.sort(key=lambda r: r["sort_value"], reverse=True)
        top100 = rows[:100]

        keys = []
        for r in top100:
            if direction == "futures_spot":
                keys.append(f"md:ticker:{r['spot_exchange']}:swap:{r['full_symbol']}")
                keys.append(f"md:ticker:{r['derivative_exchange']}:{r['full_symbol']}")
            else:
                keys.append(f"md:ticker:{r['spot_exchange']}:{r['full_symbol']}")
                keys.append(f"md:ticker:{r['derivative_exchange']}:swap:{r['full_symbol']}")
        vals = await redis.mget(keys) if keys else []

        spot_prices: dict = {}
        deriv_prices: dict = {}
        spot_vols: dict = {}
        deriv_vols: dict = {}
        for i, r in enumerate(top100):
            sv = vals[i * 2] if i * 2 < len(vals) else None
            dv = vals[i * 2 + 1] if i * 2 + 1 < len(vals) else None
            pairs = [(dv, True), (sv, False)] if direction == "futures_spot" else [(sv, True), (dv, False)]
            for val, is_spot in pairs:
                if val is None:
                    continue
                parts = str(val).split("|")
                if len(parts) < 2:
                    continue
                try:
                    vol = float(parts[0])
                    price = float(parts[1])
                except (ValueError, TypeError):
                    continue
                pk = (r["full_symbol"], r["spot_exchange"], r["derivative_exchange"])
                if is_spot:
                    spot_prices[pk] = f"{price:.5f}"
                    spot_vols[pk] = _format_volume(vol)
                else:
                    deriv_prices[pk] = f"{price:.5f}"
                    deriv_vols[pk] = _format_volume(vol)

        for r in top100:
            pk = (r["full_symbol"], r["spot_exchange"], r["derivative_exchange"])
            r["spot_price"] = spot_prices.get(pk, "")
            r["deriv_price"] = deriv_prices.get(pk, "")
            r["spot_volume"] = spot_vols.get(pk, "--")
            r["deriv_volume"] = deriv_vols.get(pk, "--")

        await ws.send_json({"type": "snapshot", "items": top100})

    try:
        await push_snapshot()
        while True:
            await asyncio.sleep(5)
            await push_snapshot()
    except (WebSocketDisconnect, RuntimeError):
        pass
