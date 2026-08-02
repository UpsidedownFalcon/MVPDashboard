"""GET /api/forecasts/latest (S2-T04, schema §3): newest made_at group."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from api.queries import _iso, device_exists
from common.durations import format_duration

router = APIRouter()


@router.get("/api/forecasts/latest")
async def forecasts_latest(request: Request, device: str = Query(...)) -> dict:
    pool = request.app.state.pool
    if not await device_exists(pool, device):
        raise HTTPException(status_code=404, detail="unknown device")
    rows = await pool.fetch(
        """SELECT made_at, horizon, target_time, composite_pred, ci_low, ci_high,
                  model_version
           FROM forecasts
           WHERE device_id = $1
             AND made_at = (SELECT max(made_at) FROM forecasts WHERE device_id = $1)
           ORDER BY horizon""",
        device,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="no forecast yet")
    return {
        "made_at": _iso(rows[0]["made_at"]),
        "model_version": rows[0]["model_version"],
        "points": [
            {
                "horizon": format_duration(r["horizon"]),
                "target_time": _iso(r["target_time"]),
                "pred": float(r["composite_pred"]),
                "ci_low": None if r["ci_low"] is None else float(r["ci_low"]),
                "ci_high": None if r["ci_high"] is None else float(r["ci_high"]),
            }
            for r in rows
        ],
    }
