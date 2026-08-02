"""FastAPI auth dependencies (S3-T05).

`require_user` guards every REST route except POST /api/auth/login and
GET /api/health/live (BACKEND_SCHEMA §3); the WS handshake validates the same
cookie via `ws_user` and closes 4401 when it is missing/expired.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, WebSocket

from api.auth import COOKIE_NAME, decode_token

WS_CLOSE_UNAUTHORIZED = 4401


@dataclass(frozen=True)
class User:
    username: str
    role: str
    expires_at: float  # unix seconds (WS uses it for the mid-connection check)


def _user_from_cookie(settings, token: str | None) -> User | None:
    claims = decode_token(settings, token or "")
    if claims is None:
        return None
    return User(
        username=str(claims.get("sub", "")),
        role=str(claims.get("role", "trainer")),
        expires_at=float(claims["exp"]),
    )


async def require_user(request: Request) -> User:
    user = _user_from_cookie(
        request.app.state.settings, request.cookies.get(COOKIE_NAME)
    )
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


async def ws_user(ws: WebSocket) -> User | None:
    """Cookie check for the WS handshake. Returns None when unauthenticated —
    the caller accepts then closes with 4401 (a rejected upgrade carries no
    close code, and the frontend keys its login redirect off 4401)."""
    return _user_from_cookie(ws.app.state.settings, ws.cookies.get(COOKIE_NAME))
