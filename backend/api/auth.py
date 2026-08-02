"""Auth core (S3-T05): bcrypt password hashing, HS256 JWT session cookies,
and a naive in-memory login rate limit.

The JWT lives in an httpOnly/Secure/SameSite=Lax cookie named `session` — the
browser sends it automatically on same-origin REST and the WS upgrade, and JS
can never read it (APPFLOW §3). `bcrypt` is used directly rather than through
passlib: passlib is unmaintained and breaks against bcrypt >= 4.1.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from common.config import Settings

COOKIE_NAME = "session"
JWT_ALGORITHM = "HS256"

# Naive in-memory rate limit (task spec): N failed logins per window per IP
# -> 429. Per-process state is acceptable for a single-instance MVP api.
RATE_LIMIT_FAILS = 5
RATE_LIMIT_WINDOW_S = 60.0


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:  # malformed hash in the table
        return False


def mint_token(settings: Settings, username: str, role: str) -> tuple[str, datetime]:
    """-> (token, expiry). Raises RuntimeError if JWT_SECRET is unset —
    an unsigned session cookie must be impossible, not silently weak."""
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is not set — required from stage 3 (TRD §7)")
    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    token = jwt.encode(
        {"sub": username, "role": role, "exp": expires_at},
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    return token, expires_at


def decode_token(settings: Settings, token: str) -> dict | None:
    """-> {'sub','role','exp'} or None on any validation failure (missing
    secret, bad signature, expired, malformed)."""
    if not settings.jwt_secret or not token:
        return None
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


@dataclass
class LoginRateLimiter:
    """Sliding-window failure counter per client IP."""

    max_fails: int = RATE_LIMIT_FAILS
    window_s: float = RATE_LIMIT_WINDOW_S
    _fails: dict[str, deque[float]] = field(default_factory=dict)

    def _prune(self, ip: str, now: float) -> deque[float]:
        q = self._fails.setdefault(ip, deque())
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def blocked(self, ip: str) -> bool:
        return len(self._prune(ip, time.monotonic())) >= self.max_fails

    def record_failure(self, ip: str) -> None:
        self._prune(ip, time.monotonic()).append(time.monotonic())

    def reset(self, ip: str) -> None:
        self._fails.pop(ip, None)
