"""GET /api/insights (S2-T05, schema §3): newest first, device optional.
GET /api/insights/current: the same rows collapsed into the current advice.
GET /api/insights/timeline: the same rows bucketed by age over PAST_WINDOWS.
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


def _row_dict(r) -> dict:
    """One insight row as the dict group_actions() and the list route expect."""
    return {
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


def _finalize(actions: list[dict]) -> list[dict]:
    """ISO-format the timestamps group_actions() left as datetimes."""
    for a in actions:
        a["updated_at"] = _iso(a["updated_at"])
        for reason in a["reasons"]:
            reason["created_at"] = _iso(reason["created_at"])
    return actions


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
    actions = _finalize(group_actions(
        [_row_dict(r) for r in rows], settings.insight_max_actions,
    ))
    return {
        "device_id": device,
        "generated_at": _iso(datetime.now(tz=timezone.utc)),
        "hold_s": settings.insight_hold_s,
        "max_actions": settings.insight_max_actions,
        "actions": actions,
    }


@router.get("/api/insights/timeline")
async def insight_timeline(
    request: Request,
    device: str = Query(..., description="device_id — advice is per athlete"),
) -> dict:
    """The advice timeline: /current's live actions plus the stored history,
    bucketed by age over the SAME `PAST_WINDOWS` the historical metrics use.

    This is what makes advice survive a page reload (2026-08-06): insights were
    always persisted (the /api/insights event log), but the panel only read the
    INSIGHT_HOLD_S "currently standing" view, so anything older vanished on
    refresh. Buckets are derived from `settings.past_windows` at request time —
    change PAST_WINDOWS and the timeline follows with no code change:

        live   (0, INSIGHT_HOLD_S]      — identical definition to /current
        "5m"   (INSIGHT_HOLD_S, 5m]
        "30m"  (5m, 30m]
        "2h"   (30m, 2h]                — nothing older is returned

    Each bucket is collapsed by the same group_actions() as /current (so the
    per-time-base cap is INSIGHT_MAX_ACTIONS and `data_quality` stays
    event-log-only), then ordered newest-first WITHIN the bucket — the whole
    response reads chronologically, latest card first. The same action_id may
    legitimately appear in several buckets: a condition that kept firing is a
    story, not a duplicate.
    """
    pool = request.app.state.pool
    settings = request.app.state.settings
    hold = timedelta(seconds=settings.insight_hold_s)
    # Bucket edges: hold, then every configured window longer than hold (a
    # window shorter than the hold would be an empty range — skip it).
    labels = [w.strip() for w in settings.past_windows_raw.split(",") if w.strip()]
    edges: list[tuple[str, timedelta]] = [("live", hold)]
    for label, td in zip(labels, settings.past_windows):
        if td > hold:
            edges.append((label, td))
    span = edges[-1][1]

    rows = await pool.fetch(
        f"""SELECT {_CURRENT_COLUMNS}
            FROM insights
            WHERE device_id = $1 AND created_at > now() - $2::interval
            ORDER BY created_at DESC, insight_id DESC""",
        device, span,
    )
    now = datetime.now(tz=timezone.utc)
    per_bucket: list[list[dict]] = [[] for _ in edges]
    for r in rows:
        age = now - r["created_at"]
        for i, (_, edge) in enumerate(edges):
            if age <= edge:
                per_bucket[i].append(_row_dict(r))
                break

    buckets = []
    for (label, _), bucket_rows in zip(edges, per_bucket):
        actions = group_actions(bucket_rows, settings.insight_max_actions)
        # group_actions ranks by severity for the live view; the timeline is a
        # chronology, so newest-first within the bucket (stable, so equal
        # timestamps keep the severity order as a tie-break).
        actions.sort(key=lambda a: a["updated_at"], reverse=True)
        buckets.append({"window": label, "actions": _finalize(actions)})

    return {
        "device_id": device,
        "generated_at": _iso(now),
        "hold_s": settings.insight_hold_s,
        "max_actions": settings.insight_max_actions,
        "windows": [label for label, _ in edges],
        "buckets": buckets,
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
        {**_row_dict(r), "device_id": r["device_id"],
         "created_at": _iso(r["created_at"])}
        for r in rows
    ]
