"""S3-T10 — GET /api/metrics/history: bucket math, null gaps, window validation.

Needs the debug-profile Postgres on 127.0.0.1:5432 (skips otherwise), same as
the other db-backed route tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.routes.metrics import router as metrics_router
from db_utils import connect_admin, create_scratch_db, drop_scratch_db
from migrations.migrate import dsn

COLS = ("time", "device_id", "m1", "m2", "m3", "m4", "m5", "composite", "quality")


@pytest.fixture()
async def history_app():
    admin = await connect_admin()
    name, settings, conn = await create_scratch_db(admin)
    settings = settings.model_copy(update={"past_windows_raw": "2m,10m,30m"})
    pool = await asyncpg.create_pool(dsn(settings), min_size=0, max_size=3)

    app = FastAPI()
    app.include_router(metrics_router)
    app.state.pool = pool
    app.state.settings = settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            yield client, conn
        finally:
            await pool.close()
            await conn.close()
            await drop_scratch_db(admin, name)
            await admin.close()


async def test_bad_window_400(history_app) -> None:
    client, conn = history_app
    await conn.execute("INSERT INTO devices VALUES ('30','30')")
    resp = await client.get("/api/metrics/history",
                            params={"device": "30", "window": "7m"})
    assert resp.status_code == 400
    assert "PAST_WINDOWS" in resp.json()["detail"]


async def test_unknown_device_404(history_app) -> None:
    client, _ = history_app
    resp = await client.get("/api/metrics/history",
                            params={"device": "99", "window": "2m"})
    assert resp.status_code == 404


async def test_raw_path_buckets_and_gaps(history_app) -> None:
    """2m window reads the raw table. Rows are placed with >=10s margin from
    every bucket boundary so the ~ms skew between seeding time and the route's
    own now() cannot move them across buckets."""
    client, conn = history_app
    await conn.execute("INSERT INTO devices VALUES ('30','30')")
    now = datetime.now(tz=timezone.utc)
    rows = []
    # bucket 0 of 2 (span 60s): [now-120, now-60) -> composite 50
    for i in range(20):
        rows.append((now - timedelta(seconds=110 - 2 * i), "30",
                     20.0, None, None, None, None, 50.0, 1.0))
    # bucket 1: [now-60, now) -> composite 70
    for i in range(20):
        rows.append((now - timedelta(seconds=50 - 1.5 * i), "30",
                     10.0, None, None, None, None, 70.0, 0.5))
    await conn.copy_records_to_table("metrics", records=rows, columns=COLS)

    resp = await client.get("/api/metrics/history",
                            params={"device": "30", "window": "2m", "buckets": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["window"] == "2m"
    assert body["bucket_s"] == 60
    assert len(body["buckets"]) == 2
    b0, b1 = body["buckets"]
    assert b0["composite"]["avg"] == pytest.approx(50.0)
    assert b0["m"][0] == pytest.approx(20.0)
    assert b0["m"][3] is None                       # m4 all-NULL -> null
    assert b0["quality"] == pytest.approx(1.0)
    assert b1["composite"]["avg"] == pytest.approx(70.0)
    assert b1["quality"] == pytest.approx(0.5)

    # a window with no data at all: every bucket is null (gap, never 0)
    await conn.execute("INSERT INTO devices VALUES ('31','31')")
    resp = await client.get("/api/metrics/history",
                            params={"device": "31", "window": "2m", "buckets": 4})
    assert resp.json()["buckets"] == [None, None, None, None]


async def test_agg_path_clamps_span_to_1m(history_app) -> None:
    """10m window reads metrics_1m; requesting 96 buckets must clamp to 10
    one-minute buckets rather than scattering 1m rows into sub-minute buckets."""
    client, conn = history_app
    await conn.execute("INSERT INTO devices VALUES ('30','30')")
    m_now = (await conn.fetchrow("SELECT date_trunc('minute', now()) AS m"))["m"]
    rows = []
    for minute, comp in {4: 40.0, 3: 40.0, 2: 80.0, 1: 80.0}.items():
        for i in range(10):
            t = m_now - timedelta(minutes=minute) + timedelta(seconds=6 * i)
            rows.append((t, "30", None, None, None, None, None, comp, 1.0))
    await conn.copy_records_to_table("metrics", records=rows, columns=COLS)
    await conn.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")

    resp = await client.get("/api/metrics/history",
                            params={"device": "30", "window": "10m", "buckets": 96})
    assert resp.status_code == 200
    body = resp.json()
    assert body["bucket_s"] == 60
    assert len(body["buckets"]) == 10
    non_null = [b for b in body["buckets"] if b is not None]
    assert len(non_null) == 4
    assert [b["composite"]["avg"] for b in non_null] == [
        pytest.approx(40.0), pytest.approx(40.0),
        pytest.approx(80.0), pytest.approx(80.0),
    ]
    # chronological: t strictly increasing across non-null buckets
    ts = [b["t"] for b in non_null]
    assert ts == sorted(ts)
