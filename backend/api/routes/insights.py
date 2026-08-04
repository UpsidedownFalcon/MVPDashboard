"""GET /api/insights (S2-T05, schema §3): newest first, device optional.
GET /api/insights/current: the same rows collapsed into the current advice.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request

from api.jobs.insights import group_actions
from api.queries import _iso

router = APIRouter()

_CURRENT_COLUMNS = """insight_id, created_at, device_id, severity, rule_id,
                      message, context, action, rationale, action_id, reason"""


@router.get("/api/insights/current")
async def current_insights(
    request: Request,
    device: str = Query(..., description="device_id — advice is per athlete"),
) -> dict:
    """The advice that stands right now: at most INSIGHT_MAX_ACTIONS actions,
    each with every reason currently supporting it.

    This is a STATE view, not the event log at /api/insights. A rule that keeps
    firing appears once here (with a fresh `updated_at`) and once per cooldown
    there. `INSIGHT_HOLD_S` is the whole definition of "currently": a reason
    stays until the rule behind it has been silent that long, which is what
    stops the panel flickering as a condition hovers around its threshold.
    """
    pool = request.app.state.pool
    settings = request.app.state.settings
    rows = await pool.fetch(
        f"""SELECT {_CURRENT_COLUMNS}
            FROM insights
            WHERE device_id = $1 AND created_at > now() - $2::interval
            ORDER BY created_at DESC, insight_id DESC""",
        device, timedelta(seconds=settings.insight_hold_s),
    )
    actions = group_actions(
        [
            {
                "insight_id": r["insight_id"],
                "created_at": r["created_at"],
                "severity": r["severity"],
                "rule_id": r["rule_id"],
                "message": r["message"],
                "context": json.loads(r["context"]) if r["context"] else None,
                "action": r["action"],
                "rationale": r["rationale"],
                "action_id": r["action_id"],
                "reason": r["reason"],
            }
            for r in rows
        ],
        settings.insight_max_actions,
    )
    for a in actions:
        a["updated_at"] = _iso(a["updated_at"])
        for reason in a["reasons"]:
            reason["created_at"] = _iso(reason["created_at"])
    return {
        "device_id": device,
        "generated_at": _iso(datetime.now(tz=timezone.utc)),
        "hold_s": settings.insight_hold_s,
        "max_actions": settings.insight_max_actions,
        "actions": actions,
    }


@router.get("/api/insights")
async def list_insights(
    request: Request,
    device: str | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
) -> list[dict]:
    pool = request.app.state.pool
    if device is not None:
        rows = await pool.fetch(
            f"""SELECT {_CURRENT_COLUMNS}
                FROM insights WHERE device_id = $1
                ORDER BY created_at DESC, insight_id DESC LIMIT $2""",
            device, limit,
        )
    else:
        rows = await pool.fetch(
            f"""SELECT {_CURRENT_COLUMNS}
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
            "action_id": r["action_id"],
            "reason": r["reason"],
        }
        for r in rows
    ]
