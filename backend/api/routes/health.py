"""Full /api/health (S2-T06): one URL that answers "is everything OK and if
not where" (TRD §10, schema §3). Plus the unauthenticated /api/health/live.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


async def _db_ok(pool) -> bool:
    try:
        async with asyncio.timeout(2):
            return await pool.fetchval("SELECT 1") == 1
    except Exception:  # noqa: BLE001 — any failure = not ok
        return False


def _job_health(job) -> dict:
    return {
        "last_run": job.last_run.isoformat() if job.last_run else None,
        "runs": job.runs,
        "last_error": job.last_error,
    }


@router.get("/api/health")
async def health(request: Request) -> dict:
    state = request.app.state
    hub = state.hub
    writer = state.writer
    db_ok = await _db_ok(state.pool)
    redis_ok = await hub.redis_ok()
    return {
        "status": "ok" if (db_ok and redis_ok) else "degraded",
        "db": db_ok,
        "redis": redis_ok,
        # per-sensor rate_hz / recv / late_drop / buf_drop / sat_count,
        # per-device tick counters, global crc_fail etc. — ingest's 1s dump
        "ingest": await hub.ingest_stats(),
        # pre-normalisation biomech values per device (biomech SPEC §9.2), kept
        # from stage 1: the provisional reference bounds get calibrated
        # against this once real trial data exists
        "biomech": await hub.biomech_diag(),
        "api": {
            "ws_clients": len(hub.clients),
            "ws_dropped": hub.ws_dropped,
            "db_buffer": writer.db_buffer,
            "db_dropped": writer.db_dropped,
            "rows_written": writer.rows_written,
            "bad_ticks": writer.bad_ticks,
        },
        "jobs": {
            "predict": _job_health(state.predict_job),
            "insights": _job_health(state.insight_job),
        },
    }
