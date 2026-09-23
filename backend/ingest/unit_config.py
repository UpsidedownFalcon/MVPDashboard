"""Per-sleeve configuration: the dashboard's settings, delivered to ingest.

A knee sleeve carries two things ingest cannot learn from the wire: which rig
(soldier) it belongs to with which side, and what IMU full-scale its raw counts
are in (common/kinds.py). Both are set by an operator in the dashboard, so they
live in the api's database -- and ingest has no DB and no HTTP by design
(TRD s1). The api therefore mirrors every row to Redis as one JSON document per
unit and announces changes on a channel; this module is ingest's side of that.

It is the FIRST api -> ingest direction in the Redis contract
(BACKEND_SCHEMA s4). Everything else there flows ingest -> api.

Two pieces:
  * UnitConfigCache -- pure in-memory {unit -> UnitConfig} with rig grouping and
    per-unit defaults. No I/O, so the routing hot path never awaits.
  * UnitConfigSubscriber -- loads the whole keyspace once at start, then follows
    the channel. Redis being unavailable means every sleeve runs on defaults
    (its own rig, no side, the configured default full-scale), which is exactly
    what a brand-new sleeve does anyway.
"""

from __future__ import annotations

import asyncio
import logging

import orjson

from common import redis_keys
from common.kinds import UnitConfig

log = logging.getLogger("ingest.unit_config")

RECONNECT_MIN_S = 0.5
RECONNECT_MAX_S = 5.0
# How long to wait for the initial load before streaming on defaults. Short: a
# few hundred ms of one sleeve at the wrong full-scale is recoverable (the rig
# is rebuilt when the config lands), but blocking ingest on Redis is not.
LOAD_TIMEOUT_S = 3.0


class UnitConfigCache:
    """{unit_id -> UnitConfig}, with defaults for units nobody has configured."""

    def __init__(self, accel_fs_g: int, gyro_fs_dps: int) -> None:
        self._by_unit: dict[str, UnitConfig] = {}
        self.default_accel_fs_g = accel_fs_g
        self.default_gyro_fs_dps = gyro_fs_dps

    def default(self, unit_id: str) -> UnitConfig:
        """An unconfigured sleeve: its own rig, no side, the default scale."""
        return UnitConfig.default(unit_id, self.default_accel_fs_g,
                                  self.default_gyro_fs_dps)

    def get(self, unit_id: str) -> UnitConfig:
        return self._by_unit.get(unit_id) or self.default(unit_id)

    def members(self, rig_id: str) -> list[UnitConfig]:
        """Every unit that belongs to this rig, the host first.

        The host is listed even when nothing has been configured for it, so a
        rig can be built before any of its units has streamed a packet -- but
        only if it still claims this rig. A unit that has itself been paired
        into a third rig is not a member of this one, and including it would
        put one sleeve's samples into two rigs at once.
        """
        host = self.get(rig_id)
        others = sorted(
            (cfg for uid, cfg in self._by_unit.items()
             if cfg.rig_id == rig_id and uid != rig_id),
            key=lambda c: c.unit_id,
        )
        return ([host] if host.rig_id == rig_id else []) + others

    def set(self, cfg: UnitConfig) -> UnitConfig | None:
        """Store a config; returns the PREVIOUS one when something changed."""
        previous = self.get(cfg.unit_id)
        if previous == cfg:
            return None
        self._by_unit[cfg.unit_id] = cfg
        return previous

    def __len__(self) -> int:
        return len(self._by_unit)


class UnitConfigSubscriber:
    """Loads unit:cfg:* once, then follows the unit_cfg channel forever."""

    def __init__(self, settings, cache: UnitConfigCache, registry, client=None) -> None:
        self._cache = cache
        self._registry = registry
        if client is None:
            import redis.asyncio as aioredis  # noqa: PLC0415
            client = aioredis.from_url(settings.redis_url)
        self._client = client
        self.applied = 0

    def _apply(self, unit_id: str, raw: bytes | str | None) -> None:
        """Parse one mirrored document and push it into the routing tables."""
        if raw is None:
            # The key is gone (the api deleted it, or Redis was flushed). Fall
            # back to the defaults rather than keeping a stale pairing, and go
            # through the ordinary path so the affected rigs are torn down.
            self._registry.apply_unit_config(self._cache.default(unit_id))
            return
        try:
            cfg = UnitConfig.from_json(orjson.loads(raw))
        except Exception as exc:  # noqa: BLE001 - a bad document must not kill the loop
            log.warning("unit %s: ignoring unusable config (%s)", unit_id, exc)
            return
        if cfg.unit_id != unit_id:
            log.warning("unit %s: config claims to be %s — ignored", unit_id, cfg.unit_id)
            return
        if self._registry.apply_unit_config(cfg):
            self.applied += 1
            log.info("unit %s: rig=%s side=%s fs=+-%dg/+-%ddps",
                     cfg.unit_id, cfg.rig_id, cfg.side or "unset",
                     cfg.accel_fs_g, cfg.gyro_fs_dps)

    async def load(self) -> int:
        """Read every mirrored unit config. Returns how many were applied."""
        before = self.applied
        keys = [key async for key in self._client.scan_iter(
            match=redis_keys.UNIT_CFG_PATTERN, count=100)]
        if keys:
            values = await self._client.mget(keys)
            prefix = len(redis_keys.unit_cfg(""))
            for key, raw in zip(keys, values):
                name = key.decode() if isinstance(key, bytes) else key
                self._apply(name[prefix:], raw)
        return self.applied - before

    async def load_or_default(self) -> None:
        """Bounded initial load: never let Redis delay the UDP path for long."""
        try:
            n = await asyncio.wait_for(self.load(), timeout=LOAD_TIMEOUT_S)
        except (TimeoutError, asyncio.TimeoutError):
            log.warning("unit configs not loaded within %.0fs — sleeves start on "
                        "defaults until the subscriber catches up", LOAD_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            log.warning("unit configs unavailable (%s) — sleeves start on defaults", exc)
        else:
            log.info("loaded %d unit config(s)", n)

    async def run(self) -> None:
        """Follow the channel, reloading everything after every reconnect."""
        backoff = RECONNECT_MIN_S
        while True:
            pubsub = None
            try:
                pubsub = self._client.pubsub(ignore_subscribe_messages=True)
                await pubsub.subscribe(redis_keys.UNIT_CFG_CHANNEL)
                # A reconnect may have missed changes, so re-read the keyspace
                # before trusting the stream again.
                await self.load()
                backoff = RECONNECT_MIN_S
                async for message in pubsub.listen():
                    data = message.get("data")
                    if not data:
                        continue
                    unit_id = data.decode() if isinstance(data, bytes) else str(data)
                    raw = await self._client.get(redis_keys.unit_cfg(unit_id))
                    self._apply(unit_id, raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("unit config subscription failed (%s) — retrying in %.1fs",
                            exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_S)
            finally:
                if pubsub is not None:
                    with_suppressed = getattr(pubsub, "aclose", pubsub.close)
                    try:
                        await with_suppressed()
                    except Exception:  # noqa: BLE001
                        pass

    async def close(self) -> None:
        await self._client.aclose()
