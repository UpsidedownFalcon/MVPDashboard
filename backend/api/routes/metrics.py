"""GET /api/metrics/recent + /api/metrics/windows (S2-T03, schema §3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from api import queries

router = APIRouter()


async def _require_device(request: Request, device: str) -> None:
    if not await queries.device_exists(request.app.state.pool, device):
        raise HTTPException(status_code=404, detail="unknown device")


@router.get("/api/metrics/recent")
async def metrics_recent(
    request: Request,
    device: str = Query(...),
    seconds: int = Query(30, ge=1, le=3600),
) -> dict:
    await _require_device(request, device)
    return await queries.recent(request.app.state.pool, device, seconds)


@router.get("/api/metrics/windows")
async def metrics_windows(request: Request, device: str = Query(...)) -> dict:
    await _require_device(request, device)
    return await queries.windows(
        request.app.state.pool, request.app.state.settings, device
    )
