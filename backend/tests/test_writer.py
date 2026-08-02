"""S2-T02 — batched writer tests against a real (scratch) TimescaleDB.

DB outage is injected by pointing the writer at a pool whose host:port is
closed (equivalent to `docker compose stop db` from the writer's perspective:
connect refused on acquire), then swapping the good pool back for recovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from api.writer import Writer
from migrations.migrate import dsn
from db_utils import connect_admin, create_scratch_db, db_settings, drop_scratch_db


def make_tick(dev: str = "30", t: datetime | None = None, c: float = 42.5) -> dict:
    t = t or datetime.now(tz=timezone.utc)
    return {
        "type": "tick",
        "t": t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z",
        "dev": dev,
        "m": [10.0, 20.0, 30.0, None, None],
        "c": c,
        "q": 0.97,
        "f": ["warming_up"],   # undocumented extra key — must be ignored
    }


@pytest.fixture()
async def scratch():
    admin = await connect_admin()
    name, settings, conn = await create_scratch_db(admin)
    pool = await asyncpg.create_pool(dsn(settings), min_size=0, max_size=3)
    try:
        yield settings, conn, pool
    finally:
        await pool.close()
        await conn.close()
        await drop_scratch_db(admin, name)
        await admin.close()


async def test_rows_land_and_devices_autoregister(scratch) -> None:
    settings, conn, pool = scratch
    writer = Writer(settings, pool)
    base = datetime.now(tz=timezone.utc)
    for i in range(120):
        writer.on_tick(make_tick("30", base + timedelta(milliseconds=i)))
    for i in range(60):
        writer.on_tick(make_tick("31", base + timedelta(milliseconds=i)))
    assert writer.db_buffer == 180

    await writer.flush()

    assert writer.rows_written == 180
    assert writer.db_buffer == 0
    assert await conn.fetchval("SELECT count(*) FROM metrics") == 180
    row = await conn.fetchrow("SELECT * FROM metrics WHERE device_id='30' LIMIT 1")
    assert row["m4"] is None and row["composite"] == pytest.approx(42.5)
    devices = {
        r["device_id"]: r["display_name"]
        for r in await conn.fetch("SELECT device_id, display_name FROM devices")
    }
    assert devices == {"30": "30", "31": "31"}   # display_name defaults to id


async def test_malformed_tick_counted_not_raised(scratch) -> None:
    settings, _conn, pool = scratch
    writer = Writer(settings, pool)
    writer.on_tick({"type": "tick", "dev": "30"})          # missing fields
    writer.on_tick({"type": "tick", "t": "nonsense", "dev": "30",
                    "m": [1, 2, 3, 4, 5], "c": 1.0, "q": 1.0})
    assert writer.bad_ticks == 2
    assert writer.db_buffer == 0


async def test_outage_buffers_then_recovers(scratch) -> None:
    settings, conn, pool = scratch
    # "db down": nothing listens on this port; acquire fails on demand
    dead_settings = db_settings(settings.postgres_db)
    dead_pool = await asyncpg.create_pool(
        dsn(dead_settings).replace(":5432/", ":59999/"), min_size=0
    )
    writer = Writer(settings, dead_pool)
    for _ in range(50):
        writer.on_tick(make_tick("30"))

    await writer.flush()          # must not raise
    assert writer.rows_written == 0
    assert writer.db_buffer == 50          # rows kept for retry
    assert writer.db_ok is False

    writer._pool = pool           # "db back up"
    await writer.flush()
    assert writer.rows_written == 50
    assert writer.db_buffer == 0
    assert writer.db_ok is True
    assert await conn.fetchval("SELECT count(*) FROM metrics") == 50
    await dead_pool.close()


async def test_buffer_cap_drops_oldest(scratch) -> None:
    settings, _conn, pool = scratch
    writer = Writer(settings, pool)
    writer._cap = 100
    # µs=0: tick timestamps serialize at ms precision, so keep them comparable
    base = datetime.now(tz=timezone.utc).replace(microsecond=0)
    for i in range(150):
        writer.on_tick(make_tick("30", base + timedelta(milliseconds=i)))
    assert writer.db_buffer == 100
    assert writer.db_dropped == 50
    # oldest dropped: the earliest remaining record is tick #50
    assert writer._buf[0][0] == base + timedelta(milliseconds=50)
