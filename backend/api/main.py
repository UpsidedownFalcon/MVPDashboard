"""Minimal api service (S1-T12): WS fan-out + /debug viewer + health.

DB writer, REST and jobs arrive in stage 2; auth in stage 3.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse

from common.config import get_settings
from api.ws import Hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

DEBUG_HTML = Path(__file__).resolve().parent / "debug.html"


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub = Hub(get_settings())
        await hub.start()
        app.state.hub = hub
        log.info("api up (ws fan-out + /debug)")
        yield
        await hub.stop()

    app = FastAPI(title="MVP Dashboard API", lifespan=lifespan)

    @app.get("/api/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health")
    async def health() -> dict:
        hub: Hub = app.state.hub
        redis_ok = await hub.redis_ok()
        return {
            "status": "ok" if redis_ok else "degraded",
            "redis": redis_ok,
            "ingest": await hub.ingest_stats(),
            "api": {
                "ws_clients": len(hub.clients),
                "ws_dropped": hub.ws_dropped,
            },
        }

    @app.get("/debug")
    async def debug_page() -> FileResponse:
        return FileResponse(DEBUG_HTML, media_type="text/html")

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        hub: Hub = app.state.hub
        await hub.handle_client(ws)

    return app


app = create_app()
