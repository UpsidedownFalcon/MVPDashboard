"""Sleeve registration + the unit-config mirror (api -> Redis -> ingest).

Ingest has no database. Everything the dashboard owns about a sleeve -- its
pairing, its side and its IMU full-scale (table `sleeve_units`, migration 005)
-- therefore reaches the pipeline through Redis: one JSON document per unit at
`unit:cfg:{id}` with NO TTL, plus a `unit_cfg` publish naming the unit that
changed. This is the FIRST key written by the api and read by ingest; every
other Redis key flows the other way.

Two jobs, one task:

  * registration (every REGISTER_INTERVAL_S) -- ingest publishes `unit:{id}:rig`
    into `ingest:stats` for every sleeve it has seen on the wire, which is how a
    brand-new sleeve announces itself. Unknown ids are inserted unpaired, with
    no side (decision G) and the Settings full-scale defaults, then mirrored.
    Ingest is already running exactly those defaults for an unconfigured unit
    and `Registry.apply_unit_config` no-ops on an equal config, so registering a
    sleeve never resets a rig (decision N is about real changes only).

  * re-mirror (at start and every MIRROR_INTERVAL_S) -- the keys have no TTL,
    so the only thing that loses them is a Redis restart or flush. Rewriting
    every row once a minute makes that self-heal within MIRROR_INTERVAL_S
    instead of needing an api restart.

Deliberately NOT part of `Writer`: the writer holds no Redis handle and returns
early whenever its tick buffer is empty (writer.py:94-95), which is precisely
the state a fleet of sleeves is in before anything has been configured.

Redis or the DB being down is normal here (the api must come up without either,
main.py:47-49): every cycle logs at most once per state change and retries.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Mapping

import asyncpg
import orjson
import redis.asyncio as aioredis

from common import kinds, redis_keys
from common.config import Settings

log = logging.getLogger("api.unit_mirror")

# Poll cadence for newly seen sleeves. Cheap: one HGETALL the health route
# already makes every second, plus one indexed lookup only when a unit id in
# the stats hash is not in the table yet.
REGISTER_INTERVAL_S = 2.0
# Full re-mirror cadence: the recovery time after a Redis restart.
MIRROR_INTERVAL_S = 60.0

MIRROR_COLUMNS = "unit_id, rig_id, side, accel_fs_g, gyro_fs_dps"

_RIG_SUFFIX = ":rig"
_UNIT_PREFIX = "unit:"


def _d(value: Any) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


def units_in_stats(raw_stats: Mapping) -> list[str]:
    """Unit ids ingest reports in `ingest:stats`, from the `unit:{id}:rig` field.

    The rig VALUE is ignored on purpose: ingest derives it from the config this
    module publishes, so treating it as input would make the two sides chase
    each other. Only the existence of the field matters -- "this sleeve is on
    the wire".
    """
    out = []
    for key in raw_stats:
        field = _d(key)
        if not field.startswith(_UNIT_PREFIX) or not field.endswith(_RIG_SUFFIX):
            continue
        unit = field[len(_UNIT_PREFIX):-len(_RIG_SUFFIX)]
        if kinds.is_unit_id(unit):
            out.append(unit)
    return sorted(set(out))


async def mirror_unit(redis: aioredis.Redis, row: Mapping) -> None:
    """Publish one sleeve_units row to Redis for ingest.

    No TTL: the config is state, not liveness, and a unit that stops streaming
    for a week must still be paired when it comes back. `mirror_all` is what
    replaces the expiry as the repair mechanism.
    """
    cfg = kinds.UnitConfig(
        unit_id=row["unit_id"],
        rig_id=row["rig_id"],
        side=row["side"],
        accel_fs_g=int(row["accel_fs_g"]),
        gyro_fs_dps=int(row["gyro_fs_dps"]),
    )
    await redis.set(redis_keys.unit_cfg(cfg.unit_id), orjson.dumps(cfg.to_json()))
    await redis.publish(redis_keys.UNIT_CFG_CHANNEL, cfg.unit_id)


async def mirror_all(pool: asyncpg.Pool, redis: aioredis.Redis) -> int:
    """Re-publish every unit; returns the number mirrored."""
    rows = await pool.fetch(
        f"SELECT {MIRROR_COLUMNS} FROM sleeve_units ORDER BY unit_id"
    )
    for row in rows:
        await mirror_unit(redis, row)
    return len(rows)


async def register_units(
    pool: asyncpg.Pool, redis: aioredis.Redis, settings: Settings,
    unit_ids: list[str],
) -> list[str]:
    """Insert unknown sleeves (unpaired, side-less, Settings full-scale).

    Returns the ids actually created, already mirrored. `ON CONFLICT DO NOTHING`
    makes a second api instance or a racing restart harmless: the loser sees no
    RETURNING row and mirrors nothing, because the winner already did.
    """
    if not unit_ids:
        return []
    known = {
        r["unit_id"] for r in await pool.fetch(
            "SELECT unit_id FROM sleeve_units WHERE unit_id = ANY($1::text[])",
            unit_ids,
        )
    }
    created: list[str] = []
    for unit in sorted(set(unit_ids) - known):
        wire = kinds.parse_unit_id(unit)
        if wire is None:            # defensive: units_in_stats already filtered
            continue
        device_id, source_id = wire
        row = await pool.fetchrow(
            f"""INSERT INTO sleeve_units (unit_id, wire_device_id, wire_source_id,
                                          rig_id, side, accel_fs_g, gyro_fs_dps)
                VALUES ($1, $2, $3, $1, NULL, $4, $5)
                ON CONFLICT (unit_id) DO NOTHING
                RETURNING {MIRROR_COLUMNS}""",
            unit, device_id, source_id,
            settings.unilateral_accel_fs_g, settings.unilateral_gyro_fs_dps,
        )
        if row is None:
            continue
        await mirror_unit(redis, row)
        created.append(unit)
    if created:
        log.info("registered sleeve unit(s): %s", ", ".join(created))
    return created


class UnitMirror:
    """One background task: register new sleeves, keep Redis in sync."""

    def __init__(self, settings: Settings, pool: asyncpg.Pool,
                 redis: aioredis.Redis) -> None:
        self._settings = settings
        self._pool = pool
        self._redis = redis
        self._task: asyncio.Task | None = None
        self._ok: bool | None = None   # None = unknown; log only on change
        self.registered = 0
        self.mirrored = 0
        self.last_error: str | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="unit-mirror")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    # --- one cycle ------------------------------------------------------------

    async def register_once(self) -> list[str]:
        raw = await self._redis.hgetall(redis_keys.INGEST_STATS)
        created = await register_units(
            self._pool, self._redis, self._settings, units_in_stats(raw)
        )
        self.registered += len(created)
        return created

    async def mirror_once(self) -> int:
        n = await mirror_all(self._pool, self._redis)
        self.mirrored += n
        return n

    async def _loop(self, interval: float = REGISTER_INTERVAL_S,
                    full_interval: float = MIRROR_INTERVAL_S) -> None:
        next_full = 0.0
        while True:
            try:
                now = time.monotonic()
                if now >= next_full:
                    await self.mirror_once()
                    next_full = now + full_interval
                await self.register_once()
                self.last_error = None
                if self._ok is not True:
                    log.info("unit mirror healthy")
                self._ok = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — db or redis down is normal
                self.last_error = str(exc)
                # Retry the full mirror on the next cycle: a failure here most
                # likely means Redis went away, which is exactly the case the
                # re-mirror exists for.
                next_full = 0.0
                if self._ok is not False:
                    log.warning("unit mirror failed (%s) — retrying every %.0fs",
                                exc, interval)
                self._ok = False
            await asyncio.sleep(interval)
