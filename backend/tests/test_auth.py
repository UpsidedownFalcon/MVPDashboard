"""S3-T05 — auth: happy path, wrong password, expired token, cookie-less WS
rejected (4401), rate limit, guarded routes.

Needs the debug-profile Postgres on 127.0.0.1:5432 (skips otherwise). The app
under test mirrors main.py's wiring: auth router open, data routers behind
`require_user`, /api/health/live open, WS validated via `ws_user`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI, WebSocket
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

import api.routes.auth as auth_routes
from api.auth import JWT_ALGORITHM, LoginRateLimiter, mint_token
from api.deps import WS_CLOSE_UNAUTHORIZED, require_user, ws_user
from api.routes.auth import router as auth_router
from api.routes.devices import router as devices_router
from api.routes.health import router as health_router
from api.seed_users import seed
from db_utils import connect_admin, create_scratch_db, drop_scratch_db
from migrations.migrate import dsn
from test_routes_metrics import StubRedis

SECRET = "test-secret-key"


def _build_app(settings, pool) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    guard = [Depends(require_user)]
    app.include_router(devices_router, dependencies=guard)
    app.include_router(health_router)
    app.state.settings = settings
    app.state.pool = pool
    app.state.redis = StubRedis()

    # same accept-then-close-4401 contract as api.main.ws_live
    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        user = await ws_user(ws)
        if user is None:
            await ws.accept()
            await ws.close(code=WS_CLOSE_UNAUTHORIZED)
            return
        await ws.accept()
        await ws.send_json({"hello": user.username})
        await ws.close()

    return app


@pytest.fixture()
async def auth_app():
    admin = await connect_admin()
    name, settings, conn = await create_scratch_db(admin)
    settings = settings.model_copy(update={"jwt_secret": SECRET})
    pool = await asyncpg.create_pool(dsn(settings), min_size=0, max_size=3)
    await seed(conn, "alice:pw1,bob:pw2")
    auth_routes._rate_limiter = LoginRateLimiter()   # isolate tests

    app = _build_app(settings, pool)
    transport = ASGITransport(app=app)
    # https base_url: the cookie is Secure, and httpx's jar (correctly)
    # refuses to send Secure cookies over plain http
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        try:
            yield app, client, conn, settings
        finally:
            await pool.close()
            await conn.close()
            await drop_scratch_db(admin, name)
            await admin.close()


async def test_login_happy_path_and_logout(auth_app) -> None:
    app, client, conn, _ = auth_app
    resp = await client.post("/api/auth/login",
                             json={"username": "alice", "password": "pw1"})
    assert resp.status_code == 200
    assert resp.json() == {"username": "alice", "role": "trainer"}
    cookie = resp.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax")

    assert (await client.get("/api/auth/me")).json()["username"] == "alice"
    assert (await client.get("/api/devices")).status_code == 200

    await client.post("/api/auth/logout")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_wrong_password_and_unknown_user_401(auth_app) -> None:
    _, client, _, _ = auth_app
    for body in ({"username": "alice", "password": "wrong"},
                 {"username": "nobody", "password": "pw1"}):
        resp = await client.post("/api/auth/login", json=body)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "invalid username or password"


async def test_guarded_routes_401_without_cookie(auth_app) -> None:
    _, client, _, _ = auth_app
    assert (await client.get("/api/devices")).status_code == 401
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/health")).status_code == 401
    # liveness stays open
    assert (await client.get("/api/health/live")).status_code == 200


async def test_expired_token_401(auth_app) -> None:
    _, client, _, settings = auth_app
    expired = pyjwt.encode(
        {"sub": "alice", "role": "trainer",
         "exp": datetime.now(tz=timezone.utc) - timedelta(hours=1)},
        SECRET, algorithm=JWT_ALGORITHM,
    )
    client.cookies.set("session", expired)
    assert (await client.get("/api/auth/me")).status_code == 401
    # tampered signature is equally dead
    token, _ = mint_token(settings, "alice", "trainer")
    client.cookies.set("session", token[:-2] + "xx")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_rate_limit_429(auth_app) -> None:
    _, client, _, _ = auth_app
    for _ in range(5):
        resp = await client.post("/api/auth/login",
                                 json={"username": "alice", "password": "wrong"})
        assert resp.status_code == 401
    # 6th attempt is blocked even with CORRECT credentials
    resp = await client.post("/api/auth/login",
                             json={"username": "alice", "password": "pw1"})
    assert resp.status_code == 429


async def test_ws_cookie_rules(auth_app) -> None:
    app, _, _, settings = auth_app
    tc = TestClient(app)
    # no cookie -> accepted then closed 4401
    with tc.websocket_connect("/ws/live") as ws:
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == WS_CLOSE_UNAUTHORIZED
    # valid cookie -> stream opens
    token, _ = mint_token(settings, "alice", "trainer")
    with tc.websocket_connect("/ws/live",
                              headers={"cookie": f"session={token}"}) as ws:
        assert ws.receive_json() == {"hello": "alice"}


async def test_seed_idempotent(auth_app) -> None:
    _, client, conn, _ = auth_app
    assert await conn.fetchval("SELECT count(*) FROM users") == 2
    await seed(conn, "alice:pw1,bob:pw2")           # re-run: still 2, login works
    assert await conn.fetchval("SELECT count(*) FROM users") == 2
    resp = await client.post("/api/auth/login",
                             json={"username": "bob", "password": "pw2"})
    assert resp.status_code == 200
