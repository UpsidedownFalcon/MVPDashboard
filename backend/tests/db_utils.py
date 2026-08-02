"""Shared helpers for stage-2 db-backed tests: scratch database on the
debug-profile port (127.0.0.1:5432), migrated to the full schema.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from common.config import Settings
from migrations.migrate import apply_migrations, dsn

HOST = "127.0.0.1"


def db_settings(db_name: str | None = None) -> Settings:
    base = Settings()   # picks up the repo .env (compose used the same values)
    return Settings(
        postgres_host=HOST,
        postgres_db=db_name or base.postgres_db,
        postgres_user=base.postgres_user,
        postgres_password=base.postgres_password,
    )


async def connect_admin() -> asyncpg.Connection:
    """Connect to the dev db or skip the test if it isn't reachable."""
    try:
        return await asyncio.wait_for(asyncpg.connect(dsn(db_settings())), timeout=3)
    except (OSError, asyncio.TimeoutError, asyncpg.PostgresError):
        pytest.skip("db not reachable on 127.0.0.1:5432 (debug profile down)")


async def create_scratch_db(admin: asyncpg.Connection, migrate: bool = True):
    """-> (name, settings, conn). Caller drops it via drop_scratch_db()."""
    name = f"scratch_{uuid.uuid4().hex[:12]}"
    await admin.execute(f'CREATE DATABASE "{name}"')
    settings = db_settings(name)
    conn = await asyncpg.connect(dsn(settings))
    if migrate:
        await apply_migrations(conn, settings)
    return name, settings, conn


async def drop_scratch_db(admin: asyncpg.Connection, name: str) -> None:
    await admin.execute(f'DROP DATABASE "{name}" (FORCE)')
