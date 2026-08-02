"""POST /api/auth/login|logout + GET /api/auth/me (S3-T05, schema §3)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from api.auth import COOKIE_NAME, LoginRateLimiter, mint_token, verify_password
from api.deps import User, require_user

log = logging.getLogger("api.auth")

router = APIRouter()

# Module-level: one limiter per api process (single-instance MVP).
_rate_limiter = LoginRateLimiter()


class LoginBody(BaseModel):
    username: str
    password: str


def _client_ip(request: Request) -> str:
    # Caddy proxies same-host; the socket peer is fine for a naive limiter.
    return request.client.host if request.client else "unknown"


@router.post("/api/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    ip = _client_ip(request)
    if _rate_limiter.blocked(ip):
        raise HTTPException(status_code=429, detail="too many attempts — wait a minute")

    settings = request.app.state.settings
    row = await request.app.state.pool.fetchrow(
        "SELECT username, password_hash, role FROM users WHERE username = $1",
        body.username.strip(),
    )
    # Same 401 for unknown user and wrong password (UIUX §2: never say which).
    if row is None or not verify_password(body.password, row["password_hash"]):
        _rate_limiter.record_failure(ip)
        log.info("login failed for %r from %s", body.username, ip)
        raise HTTPException(status_code=401, detail="invalid username or password")

    _rate_limiter.reset(ip)
    token, _ = mint_token(settings, row["username"], row["role"])
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=settings.jwt_expire_hours * 3600,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    log.info("login ok: %s", row["username"])
    return {"username": row["username"], "role": row["role"]}


@router.post("/api/auth/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {}


@router.get("/api/auth/me")
async def me(user: User = Depends(require_user)) -> dict:
    return {"username": user.username, "role": user.role}
