"""WP3 — sleeve units: registration, the Redis mirror, and the four
/api/units endpoints (PLAN_unilateral_devices.md section 6).

Runs against a scratch TimescaleDB on 127.0.0.1:5432 like the other stage-2
tests (db_utils); Redis is stubbed, because what matters here is exactly WHAT
the api writes for ingest, not that a server stored it.
"""

from __future__ import annotations

import asyncpg
import orjson
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api import queries
from api.routes.devices import router as devices_router
from api.routes.units import router as units_router
from api.unit_mirror import UnitMirror, units_in_stats
from common import redis_keys
from common.kinds import UnitConfig
from db_utils import connect_admin, create_scratch_db, drop_scratch_db
from migrations.migrate import dsn
from test_routes_metrics import StubRedis


class MirrorRedis(StubRedis):
    """StubRedis that also records the kwargs of every SET (TTL assertions)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.set_calls: list[tuple[str, dict]] = []

    async def set(self, key: str, value, **kwargs) -> bool:
        self.set_calls.append((key, kwargs))
        return await super().set(key, value, **kwargs)


@pytest.fixture()
async def units_app():
    admin = await connect_admin()
    name, settings, conn = await create_scratch_db(admin)
    pool = await asyncpg.create_pool(dsn(settings), min_size=0, max_size=3)

    app = FastAPI()
    app.include_router(devices_router)
    app.include_router(units_router)
    app.state.pool = pool
    app.state.settings = settings
    app.state.redis = MirrorRedis()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            yield app, client, conn
        finally:
            await pool.close()
            await conn.close()
            await drop_scratch_db(admin, name)
            await admin.close()


# --- helpers ------------------------------------------------------------------

async def _seen(app, *unit_ids: str) -> list[str]:
    """Pretend ingest has these sleeves on the wire, then run one register pass."""
    app.state.redis.stats.update({f"unit:{u}:rig": u for u in unit_ids})
    mirror = UnitMirror(app.state.settings, app.state.pool, app.state.redis)
    return await mirror.register_once()


async def _rig_row(app, device_id: str) -> None:
    """The `devices` row a tick would auto-register (writer.py)."""
    await app.state.pool.execute(
        "INSERT INTO devices (device_id, display_name) VALUES ($1, $1) "
        "ON CONFLICT DO NOTHING",
        device_id,
    )


def _cfg(app, unit_id: str) -> dict:
    raw = app.state.redis.kv[redis_keys.unit_cfg(unit_id)]
    return orjson.loads(raw)


# --- registration + mirror ----------------------------------------------------

def test_units_in_stats_reads_only_the_rig_field() -> None:
    stats = {
        b"unit:u30-0:rig": b"u30-0",
        b"unit:u30-1:rig": b"u30-0",      # a paired member is still a unit
        b"unit:u30-1:soc": b"91",         # other unit fields are not ids
        b"unit:nonsense:rig": b"x",       # not a unit id
        b"dev:30:soc": b"88",
        b"global:bad_sync": b"0",
    }
    assert units_in_stats(stats) == ["u30-0", "u30-1"]


async def test_register_from_stats_mirrors_defaults(units_app) -> None:
    app, client, conn = units_app
    settings = app.state.settings

    assert await _seen(app, "u30-0", "u31-1") == ["u30-0", "u31-1"]

    rows = {r["unit_id"]: r for r in await conn.fetch(
        "SELECT * FROM sleeve_units ORDER BY unit_id")}
    assert set(rows) == {"u30-0", "u31-1"}
    # unpaired, side from the wire source_id (PLAN_msd_management decision H,
    # amending decision G), Settings full-scale (decision B)
    assert rows["u30-0"]["rig_id"] == "u30-0"
    assert rows["u30-0"]["side"] == "left"
    assert rows["u31-1"]["side"] == "right"
    assert rows["u30-0"]["accel_fs_g"] == settings.unilateral_accel_fs_g
    assert rows["u30-0"]["gyro_fs_dps"] == settings.unilateral_gyro_fs_dps
    # the wire identity behind the id
    assert (rows["u31-1"]["wire_device_id"], rows["u31-1"]["wire_source_id"]) == (31, 1)

    # the mirror ingest reads: one JSON doc per unit, NO TTL, plus a publish --
    # and it equals the default ingest already runs, so nothing is reset
    for unit in ("u30-0", "u31-1"):
        assert _cfg(app, unit) == UnitConfig.default(
            unit, settings.unilateral_accel_fs_g, settings.unilateral_gyro_fs_dps
        ).to_json()
    assert app.state.redis.published == [
        (redis_keys.UNIT_CFG_CHANNEL, "u30-0"),
        (redis_keys.UNIT_CFG_CHANNEL, "u31-1"),
    ]
    assert all(kwargs == {} for _, kwargs in app.state.redis.set_calls)

    # seeing the same sleeves again registers nothing and publishes nothing
    app.state.redis.published.clear()
    assert await _seen(app, "u30-0", "u31-1") == []
    assert app.state.redis.published == []


async def test_mirror_all_self_heals_after_redis_restart(units_app) -> None:
    app, _client, _conn = units_app
    await _seen(app, "u30-0", "u30-1")

    app.state.redis.kv.clear()            # redis restarted: the keys had no TTL
    app.state.redis.published.clear()
    mirror = UnitMirror(app.state.settings, app.state.pool, app.state.redis)
    assert await mirror.mirror_once() == 2
    assert set(app.state.redis.kv) == {
        redis_keys.unit_cfg("u30-0"), redis_keys.unit_cfg("u30-1")}
    assert app.state.redis.published == [
        (redis_keys.UNIT_CFG_CHANNEL, "u30-0"),
        (redis_keys.UNIT_CFG_CHANNEL, "u30-1"),
    ]


async def test_register_side_follows_wire_source(units_app) -> None:
    """Decision H: source_id 0 registers as left, 1 as right; the operator can
    still clear it (PATCH side null) because NULL stays allowed."""
    app, client, conn = units_app
    assert await _seen(app, "u5-0", "u5-1") == ["u5-0", "u5-1"]

    sides = {r["unit_id"]: r["side"] for r in await conn.fetch(
        "SELECT unit_id, side FROM sleeve_units ORDER BY unit_id")}
    assert sides == {"u5-0": "left", "u5-1": "right"}
    assert _cfg(app, "u5-0")["side"] == "left"
    assert _cfg(app, "u5-1")["side"] == "right"

    resp = await client.patch("/api/units/u5-0", json={"side": None})
    assert resp.status_code == 200 and resp.json()["side"] is None
    assert _cfg(app, "u5-0")["side"] is None
    assert await conn.fetchval(
        "SELECT side FROM sleeve_units WHERE unit_id='u5-0'") is None


# --- GET /api/units -----------------------------------------------------------

async def test_list_units_merges_live_state(units_app) -> None:
    app, client, _conn = units_app
    await _seen(app, "u30-0")
    await _rig_row(app, "u30-0")
    await app.state.pool.execute(
        "UPDATE devices SET display_name='Ash' WHERE device_id='u30-0'")

    body = (await client.get("/api/units")).json()
    assert [u["unit_id"] for u in body] == ["u30-0"]
    unit = body[0]
    assert unit["rig_id"] == "u30-0" and unit["paired"] is False
    assert unit["rig_display_name"] == "Ash"
    assert unit["side"] == "left"          # wire source 0 (decision H)
    assert unit["online"] is False and unit["last_seen"] is None and unit["soc"] is None

    now_ms = (await app.state.pool.fetchval(
        "SELECT extract(epoch FROM now()) * 1000"))
    app.state.redis.stats.update({
        "unit:u30-0:last_seen": str(now_ms), "unit:u30-0:soc": "77"})
    unit = (await client.get("/api/units")).json()[0]
    assert unit["online"] is True and unit["soc"] == 77
    assert unit["last_seen"] is not None


# --- PATCH /api/units/{id} ----------------------------------------------------

async def test_patch_side_and_full_scale(units_app) -> None:
    app, client, conn = units_app
    await _seen(app, "u30-0")
    app.state.redis.published.clear()

    # "right" is a real change: u30-0 registers as left (decision H), so the
    # operator override is what this exercises
    resp = await client.patch("/api/units/u30-0", json={"side": "right"})
    assert resp.status_code == 200 and resp.json()["side"] == "right"
    assert _cfg(app, "u30-0")["side"] == "right"
    assert app.state.redis.published == [(redis_keys.UNIT_CFG_CHANNEL, "u30-0")]

    # full-scale is per sleeve (decision F) and only the sent fields change
    resp = await client.patch("/api/units/u30-0",
                              json={"accel_fs_g": 8, "gyro_fs_dps": 1000})
    assert resp.status_code == 200
    assert (resp.json()["accel_fs_g"], resp.json()["gyro_fs_dps"]) == (8, 1000)
    assert resp.json()["side"] == "right"
    assert _cfg(app, "u30-0") == {"unit": "u30-0", "rig": "u30-0", "side": "right",
                                  "accel_fs_g": 8, "gyro_fs_dps": 1000, "v": 1}
    assert await conn.fetchval(
        "SELECT accel_fs_g FROM sleeve_units WHERE unit_id='u30-0'") == 8

    # an unpaired sleeve may go back to having no side (decision G)
    resp = await client.patch("/api/units/u30-0", json={"side": None})
    assert resp.status_code == 200 and resp.json()["side"] is None


async def test_patch_rejects_values_the_firmware_cannot_produce(units_app) -> None:
    app, client, _conn = units_app
    await _seen(app, "u30-0")

    for body in ({"accel_fs_g": 3}, {"accel_fs_g": 0}, {"gyro_fs_dps": 300},
                 {"gyro_fs_dps": 2001}, {"side": "middle"}):
        resp = await client.patch("/api/units/u30-0", json=body)
        assert resp.status_code == 422, body
    # every allowed value is accepted
    for accel in (2, 4, 8, 16, 32):
        assert (await client.patch("/api/units/u30-0",
                                   json={"accel_fs_g": accel})).status_code == 200
    for gyro in (125, 250, 500, 1000, 2000, 4000):
        assert (await client.patch("/api/units/u30-0",
                                   json={"gyro_fs_dps": gyro})).status_code == 200

    assert (await client.patch("/api/units/u99-0",
                               json={"side": "left"})).status_code == 404


async def test_side_clash_and_paired_side_clear_are_409(units_app) -> None:
    app, client, _conn = units_app
    await _seen(app, "u30-0", "u30-1")
    await client.patch("/api/units/u30-0", json={"side": "left"})
    resp = await client.post("/api/units/u30-0/pair",
                             json={"unit_id": "u30-1", "side": "right"})
    assert resp.status_code == 200

    # two members on one side would share a virtual source and therefore a limb
    # name, which silently rebuilds the biomech session every tick
    assert (await client.patch("/api/units/u30-1",
                               json={"side": "left"})).status_code == 409
    # and two SIDE-LESS members would both stream "thigh"/"shin"
    assert (await client.patch("/api/units/u30-1",
                               json={"side": None})).status_code == 409
    assert (await client.patch("/api/units/u30-0",
                               json={"side": None})).status_code == 409
    # the rejected writes changed nothing
    assert _cfg(app, "u30-1")["side"] == "right"
    # full-scale on a paired member is still editable
    assert (await client.patch("/api/units/u30-1",
                               json={"accel_fs_g": 16})).status_code == 200


# --- pair / unpair ------------------------------------------------------------

async def test_pair_requires_a_host_side(units_app) -> None:
    app, client, _conn = units_app
    await _seen(app, "u30-0", "u30-1")
    # the host registers with a side (decision H); clear it to reach the
    # "no side yet" path, which still exists for a unit an operator cleared
    assert (await client.patch("/api/units/u30-0",
                               json={"side": None})).status_code == 200

    resp = await client.post("/api/units/u30-0/pair",
                             json={"unit_id": "u30-1", "side": "right"})
    assert resp.status_code == 422
    # ... which host_side supplies in the same request
    resp = await client.post(
        "/api/units/u30-0/pair",
        json={"unit_id": "u30-1", "side": "right", "host_side": "left"})
    assert resp.status_code == 200
    assert {u["unit_id"]: u["side"] for u in resp.json()["units"]} == {
        "u30-0": "left", "u30-1": "right"}


async def test_pair_conflicts(units_app) -> None:
    app, client, _conn = units_app
    await _seen(app, "u30-0", "u30-1", "u31-0")
    await client.patch("/api/units/u30-0", json={"side": "left"})

    # a rig needs one unit per side
    assert (await client.post("/api/units/u30-0/pair",
                              json={"unit_id": "u30-1", "side": "left"})
            ).status_code == 409
    assert (await client.post("/api/units/u30-0/pair",
                              json={"unit_id": "u30-0", "side": "right"})
            ).status_code == 409
    assert (await client.post("/api/units/u30-0/pair",
                              json={"unit_id": "u99-0", "side": "right"})
            ).status_code == 404
    assert (await client.post("/api/units/u99-0/pair",
                              json={"unit_id": "u30-1", "side": "right"})
            ).status_code == 404

    assert (await client.post("/api/units/u30-0/pair",
                              json={"unit_id": "u30-1", "side": "right"})
            ).status_code == 200
    # already paired, from either end, and the host rig is full
    assert (await client.post("/api/units/u30-0/pair",
                              json={"unit_id": "u30-1", "side": "right"})
            ).status_code == 409
    assert (await client.post("/api/units/u30-0/pair",
                              json={"unit_id": "u31-0", "side": "right"})
            ).status_code == 409
    assert (await client.post("/api/units/u30-1/pair",
                              json={"unit_id": "u31-0", "side": "left"})
            ).status_code == 409


async def test_paired_member_is_hidden_but_still_readable(units_app) -> None:
    app, client, conn = units_app
    await _seen(app, "u30-0", "u30-1")
    await _rig_row(app, "u30-0")
    await _rig_row(app, "u30-1")
    await conn.execute("INSERT INTO devices (device_id, display_name) VALUES ('30','30')")

    # both sleeves are their own rig before pairing
    listed = [d["device_id"] for d in (await client.get("/api/devices")).json()]
    assert listed == ["30", "u30-0", "u30-1"]

    app.state.redis.published.clear()
    # Swap both legs: u30-0 registered left and u30-1 right (decision H), so
    # this is the case where BOTH rows change. A host whose side already
    # matches host_side is left alone and NOT re-mirrored (no session reset).
    resp = await client.post(
        "/api/units/u30-0/pair",
        json={"unit_id": "u30-1", "side": "left", "host_side": "right"})
    assert resp.status_code == 200
    rig = resp.json()
    assert rig["device_id"] == "u30-0" and rig["kind"] == "unilateral"
    assert [u["unit_id"] for u in rig["units"]] == ["u30-0", "u30-1"]
    assert {u["unit_id"]: u["side"] for u in rig["units"]} == {
        "u30-0": "right", "u30-1": "left"}
    # both changed units were mirrored for ingest
    assert sorted(app.state.redis.published) == [
        (redis_keys.UNIT_CFG_CHANNEL, "u30-0"),
        (redis_keys.UNIT_CFG_CHANNEL, "u30-1"),
    ]
    assert _cfg(app, "u30-1")["rig"] == "u30-0"

    # the joiner is gone from the fleet ...
    listed = [d["device_id"] for d in (await client.get("/api/devices")).json()]
    assert listed == ["30", "u30-0"]
    # ... but is still readable and renamable by id (routes/devices.py)
    hidden = await queries.device_one(
        app.state.pool, app.state.redis, app.state.settings, "u30-1")
    assert hidden is not None and hidden["device_id"] == "u30-1"
    resp = await client.patch("/api/devices/u30-1", json={"display_name": "Spare"})
    assert resp.status_code == 200 and resp.json()["display_name"] == "Spare"
    # a bilateral rig is never hidden by the predicate
    assert (await queries.device_one(
        app.state.pool, app.state.redis, app.state.settings, "30"))["kind"] == "bilateral"


async def test_device_row_carries_units_and_ingest_limb_names(units_app) -> None:
    """A sleeve rig's sensors name the limbs INGEST used, not LIMB_MAP.

    A sleeve's limb names depend on its side and on UNILATERAL_SENSOR_MAP, so
    the static bilateral map would label (0,1) "left_shin" where the rig is
    actually streaming a thigh.
    """
    app, client, _conn = units_app
    await _seen(app, "u30-0", "u30-1")
    await _rig_row(app, "u30-0")
    await client.patch("/api/units/u30-0", json={"side": "left"})
    await client.post("/api/units/u30-0/pair",
                      json={"unit_id": "u30-1", "side": "right"})

    now_ms = await app.state.pool.fetchval("SELECT extract(epoch FROM now()) * 1000")
    app.state.redis.stats.update({
        "dev:u30-0:quality": "0.990",
        "sensor:u30-0:0:1:rate_hz": "640.0",
        "sensor:u30-0:0:1:limb": "left_thigh",
        "sensor:u30-0:1:2:rate_hz": "639.0",
        "sensor:u30-0:1:2:limb": "right_shin",
        "unit:u30-0:last_seen": str(now_ms),
        "unit:u30-0:soc": "64",
        "unit:u30-1:soc": "41",
    })

    rig = (await client.get("/api/devices")).json()[0]
    assert rig["device_id"] == "u30-0" and rig["kind"] == "unilateral"
    assert [(s["source_id"], s["sensor_id"], s["limb"], s["unit_id"])
            for s in rig["sensors"]] == [
        (0, 1, "left_thigh", "u30-0"),      # virtual source 0 = the left unit
        (1, 2, "right_shin", "u30-1"),      # virtual source 1 = the right unit
    ]
    assert [(u["unit_id"], u["side"], u["soc"], u["online"]) for u in rig["units"]] == [
        ("u30-0", "left", 64, True),
        ("u30-1", "right", 41, False),
    ]


async def test_unpair_member_and_host(units_app) -> None:
    app, client, conn = units_app
    await _seen(app, "u30-0", "u30-1")
    await _rig_row(app, "u30-0")
    await _rig_row(app, "u30-1")

    assert (await client.post("/api/units/u30-1/unpair")).status_code == 409

    await client.post("/api/units/u30-0/pair",
                      json={"unit_id": "u30-1", "side": "right", "host_side": "left"})

    app.state.redis.published.clear()
    resp = await client.post("/api/units/u30-1/unpair")
    assert resp.status_code == 200
    units = {u["unit_id"]: u for u in resp.json()["units"]}
    assert set(units) == {"u30-0", "u30-1"}
    assert units["u30-1"]["rig_id"] == "u30-1" and units["u30-1"]["paired"] is False
    # sides are kept: a released sleeve is a one-leg soldier on the side it
    # was actually worn on
    assert units["u30-1"]["side"] == "right" and units["u30-0"]["side"] == "left"
    assert app.state.redis.published == [(redis_keys.UNIT_CFG_CHANNEL, "u30-1")]
    assert _cfg(app, "u30-1")["rig"] == "u30-1"

    # the member is back in the fleet with its own history
    listed = [d["device_id"] for d in (await client.get("/api/devices")).json()]
    assert listed == ["u30-0", "u30-1"]

    # unpairing from the HOST end releases every member
    await client.post("/api/units/u30-0/pair", json={"unit_id": "u30-1", "side": "right"})
    assert await conn.fetchval(
        "SELECT rig_id FROM sleeve_units WHERE unit_id='u30-1'") == "u30-0"
    resp = await client.post("/api/units/u30-0/unpair")
    assert resp.status_code == 200
    assert all(u["paired"] is False for u in resp.json()["units"])
    assert (await client.post("/api/units/u30-0/unpair")).status_code == 409
    assert (await client.post("/api/units/u99-0/unpair")).status_code == 404
