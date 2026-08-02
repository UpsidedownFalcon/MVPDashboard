"""Read-side query layer (S2-T03) — all SQL parameterized asyncpg, Redis
merges for liveness. Response shapes are BACKEND_SCHEMA.md §3, exactly.

Source-table rule: past windows < 5 min query the 60Hz `metrics` table
directly; anything larger reads the `metrics_1m` continuous aggregate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import redis.asyncio as aioredis

from common import redis_keys
from common.config import Settings

WINDOW_CAGG_MIN = timedelta(minutes=5)
# trend dead-band: ±2% of the 0-100 composite full scale
TREND_DEADBAND = 2.0


def _iso(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _iso_ms(unix_ms: float) -> str:
    return _iso(datetime.fromtimestamp(unix_ms / 1000.0, tz=timezone.utc))


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


async def device_exists(pool: asyncpg.Pool, device_id: str) -> bool:
    return await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM devices WHERE device_id=$1)", device_id
    )


# --- recent -------------------------------------------------------------------

async def recent(pool: asyncpg.Pool, device_id: str, seconds: int) -> dict:
    """Compact arrays for chart backfill: rows = [[t_offset_ms, m1..m5, c, q]]."""
    rows = await pool.fetch(
        """SELECT time, m1, m2, m3, m4, m5, composite, quality
           FROM metrics
           WHERE device_id = $1 AND time >= now() - $2::interval
           ORDER BY time""",
        device_id, timedelta(seconds=seconds),
    )
    if not rows:
        return {"device_id": device_id, "t0": None, "rows": []}
    t0 = rows[0]["time"]
    return {
        "device_id": device_id,
        "t0": _iso(t0),
        "rows": [
            [
                int((r["time"] - t0).total_seconds() * 1000),
                _f(r["m1"]), _f(r["m2"]), _f(r["m3"]), _f(r["m4"]), _f(r["m5"]),
                float(r["composite"]), float(r["quality"]),
            ]
            for r in rows
        ],
    }


# --- windows ------------------------------------------------------------------

async def _window_agg(
    pool: asyncpg.Pool, device_id: str, t_from: datetime, t_to: datetime,
    use_raw: bool,
) -> asyncpg.Record:
    if use_raw:
        return await pool.fetchrow(
            """SELECT avg(m1) m1, avg(m2) m2, avg(m3) m3, avg(m4) m4, avg(m5) m5,
                      avg(composite) c_avg, min(composite) c_min, max(composite) c_max,
                      avg(quality) quality, count(*) n
               FROM metrics
               WHERE device_id=$1 AND time >= $2 AND time < $3""",
            device_id, t_from, t_to,
        )
    return await pool.fetchrow(
        """SELECT avg(m1) m1, avg(m2) m2, avg(m3) m3, avg(m4) m4, avg(m5) m5,
                  avg(composite) c_avg, min(composite_min) c_min, max(composite_max) c_max,
                  avg(quality) quality, coalesce(sum(n), 0) n
           FROM metrics_1m
           WHERE device_id=$1 AND bucket >= $2 AND bucket < $3""",
        device_id, t_from, t_to,
    )


def _trend(cur_avg: float | None, prev_avg: float | None) -> str:
    if cur_avg is None or prev_avg is None:
        return "flat"
    diff = cur_avg - prev_avg
    if diff > TREND_DEADBAND:
        return "up"
    if diff < -TREND_DEADBAND:
        return "down"
    return "flat"


async def windows(pool: asyncpg.Pool, settings: Settings, device_id: str) -> dict:
    """One entry per PAST_WINDOWS duration; trend vs the preceding
    equal-length window (±2% of full scale dead-band)."""
    now = datetime.now(tz=timezone.utc)
    out = []
    labels = [w.strip() for w in settings.past_windows_raw.split(",")]
    for label, td in zip(labels, settings.past_windows):
        use_raw = td < WINDOW_CAGG_MIN
        cur = await _window_agg(pool, device_id, now - td, now, use_raw)
        prev = await _window_agg(pool, device_id, now - 2 * td, now - td, use_raw)
        out.append({
            "window": label,
            "from": _iso(now - td),
            "m": [_f(cur["m1"]), _f(cur["m2"]), _f(cur["m3"]), _f(cur["m4"]), _f(cur["m5"])],
            "composite": {
                "avg": _f(cur["c_avg"]), "min": _f(cur["c_min"]), "max": _f(cur["c_max"]),
            },
            "quality": _f(cur["quality"]),
            "trend": _trend(_f(cur["c_avg"]), _f(prev["c_avg"])),
        })
    return {"windows": out}


# --- devices ------------------------------------------------------------------

def _d(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


async def devices(
    pool: asyncpg.Pool, redis: aioredis.Redis, settings: Settings,
) -> list[dict]:
    """Registry rows merged with Redis liveness + per-sensor stats."""
    rows = await pool.fetch(
        "SELECT device_id, display_name FROM devices ORDER BY device_id"
    )
    raw_stats = await redis.hgetall(redis_keys.INGEST_STATS)
    stats = {_d(k): _d(v) for k, v in raw_stats.items()}
    now_ms = datetime.now(tz=timezone.utc).timestamp() * 1000.0
    threshold_ms = settings.offline_after_s * 1000.0

    out = []
    for row in rows:
        dev = row["device_id"]
        last_seen_raw = _d(await redis.get(redis_keys.last_seen_dev(dev)))
        last_seen_ms = float(last_seen_raw) if last_seen_raw else None
        quality = stats.get(f"dev:{dev}:quality")

        sensors = []
        prefix = f"sensor:{dev}:"
        pairs = sorted({
            tuple(int(p) for p in key[len(prefix):].split(":")[:2])
            for key in stats
            if key.startswith(prefix)
        })
        for src, sen in pairs:
            sensor_seen_raw = _d(await redis.get(redis_keys.last_seen_sensor(dev, src, sen)))
            rate = stats.get(f"sensor:{dev}:{src}:{sen}:rate_hz")
            sensors.append({
                "source_id": src,
                "sensor_id": sen,
                "limb": settings.limb_map.get((src, sen), f"{src},{sen}"),
                "rate_hz": float(rate) if rate is not None else 0.0,
                "last_seen": _iso_ms(float(sensor_seen_raw)) if sensor_seen_raw else None,
            })

        out.append({
            "device_id": dev,
            "display_name": row["display_name"],
            "online": (
                last_seen_ms is not None and (now_ms - last_seen_ms) <= threshold_ms
            ),
            "last_seen": _iso_ms(last_seen_ms) if last_seen_ms is not None else None,
            "quality": float(quality) if quality is not None else None,
            "sensors": sensors,
        })
    return out


async def device_one(
    pool: asyncpg.Pool, redis: aioredis.Redis, settings: Settings, device_id: str,
) -> dict | None:
    for dev in await devices(pool, redis, settings):
        if dev["device_id"] == device_id:
            return dev
    return None
