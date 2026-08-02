"""Minimal api service (S1-T04 placeholder) — grows WS fan-out + /debug in S1-T12."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="MVP Dashboard API")


@app.get("/api/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}
