"""GET /api/insights (S2-T05, schema §3): newest first, device optional."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request

from api.queries import _iso

router = APIRouter()


@router.get("/api/insights")
async def list_insights(
    request: Request,
    device: str | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
) -> list[dict]:
    pool = request.app.state.pool
    if device is not None:
        rows = await pool.fetch(
            """SELECT insight_id, created_at, device_id, severity, rule_id,
                      message, context, action, rationale
               FROM insights WHERE device_id = $1
               ORDER BY created_at DESC, insight_id DESC LIMIT $2""",
            device, limit,
        )
    else:
        rows = await pool.fetch(
            """SELECT insight_id, created_at, device_id, severity, rule_id,
                      message, context, action, rationale
               FROM insights
               ORDER BY created_at DESC, insight_id DESC LIMIT $1""",
            limit,
        )
    return [
        {
            "insight_id": r["insight_id"],
            "created_at": _iso(r["created_at"]),
            "device_id": r["device_id"],
            "severity": r["severity"],
            "rule_id": r["rule_id"],
            "message": r["message"],
            "context": json.loads(r["context"]) if r["context"] else None,
            "action": r["action"],
            "rationale": r["rationale"],
        }
        for r in rows
    ]
