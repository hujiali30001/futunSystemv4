from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from pydantic import BaseModel

from app.api.deps import get_redis, get_current_admin, require_role
from app.admin.control_store import ControlPlaneStore, PlatformSwitchRecord

router = APIRouter()


class SwitchUpdate(BaseModel):
    enabled: bool


@router.get("/switches")
async def list_switches(redis=Depends(get_redis), admin=Depends(get_current_admin)):
    store = ControlPlaneStore(redis)
    items = await store.list_switches()
    return {"switches": [s.__dict__ for s in items]}


@router.put("/switches/{switch_id:path}")
async def put_switch(switch_id: str, body: SwitchUpdate, redis=Depends(get_redis),
                     admin=Depends(require_role("superadmin", "risk_admin"))):
    parts = switch_id.split(":", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="switch_id must be key:scope_type:scope_id")
    store = ControlPlaneStore(redis)
    record = PlatformSwitchRecord(
        switch_key=parts[0], scope_type=parts[1], scope_id=parts[2],
        enabled=body.enabled,
    )
    await store.put_switch(record)
    return {"ok": True}


@router.delete("/switches/{switch_id:path}")
async def delete_switch(switch_id: str, redis=Depends(get_redis),
                        admin=Depends(require_role("superadmin", "risk_admin"))):
    parts = switch_id.split(":", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    store = ControlPlaneStore(redis)
    await store.delete_switch(parts[0], scope_type=parts[1], scope_id=parts[2])
    return {"ok": True}
