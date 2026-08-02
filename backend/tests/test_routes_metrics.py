"""S2-T03 — query layer + REST tests against a scratch TimescaleDB.

The app under test is a bare FastAPI with just the two routers and hand-set
state (pool / stub redis / settings) — no hub, no writer, no lifespan.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.routes.devices import router as devices_router
from api.routes.metrics import router as metrics_router
from db_utils import connect_admin, create_scratch_db, drop_scratch_db
from migrations.migrate import dsn


class StubRedis:
    """Just enough of redis.asyncio for queries.py (bytes, like the real one)."""

    def __init__(self, kv: dict[str, str] | None = None,
                 stats: dict[str, str] | None = None) -> None:
        self.kv = kv or {}
        self.stats = stats or {}

    async def get(self, key: str):
        v = self.kv.get(key)
        return v.encode() if v is not None else None

    async def hgetall(self, key: str):
        if key != "ingest:stats":
            return {}
        return {k.encode(): v.encode() for k, v in self.stats.items()}


@pytest.fixture()
async def api_app():
    admin = await connect_admin()
    name, settings, conn = await create_scratch_db(admin)
    # 2m exercises the raw-table path (<5 min), 10m/30m the cagg path
    settings = settings.model_copy(update={"past_windows_raw": "2m,10m,30m"})
    pool = await asyncpg.create_pool(dsn(settings), min_size=0, max_size=3)

    app = FastAPI()
    app.include_router(devices_router)
    app.include_router(metrics_router)
    app.state.pool = pool
    app.state.settings = settings
    app.state.redis = StubRedis()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            yield app, client, conn
        finally:
            await pool.close()
            await conn.close()
            await drop_scratch_db(admin, name)
            await admin.close()


async def _seed(conn: asyncpg.Connection) -> None:
    """Minute-aligned fixture: composite 40 (m1=20) in buckets now-4m/now-3m,
    composite 80 (m1=10) in buckets now-2m/now-1m; 10 rows per minute."""
    await conn.execute(
        "INSERT INTO devices (device_id, display_name) VALUES ('30','30'), ('31','31')"
    )
    m_now = (await conn.fetchrow("SELECT date_trunc('minute', now()) AS m"))["m"]
    rows = []
    for minute, (comp, m1) in {4: (40.0, 20.0), 3: (40.0, 20.0),
                               2: (80.0, 10.0), 1: (80.0, 10.0)}.items():
        for i in range(10):
            t = m_now - timedelta(minutes=minute) + timedelta(seconds=6 * i)
            rows.append((t, "30", m1, None, None, None, None, comp, 1.0))
    await conn.copy_records_to_table(
        "metrics", records=rows,
        columns=("time", "device_id", "m1", "m2", "m3", "m4", "m5",
                 "composite", "quality"),
    )
    await conn.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")


async def test_windows_math_and_trend(api_app) -> None:
    app, client, conn = api_app
    await _seed(conn)

    resp = await client.get("/api/metrics/windows", params={"device": "30"})
    assert resp.status_code == 200
    w = {entry["window"]: entry for entry in resp.json()["windows"]}
    assert list(w) == ["2m", "10m", "30m"]

    # 2m raw window: only 80-composite rows fall inside it
    assert w["2m"]["composite"]["avg"] == pytest.approx(80.0)
    assert w["2m"]["composite"]["min"] == pytest.approx(80.0)
    assert w["2m"]["composite"]["max"] == pytest.approx(80.0)
    assert w["2m"]["m"][0] == pytest.approx(10.0)
    assert w["2m"]["m"][3] is None                 # m4 all-NULL -> null
    assert w["2m"]["quality"] == pytest.approx(1.0)
    assert w["2m"]["trend"] == "up"                # 80 vs ~40 in prior 2m

    # 10m cagg window: 4 buckets 80/80/40/40 -> avg 60, extremes from cagg cols
    assert w["10m"]["composite"]["avg"] == pytest.approx(60.0)
    assert w["10m"]["composite"]["min"] == pytest.approx(40.0)
    assert w["10m"]["composite"]["max"] == pytest.approx(80.0)
    assert w["10m"]["m"][0] == pytest.approx(15.0)
    assert w["10m"]["trend"] == "flat"             # preceding 10m empty

    # hand-run SQL cross-check for the 10m entry. This MUST be the n-weighted
    # form: an unweighted avg(composite) over metrics_1m is the estimator the
    # endpoint used to compute, so cross-checking against it asserted the bug
    # rather than the maths. It happens to agree here (every seeded bucket holds
    # exactly 10 rows, so weighted == unweighted) -- which is precisely why
    # test_windows_weighted_by_bucket_rows below seeds unequal buckets.
    sql_avg = await conn.fetchval(
        """SELECT sum(composite * n) / sum(n) FROM metrics_1m
           WHERE device_id='30' AND bucket >= now() - interval '10 minutes'"""
    )
    assert w["10m"]["composite"]["avg"] == pytest.approx(float(sql_avg))


async def test_windows_weighted_by_bucket_rows(api_app) -> None:
    """A window mean must weight each 1-minute bucket by the number of 60Hz
    samples in it. Unweighted, a short partial bucket counts the same as a full
    one -- measured at 11% error on the 712 real rows in the local database
    (buckets n=300 and n=412: unweighted 13.688 vs weighted 12.348).

    Seeded here: composite 90 over 5 rows, 30 over 45 rows. The correct
    (weighted) mean is (90*5 + 30*45) / 50 = 36.0; the unweighted mean of the
    two per-bucket averages is 60.0. The gap is the bug.
    """
    app, client, conn = api_app
    await conn.execute("INSERT INTO devices (device_id, display_name) VALUES ('30','30')")
    m_now = (await conn.fetchrow("SELECT date_trunc('minute', now()) AS m"))["m"]
    rows = []
    for minute, comp, count in ((6, 90.0, 5), (5, 30.0, 45)):
        base = m_now - timedelta(minutes=minute)
        for i in range(count):
            rows.append((base + timedelta(milliseconds=1000 * i), "30",
                         None, None, None, None, None, comp, 1.0))
    await conn.copy_records_to_table(
        "metrics", records=rows,
        columns=("time", "device_id", "m1", "m2", "m3", "m4", "m5",
                 "composite", "quality"),
    )
    await conn.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")

    resp = await client.get("/api/metrics/windows", params={"device": "30"})
    w = {e["window"]: e for e in resp.json()["windows"]}
    assert w["10m"]["composite"]["avg"] == pytest.approx(36.0)
    assert w["10m"]["composite"]["avg"] != pytest.approx(60.0)
    # extremes are decomposable and stay exact
    assert w["10m"]["composite"]["min"] == pytest.approx(30.0)
    assert w["10m"]["composite"]["max"] == pytest.approx(90.0)


async def test_recent_shape(api_app) -> None:
    app, client, conn = api_app
    await _seed(conn)
    resp = await client.get("/api/metrics/recent",
                            params={"device": "30", "seconds": 600})
    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == "30"
    assert len(body["rows"]) == 40
    offsets = [r[0] for r in body["rows"]]
    assert offsets[0] == 0 and offsets == sorted(offsets)
    first = body["rows"][0]
    assert len(first) == 8                    # t_offset_ms, m1..m5, c, q
    assert first[4] is None and first[5] is None    # m4, m5 null
    # empty result for a registered device with no data
    resp = await client.get("/api/metrics/recent",
                            params={"device": "31", "seconds": 30})
    assert resp.json() == {"device_id": "31", "t0": None, "rows": []}


async def test_recent_unknown_device_404(api_app) -> None:
    _, client, _ = api_app
    for path in ("/api/metrics/recent", "/api/metrics/windows"):
        resp = await client.get(path, params={"device": "99"})
        assert resp.status_code == 404


async def test_devices_merge_and_rename(api_app) -> None:
    app, client, conn = api_app
    await _seed(conn)
    now_ms = datetime.now(tz=timezone.utc).timestamp() * 1000.0
    app.state.redis = StubRedis(
        kv={
            "last_seen:dev:30": str(now_ms),
            "last_seen:sensor:30:0:1": str(now_ms),
        },
        stats={
            "dev:30:quality": "0.970",
            "sensor:30:0:1:rate_hz": "600.2",
            "sensor:30:0:1:recv": "12345",
        },
    )

    resp = await client.get("/api/devices")
    assert resp.status_code == 200
    devs = {d["device_id"]: d for d in resp.json()}
    assert devs["30"]["online"] is True
    assert devs["30"]["quality"] == pytest.approx(0.97)
    assert devs["30"]["sensors"] == [{
        "source_id": 0, "sensor_id": 1, "limb": "left_shin",
        "rate_hz": pytest.approx(600.2),
        "last_seen": devs["30"]["sensors"][0]["last_seen"],
    }]
    assert devs["31"]["online"] is False and devs["31"]["last_seen"] is None

    # rename round-trip
    resp = await client.patch("/api/devices/30", json={"display_name": "Alice"})
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Alice"
    assert (await client.get("/api/devices")).json()[0]["display_name"] == "Alice"
    # persisted in the registry
    assert await conn.fetchval(
        "SELECT display_name FROM devices WHERE device_id='30'"
    ) == "Alice"

    # validation + unknown device
    assert (await client.patch("/api/devices/30", json={"display_name": "  "})).status_code == 422
    assert (await client.patch("/api/devices/30", json={"display_name": "x" * 65})).status_code == 422
    assert (await client.patch("/api/devices/99", json={"display_name": "ok"})).status_code == 404
