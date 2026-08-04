"""Redis publishing — ingest's side of the contract (BACKEND_SCHEMA.md §§2, 4).

Tick JSON -> PUBLISH ticks (fire-and-forget, bounded pending, never blocks the
ticker). 1s loop refreshes last_seen keys and rewrites the ingest:stats hash.
Redis being down never crashes or stalls ingest: publishes are dropped+counted
and a reconnect probe runs with backoff.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone

import orjson
import redis.asyncio as aioredis

from common import redis_keys
from common.config import Settings
from ingest import biomech
from ingest.biomech import Metrics
from ingest.state import Registry
from ingest.ticker import TickInput

log = logging.getLogger("ingest.publish")

MAX_PENDING = 256
RECONNECT_MIN_S = 0.5
RECONNECT_MAX_S = 5.0


def tick_to_json(tick: TickInput, metrics: Metrics) -> bytes:
    """Exactly the §2 schema: {"type","t","dev","m","c","q","f","cal"}.

    m entries are nullable (§2): m4/m5 are None for the first minute of every
    session, and whenever a leg loses a sensor. round(None, 4) raises TypeError,
    and this runs synchronously inside the ticker task — so an unguarded round()
    here would permanently kill that device's ticker on the very first tick.

    `cal` is seconds of continued stillness before calibration completes, or
    null when nothing is calibrating. It rides the tick rather than a poll
    because it is a COUNTDOWN the athlete is watching — anything slower than
    the tick makes it visibly jump.
    """
    dt = datetime.fromtimestamp(tick.t_server, tz=timezone.utc)
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    cal_left = metrics.raw.get("cal_left") if metrics.raw else None
    return orjson.dumps({
        "type": "tick",
        "t": iso,
        "dev": str(tick.device_id),
        "m": [None if v is None else round(v, 2) for v in metrics.as_list()],
        "c": round(metrics.composite, 2),
        "q": round(tick.quality, 3),
        "f": sorted(metrics.flags) or None,
        "cal": (
            round(cal_left, 1)
            if cal_left is not None and math.isfinite(cal_left)
            else None
        ),
    })


class Publisher:
    def __init__(self, settings: Settings, registry: Registry) -> None:
        self._registry = registry
        self._session_gap_s = settings.session_gap_s
        self._redis = aioredis.from_url(settings.redis_url)
        self._pending: set[asyncio.Task] = set()
        self._up = True                # optimistic until a command fails
        self._retry_at = 0.0
        self._backoff = RECONNECT_MIN_S
        self._started = time.time()
        self.published = 0
        self.pub_dropped = 0

    # --- tick path (called from the ticker callback, must never block) ----------

    def publish_tick(self, tick: TickInput, metrics: Metrics) -> None:
        if not self._up:
            if time.time() >= self._retry_at:
                self._up = True        # probe again with the next publish
            else:
                self.pub_dropped += 1
                return
        if len(self._pending) >= MAX_PENDING:
            self.pub_dropped += 1
            return
        task = asyncio.create_task(self._send(tick_to_json(tick, metrics)))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _send(self, payload: bytes) -> None:
        try:
            await self._redis.publish(redis_keys.TICKS_CHANNEL, payload)
            self.published += 1
            self._backoff = RECONNECT_MIN_S
        except Exception as exc:  # noqa: BLE001 — any redis failure = drop + backoff
            self.pub_dropped += 1
            if self._up:
                log.warning("redis publish failed (%s) — backing off %.1fs",
                            exc, self._backoff)
            self._up = False
            self._retry_at = time.time() + self._backoff
            self._backoff = min(self._backoff * 2, RECONNECT_MAX_S)

    # --- 1s stats/last_seen loop ------------------------------------------------

    def _stats_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {
            "uptime_s": f"{time.time() - self._started:.0f}",
            "global:crc_fail": str(self._registry.crc_fail),
            "global:bad_sync": str(self._registry.bad_sync),
            "global:bad_len": str(self._registry.bad_len),
            "global:pub_dropped": str(self.pub_dropped),
            "global:published": str(self.published),
        }
        for device_id, device in self._registry.devices.items():
            mapping[f"dev:{device_id}:ticks_out"] = str(device.ticks_out)
            mapping[f"dev:{device_id}:tick_rate"] = f"{device.tick_rate:.1f}"
            if device.quality_ema is not None:
                mapping[f"dev:{device_id}:quality"] = f"{device.quality_ema:.3f}"
            # Battery: publish the LOWEST of the device's leg MCUs, so a dying
            # unit cannot hide behind a healthy one. Per-source values go out
            # too, for diagnosis.
            if device.soc:
                mapping[f"dev:{device_id}:soc"] = str(min(device.soc.values()))
                for src, soc in device.soc.items():
                    mapping[f"dev:{device_id}:soc:{src}"] = str(soc)
            for (src, sen), sensor in device.sensors.items():
                p = f"sensor:{device_id}:{src}:{sen}"
                st = sensor.stats
                mapping[f"{p}:rate_hz"] = f"{st.rate_hz:.1f}"
                mapping[f"{p}:recv"] = str(st.recv)
                mapping[f"{p}:late_drop"] = str(st.late_drop)
                mapping[f"{p}:buf_drop"] = str(st.buf_drop)
                mapping[f"{p}:sat_count"] = str(st.sat_count)
        mapping["global:dev_dropped"] = str(self._registry.dev_dropped)
        return mapping

    def _write_biomech(self, pipe, now_ms: int) -> None:
        """Per-device biomech diagnostics + the warm-restart state snapshot.

        The snapshot (§7.4) is why an ingest restart does not silently reset a
        mid-session athlete to zero accumulated load. ~200 B of O(1) scalars;
        the 1 s summary rings and filter state are deliberately NOT persisted —
        they re-warm in under a second and are not worth the bytes.
        """
        ttl = int(2 * self._session_gap_s)
        for device_id, device in self._registry.devices.items():
            metrics = getattr(device, "last_metrics", None)
            if metrics is not None:
                diag = {k: f"{v:.6g}" for k, v in metrics.raw.items()}
                diag["flags"] = ",".join(sorted(metrics.flags))
                key = redis_keys.biomech_diag(device_id)
                pipe.delete(key)
                pipe.hset(key, mapping=diag)
                # Expire with the device: without a TTL, /api/health keeps
                # reporting devices that stopped streaming hours ago.
                pipe.expire(key, ttl)
            snap = biomech.snapshot(device.user_state)
            if snap is not None:
                pipe.set(redis_keys.biomech_state(device_id),
                         orjson.dumps(snap), ex=ttl)
            # Last-known-good calibration, carried between SESSIONS (SPEC §3.8)
            # — a separate key with a TTL of days, because the snapshot above is
            # deliberately discarded after SESSION_GAP_S and that is exactly
            # when carry-over matters. Only a device with no history should
            # ever run on defaults.
            cal = biomech.calibration_export(device.user_state)
            if cal is not None:
                pipe.set(redis_keys.biomech_cal(device_id),
                         orjson.dumps(cal), ex=biomech.CAL_CARRY_TTL_S)

    async def stats_loop(self, interval: float = 1.0) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                now_ms = int(time.time() * 1000)
                pipe = self._redis.pipeline(transaction=False)
                for device_id, device in self._registry.devices.items():
                    if device.last_seen:
                        seen_ms = int(device.last_seen * 1000)
                        pipe.set(redis_keys.last_seen_dev(device_id), seen_ms)
                    for (src, sen), sensor in device.sensors.items():
                        if sensor.last_seen:
                            pipe.set(
                                redis_keys.last_seen_sensor(device_id, src, sen),
                                int(sensor.last_seen * 1000),
                            )
                pipe.delete(redis_keys.INGEST_STATS)
                pipe.hset(redis_keys.INGEST_STATS, mapping=self._stats_mapping())
                self._write_biomech(pipe, now_ms)
                await pipe.execute()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("stats write failed: %s (retrying next interval)", exc)

    async def close(self) -> None:
        for task in list(self._pending):
            task.cancel()
        await asyncio.gather(*self._pending, return_exceptions=True)
        await self._redis.aclose()
