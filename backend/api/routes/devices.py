"""GET /api/devices + PATCH /api/devices/{id} (S2-T03, schema §3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from api import queries

router = APIRouter()


class RenameBody(BaseModel):
    display_name: str

    @field_validator("display_name")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("display_name must be non-empty")
        if len(value) > 64:
            raise ValueError("display_name must be at most 64 characters")
        return value


@router.get("/api/devices")
async def list_devices(request: Request) -> list[dict]:
    state = request.app.state
    return await queries.devices(state.pool, state.redis, state.settings)


@router.patch("/api/devices/{device_id}")
async def rename_device(device_id: str, body: RenameBody, request: Request) -> dict:
    state = request.app.state
    updated = await state.pool.fetchrow(
        "UPDATE devices SET display_name=$2 WHERE device_id=$1 RETURNING device_id",
        device_id, body.display_name,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="unknown device")
    device = await queries.device_one(state.pool, state.redis, state.settings, device_id)
    assert device is not None
    return device
