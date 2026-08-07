"""GET /api/insights (S2-T05, schema §3): newest first, device optional.
GET /api/insights/current: the same rows collapsed into the current advice.
GET /api/insights/timeline: the same rows bucketed by age over PAST_WINDOWS.
POST /api/insights/decisions: record Adopt/Override on one advice card.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from api.deps import _user_from_cookie
from api.auth import COOKIE_NAME
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

    # Attach the NEWEST Adopt/Override decision per card (decisions are
    # append-only and changeable — migration 004). Matched on the _iso string
    # of (action_id, action_updated_at): _iso truncates to milliseconds, and
    # the stored timestamp round-tripped through the frontend's ISO value, so
    # string equality is exact where raw datetime equality (µs vs ms) is not.
    decisions = await pool.fetch(
        """SELECT DISTINCT ON (action_id, action_updated_at)
                  action_id, action_updated_at, decision, note,
                  decided_by, created_at
           FROM insight_decisions
           WHERE device_id = $1
           ORDER BY action_id, action_updated_at, decision_id DESC""",
        device,
    )
    by_card = {
        (d["action_id"], _iso(d["action_updated_at"])): {
            "decision": d["decision"],
            "note": d["note"],
            "decided_by": d["decided_by"],
            "decided_at": _iso(d["created_at"]),
        }
        for d in decisions
    }
    for bucket in buckets:
        for a in bucket["actions"]:
            a["decision"] = by_card.get((a["action_id"], a["updated_at"]))

    return {
        "device_id": device,
        "generated_at": _iso(now),
        "hold_s": settings.insight_hold_s,
        "max_actions": settings.insight_max_actions,
        "windows": [label for label, _ in edges],
        "buckets": buckets,
    }


class DecisionBody(BaseModel):
    """One Adopt/Override press on one advice card."""
    device_id: str
    action_id: str
    # the card's `updated_at` exactly as the timeline returned it (ISO, ms)
    action_updated_at: datetime
    decision: Literal["adopted", "overridden"]
    # override only: what the trainer is doing instead; blank/absent means
    # "overridden without comment"
    note: str | None = Field(default=None, max_length=500)


@router.post("/api/insights/decisions", status_code=201)
async def record_decision(request: Request, body: DecisionBody) -> dict:
    """Store one Adopt/Override decision (migration 004).

    Append-only: pressing "change" on a decided card simply inserts a newer
    row, and the timeline surfaces the newest one per card — the full history
    stays for later analysis. `note` is kept only for overrides; a blank note
    is stored as NULL so the record reads "overridden", nothing more.
    """
    note = (body.note or "").strip() or None
    if body.decision != "overridden":
        note = None
    # Router-level auth already guarded this request in production; the cookie
    # read here only recovers the username for the audit trail (NULL when the
    # app is mounted without the guard, e.g. in tests).
    user = _user_from_cookie(
        request.app.state.settings, request.cookies.get(COOKIE_NAME)
    )
    row = await request.app.state.pool.fetchrow(
        """INSERT INTO insight_decisions
               (device_id, action_id, action_updated_at, decision, note, decided_by)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING decision_id, created_at""",
        body.device_id, body.action_id, body.action_updated_at,
        body.decision, note, user.username if user else None,
    )
    return {
        "decision_id": row["decision_id"],
        "decided_at": _iso(row["created_at"]),
        "device_id": body.device_id,
        "action_id": body.action_id,
        "action_updated_at": _iso(body.action_updated_at),
        "decision": body.decision,
        "note": note,
        "decided_by": user.username if user else None,
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
