"""api service: WS fan-out + /debug viewer + health (S1-T12), DB writer +
device auto-registration (S2-T02), REST + jobs (S2-T03..06), auth on every
route and the WS (S3-T05 — only POST /api/auth/login and GET /api/health/live
stay open, BACKEND_SCHEMA §3).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import Depends, FastAPI, WebSocket
from fastapi.responses import FileResponse

from common.config import get_settings
from migrations.migrate import dsn
from api.deps import WS_CLOSE_UNAUTHORIZED, require_user, ws_user
from api.jobs.insights import InsightJob
from api.jobs.predict import PredictJob
from api.routes.auth import router as auth_router
from api.routes.devices import router as devices_router
from api.routes.forecasts import router as forecasts_router
from api.routes.health import router as health_router
from api.routes.insights import router as insights_router
from api.routes.metrics import router as metrics_router
from api.writer import Writer
from api.ws import Hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

DEBUG_HTML = Path(__file__).resolve().parent / "debug.html"


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        if not settings.jwt_secret:
            # Fail fast and loud: from stage 3 every route is cookie-authed and
            # an empty secret would otherwise surface as a confusing 401-on-
            # everything (or worse, tokens nobody can validate).
            raise RuntimeError("JWT_SECRET is not set — required from stage 3 (TRD §7)")
        hub = Hub(settings)
        await hub.start()
        app.state.hub = hub
        # min_size=0: the api must come up (and stream live data) even if the
        # db is down — the writer just buffers/drops and retries.
        pool = await asyncpg.create_pool(dsn(settings), min_size=0, max_size=5)
        writer = Writer(settings, pool)
        hub.tick_listeners.append(writer.on_tick)
        await writer.start()
        predict_job = PredictJob(settings, pool)
        await predict_job.start()
        insight_job = InsightJob(settings, pool)
        await insight_job.start()
        app.state.pool = pool
        app.state.writer = writer
        app.state.predict_job = predict_job
        app.state.insight_job = insight_job
        app.state.settings = settings
        app.state.redis = hub.redis
        log.info("api up (ws fan-out + /debug + db writer + rest + jobs)")
        yield
        await insight_job.stop()
        await predict_job.stop()
        await writer.stop()
        await pool.close()
        await hub.stop()

    app = FastAPI(title="MVP Dashboard API", lifespan=lifespan)
    # auth router: login/logout open, /me guards itself
    app.include_router(auth_router)
    guard = [Depends(require_user)]
    app.include_router(devices_router, dependencies=guard)
    app.include_router(metrics_router, dependencies=guard)
    app.include_router(forecasts_router, dependencies=guard)
    app.include_router(insights_router, dependencies=guard)
    # health router guards /api/health itself; /api/health/live stays open
    app.include_router(health_router)

    # /debug shows live data, so it sits behind auth too (S3-T05 step 4)
    @app.get("/debug", dependencies=guard)
    async def debug_page() -> FileResponse:
        return FileResponse(DEBUG_HTML, media_type="text/html")

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        user = await ws_user(ws)
        if user is None:
            # accept-then-close: a refused upgrade carries no close code, and
            # the frontend keys its login redirect off 4401 (APPFLOW §3)
            await ws.accept()
            await ws.close(code=WS_CLOSE_UNAUTHORIZED)
            return
        hub: Hub = app.state.hub
        await hub.handle_client(ws, expires_at=user.expires_at)

    return app


app = create_app()
