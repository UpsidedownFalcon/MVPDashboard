"""Idempotent user seeding from SEED_USERS (S3-T05).

Parses `SEED_USERS="alice:pw1,bob:pw2"` and bcrypt-upserts each account. Run by
the api entrypoint after migrations (compose command) — re-running with the
same env is a no-op apart from re-hashing; user sets real accounts in the prod
`.env`. Also runnable manually: `uv run python -m api.seed_users`.
"""

from __future__ import annotations

import asyncio
import logging

from api.auth import hash_password
from common.config import get_settings
from migrations.migrate import connect

log = logging.getLogger("seed_users")


def parse_seed_users(raw: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        username, sep, password = entry.partition(":")
        if not sep or not username.strip() or not password:
            raise ValueError(f"SEED_USERS entry not 'user:pass': {entry!r}")
        pairs.append((username.strip(), password))
    return pairs


async def seed(conn, raw: str) -> int:
    users = parse_seed_users(raw)
    for username, password in users:
        await conn.execute(
            """INSERT INTO users (username, password_hash)
               VALUES ($1, $2)
               ON CONFLICT (username) DO UPDATE SET password_hash = $2""",
            username, hash_password(password),
        )
    return len(users)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if not settings.seed_users.strip():
        log.warning("SEED_USERS empty — no accounts seeded, nobody can log in")
        return
    conn = await connect(settings)
    try:
        count = await seed(conn, settings.seed_users)
    finally:
        await conn.close()
    log.info("seeded %d user(s)", count)


if __name__ == "__main__":
    asyncio.run(main())
