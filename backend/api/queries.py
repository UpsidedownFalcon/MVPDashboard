"""Read-side query layer (S2-T03) — all SQL parameterized asyncpg, Redis
merges for liveness. Response shapes are BACKEND_SCHEMA.md §3, exactly.

Source-table rule: past windows <= 5 min query the 60Hz `metrics` table
directly; anything larger reads the `metrics_1m` continuous aggregate.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import redis.asyncio as aioredis

from common import redis_keys
from common.config import Settings

# Windows AT OR BELOW this read the raw hypertable. The comparison used to be
# strict (`<`), which made the raw path dead code in production: the shipped
# PAST_WINDOWS starts at exactly 5m, and `5m < 5m` is False. That mattered
# because `metrics_1m` is created without `materialized_only = false`, so on
# TimescaleDB >= 2.13 the newest 1-2 minutes of the aggregate do not exist
# until the refresh policy runs -- up to 40% of a 5m window. The raw table has
# no such lag, and 5 min of 60Hz data is ~18k rows behind the (device_id, time)
# index, so serving the shortest window from it is both cheaper and correct.
WINDOW_RAW_MAX = timedelta(minutes=5)

# Trend dead-band. The floor is the original absolute ±2.0 points, kept so
# behaviour on quiet data is unchanged. The dispersion term is what is new:
# ±2.0 is ~2.4% of an 85-composite reading but 20% of a 10-composite one, and
# it was applied identically to a 2m and a 2h window on a metric that is now
# far more volatile in the same demand range than when the constant was chosen.
#
# The scale factor multiplies the pooled STANDARD DEVIATION, not the standard
# error of the mean. That is deliberate: consecutive composite samples are
# strongly autocorrelated (m3 is a 45-min accumulator, m1/m2 are 1 s peak
# holds), so the effective sample size behind a window mean can be O(1) rather
# than O(n) and sd/sqrt(n) would overstate the precision by an unknown factor.
# Comparing against the raw spread is the honest conservative bound.
# 0.5 sd is Cohen's medium-effect boundary -- a shift worth naming.
TREND_DEADBAND_FLOOR = 2.0
TREND_DEADBAND_SDS = 0.5


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
        # Every row here is one 60Hz sample, so a plain avg() is ALREADY the
        # correctly weighted mean -- the weighting problem below is specific to
        # the aggregate, where one row is a whole bucket.
        return await pool.fetchrow(
            """SELECT avg(m1) m1, avg(m2) m2, avg(m3) m3, avg(m4) m4, avg(m5) m5,
                      avg(composite) c_avg, min(composite) c_min, max(composite) c_max,
                      stddev_samp(composite) c_sd,
                      stddev_samp(m1) m1_sd, stddev_samp(m2) m2_sd,
                      stddev_samp(m3) m3_sd, stddev_samp(m4) m4_sd,
                      stddev_samp(m5) m5_sd,
                      avg(quality) quality, count(*) n
               FROM metrics
               WHERE device_id=$1 AND time >= $2 AND time < $3""",
            device_id, t_from, t_to,
        )
    # Each metrics_1m row is a 1-minute bucket holding `n` 60Hz samples, so an
    # unweighted avg() of per-bucket averages is NOT the window mean: it gives a
    # 2-second partial bucket (n=120) the same weight as a full one (n=3600).
    # Measured on the 712 rows in the local database (buckets n=300 and n=412):
    # unweighted 13.688 vs weighted 12.348, an 11% error.
    #
    # composite and quality are NOT NULL in `metrics`, and m3 is assigned
    # unconditionally by biomech.compute(), so `n` is the exactly correct weight
    # for all three (the local DB confirms count(m3) = count(*) = 712). The
    # `filter` clauses are defensive only, and cost nothing.
    #
    # m1/m2/m4/m5 deliberately stay on the unweighted avg(): each bucket's value
    # is itself a NULL-skipping average over an unknown subset of that minute
    # (m1/m2 are suppressed above 2.6% saturation, m4/m5 have warm-ups and
    # freeze on missing sensors), so `n` is the wrong weight for them and the
    # right one -- count(mN) per bucket -- is not a column the aggregate has.
    # Weighting them by `n` would trade a known bias for a wrong correction.
    return await pool.fetchrow(
        """SELECT avg(m1) m1, avg(m2) m2, avg(m5) m5, avg(m4) m4,
                  sum(m3 * n) / nullif(sum(n) FILTER (WHERE m3 IS NOT NULL), 0) m3,
                  sum(composite * n)
                      / nullif(sum(n) FILTER (WHERE composite IS NOT NULL), 0) c_avg,
                  min(composite_min) c_min, max(composite_max) c_max,
                  stddev_samp(composite) c_sd,
                  stddev_samp(m1) m1_sd, stddev_samp(m2) m2_sd,
                  stddev_samp(m3) m3_sd, stddev_samp(m4) m4_sd,
                  stddev_samp(m5) m5_sd,
                  sum(quality * n)
                      / nullif(sum(n) FILTER (WHERE quality IS NOT NULL), 0) quality,
                  coalesce(sum(n), 0) n
           FROM metrics_1m
           WHERE device_id=$1 AND bucket >= $2 AND bucket < $3""",
        device_id, t_from, t_to,
    )


def _trend(cur_avg: float | None, prev_avg: float | None,
           cur_sd: float | None, prev_sd: float | None) -> str:
    """Current vs the preceding equal-length window, dead-banded by the larger
    of an absolute floor and a fraction of the pooled within-window spread."""
    if cur_avg is None or prev_avg is None:
        return "flat"
    # stddev_samp is NULL for a single row and 0 for a constant series; both
    # mean "no measurable spread", which the floor then covers.
    sd_c, sd_p = cur_sd or 0.0, prev_sd or 0.0
    pooled_sd = math.sqrt((sd_c * sd_c + sd_p * sd_p) / 2.0)
    deadband = max(TREND_DEADBAND_FLOOR, TREND_DEADBAND_SDS * pooled_sd)
    diff = cur_avg - prev_avg
    if diff > deadband:
        return "up"
    if diff < -deadband:
        return "down"
    return "flat"


def _window_entry(label: str, t_from: datetime, cur: asyncpg.Record,
                  prev: asyncpg.Record, expected_rows: float) -> dict:
    """One BACKEND_SCHEMA §3 window entry from a cur/prev aggregate pair.

    `sd` and `coverage` are what make the insight layer retune-proof and honest
    respectively (docs/ANALYTICS.md). `sd` lets a rule express a deviation in
    units of the athlete's OWN spread, which is invariant to any change in a
    metric's 0-100 normalisation bounds. `coverage` is the share of the window
    that actually has data — without it a window average cannot be told apart
    from a sliver of one.
    """
    return {
        "window": label,
        "from": _iso(t_from),
        "m": [_f(cur["m1"]), _f(cur["m2"]), _f(cur["m3"]), _f(cur["m4"]), _f(cur["m5"])],
        "sd": [_f(cur["m1_sd"]), _f(cur["m2_sd"]), _f(cur["m3_sd"]),
               _f(cur["m4_sd"]), _f(cur["m5_sd"])],
        "composite": {
            "avg": _f(cur["c_avg"]), "min": _f(cur["c_min"]), "max": _f(cur["c_max"]),
            "sd": _f(cur["c_sd"]),
        },
        "quality": _f(cur["quality"]),
        # sum() over the aggregate returns Decimal; float() before dividing
        "coverage": (min(1.0, float(cur["n"] or 0) / expected_rows)
                     if expected_rows else None),
        "trend": _trend(_f(cur["c_avg"]), _f(prev["c_avg"]),
                        _f(cur["c_sd"]), _f(prev["c_sd"])),
    }


async def live_window(pool: asyncpg.Pool, settings: Settings,
                      device_id: str) -> dict:
    """The INSIGHT_LIVE_WINDOW aggregate — the rule engine's "now".

    Always served from the raw 60Hz hypertable. That is not an optimisation but
    a correctness requirement: `metrics_1m` is materialized-only, so its newest
    1-2 minutes do not exist yet, which is most or all of a sub-minute window.
    Shape matches a `windows()` entry exactly, so MetricView consumes it
    unchanged.
    """
    td = settings.insight_live_window
    now = datetime.now(tz=timezone.utc)
    cur = await _window_agg(pool, device_id, now - td, now, use_raw=True)
    prev = await _window_agg(pool, device_id, now - 2 * td, now - td, use_raw=True)
    return _window_entry(settings.insight_live_window_raw, now - td, cur, prev,
                         settings.output_hz * td.total_seconds())


async def windows(pool: asyncpg.Pool, settings: Settings, device_id: str) -> dict:
    """One entry per PAST_WINDOWS duration; trend vs the preceding
    equal-length window (dead-band: see TREND_DEADBAND_FLOOR/_SDS)."""
    now = datetime.now(tz=timezone.utc)
    out = []
    labels = [w.strip() for w in settings.past_windows_raw.split(",")]
    for label, td in zip(labels, settings.past_windows):
        use_raw = td <= WINDOW_RAW_MAX
        cur = await _window_agg(pool, device_id, now - td, now, use_raw)
        prev = await _window_agg(pool, device_id, now - 2 * td, now - td, use_raw)
        out.append(_window_entry(label, now - td, cur, prev,
                                 settings.output_hz * td.total_seconds()))
    return {"windows": out}


# --- history (S3-T10) ---------------------------------------------------------

# Reading metrics_1m with buckets finer than its 1-minute rows would scatter
# each source row into one sub-minute bucket and leave the rest null — rendering
# as data gaps that aren't real. Clamp the span to >= 1 minute on that path and
# let the bucket count shrink instead (BACKEND_SCHEMA §3).
HISTORY_MIN_AGG_SPAN_S = 60.0


async def history(
    pool: asyncpg.Pool, settings: Settings, device_id: str,
    window: str, buckets: int,
) -> dict:
    """Time-bucketed per-metric series for the History tab (schema §3).

    `window` must be one of PAST_WINDOWS (the route validates); buckets are
    aligned to `from` (time_bucket origin = window start), so bucket k covers
    [from + k*span, from + (k+1)*span). Buckets with no rows are None.
    """
    labels = [w.strip() for w in settings.past_windows_raw.split(",")]
    td = dict(zip(labels, settings.past_windows))[window]
    use_raw = td <= WINDOW_RAW_MAX

    total_s = td.total_seconds()
    span_s = total_s / buckets
    if not use_raw and span_s < HISTORY_MIN_AGG_SPAN_S:
        buckets = max(1, int(total_s // HISTORY_MIN_AGG_SPAN_S))
        span_s = total_s / buckets
    span = timedelta(seconds=span_s)

    now = datetime.now(tz=timezone.utc)
    t_from = now - td

    if use_raw:
        # One row = one 60Hz sample: plain avg() is the correctly weighted mean
        # (same reasoning as _window_agg's raw path).
        rows = await pool.fetch(
            """SELECT time_bucket($4::interval, time, $2::timestamptz) AS tb,
                      avg(m1) m1, avg(m2) m2, avg(m3) m3, avg(m4) m4, avg(m5) m5,
                      avg(composite) c_avg, min(composite) c_min, max(composite) c_max,
                      avg(quality) quality
               FROM metrics
               WHERE device_id=$1 AND time >= $2 AND time < $3
               GROUP BY tb ORDER BY tb""",
            device_id, t_from, now, span,
        )
    else:
        # metrics_1m rows are 1-minute buckets carrying `n`; weight composite,
        # quality and m3 by n, leave m1/m2/m4/m5 unweighted — the same
        # weighting rationale documented on _window_agg above.
        rows = await pool.fetch(
            """SELECT time_bucket($4::interval, bucket, $2::timestamptz) AS tb,
                      avg(m1) m1, avg(m2) m2, avg(m4) m4, avg(m5) m5,
                      sum(m3 * n) / nullif(sum(n) FILTER (WHERE m3 IS NOT NULL), 0) m3,
                      sum(composite * n)
                          / nullif(sum(n) FILTER (WHERE composite IS NOT NULL), 0) c_avg,
                      min(composite_min) c_min, max(composite_max) c_max,
                      sum(quality * n)
                          / nullif(sum(n) FILTER (WHERE quality IS NOT NULL), 0) quality
               FROM metrics_1m
               WHERE device_id=$1 AND bucket >= $2 AND bucket < $3
               GROUP BY tb ORDER BY tb""",
            device_id, t_from, now, span,
        )

    by_start: dict[datetime, asyncpg.Record] = {r["tb"]: r for r in rows}
    out: list[dict | None] = []
    for k in range(buckets):
        start = t_from + k * span
        r = by_start.get(start)
        if r is None:
            out.append(None)
            continue
        out.append({
            "t": _iso(start),
            "m": [_f(r["m1"]), _f(r["m2"]), _f(r["m3"]), _f(r["m4"]), _f(r["m5"])],
            "composite": {
                "avg": _f(r["c_avg"]), "min": _f(r["c_min"]), "max": _f(r["c_max"]),
            },
            "quality": _f(r["quality"]),
        })
    return {
        "device_id": device_id,
        "window": window,
        "from": _iso(t_from),
        "bucket_s": int(round(span_s)),
        "buckets": out,
    }


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

        # Battery state of charge, 0-100. `dev:{id}:soc` is already the minimum
        # across the device's leg MCUs (publish.py) -- a dying unit must not
        # hide behind a healthy one. None until a datagram has been seen.
        soc_raw = stats.get(f"dev:{dev}:soc")
        out.append({
            "device_id": dev,
            "display_name": row["display_name"],
            "soc": int(soc_raw) if soc_raw is not None else None,
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
