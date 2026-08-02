"""api service: WS fan-out + /debug viewer + health (S1-T12), DB writer +
device auto-registration (S2-T02). REST and jobs land in later stage-2 tasks;
auth in stage 3.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse

from common.config import get_settings
from migrations.migrate import dsn
from api.writer import Writer
from api.ws import Hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

DEBUG_HTML = Path(__file__).resolve().parent / "debug.html"


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        hub = Hub(settings)
        await hub.start()
        app.state.hub = hub
        # min_size=0: the api must come up (and stream live data) even if the
        # db is down — the writer just buffers/drops and retries.
        pool = await asyncpg.create_pool(dsn(settings), min_size=0, max_size=5)
        writer = Writer(settings, pool)
        hub.tick_listeners.append(writer.on_tick)
        await writer.start()
        app.state.pool = pool
        app.state.writer = writer
        log.info("api up (ws fan-out + /debug + db writer)")
        yield
        await writer.stop()
        await pool.close()
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
            # Pre-normalisation biomech values per device (biomech SPEC §9.2):
            # the signed transmission ratio R, its locked baseline, signed USI,
            # dose and active flags. This is what the provisional reference
            # bounds in SPEC §4 get calibrated against once real trial data
            # exists, so it is exposed even though nothing consumes it yet.
            "biomech": await hub.biomech_diag(),
            "api": {
                "ws_clients": len(hub.clients),
                "ws_dropped": hub.ws_dropped,
                "db_buffer": app.state.writer.db_buffer,
                "db_dropped": app.state.writer.db_dropped,
                "rows_written": app.state.writer.rows_written,
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
