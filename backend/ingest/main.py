"""Ingest service entrypoint — final wiring (S1-T11).

UDP -> decode -> route -> (per device) align -> jitter -> 60Hz ticker ->
biomech stub -> Redis publish, plus 1s stats/last_seen writes. Nothing in this
process touches a DB or serves HTTP (stall-risk isolation, TRD §1).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time
from collections import deque

from common.config import get_settings
from ingest import biomech
from ingest.publish import Publisher
from ingest.state import Registry
from ingest.ticker import TickerManager, TickInput
from ingest.udp import RAW_BUF_MAXLEN, UdpCounters, drain_loop, start_udp_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ingest")

STATS_INTERVAL_S = 1.0

SECRET_KEYS = {"postgres_password", "jwt_secret", "seed_users"}


def _config_echo(settings) -> str:
    pairs = []
    for key, value in settings.model_dump().items():
        if key in SECRET_KEYS:
            value = "***"
        pairs.append(f"{key}={value}")
    return " ".join(pairs)


class BiomechRestorer:
    """Restores a device's warm-restart snapshot whenever the device appears.

    Per DEVICE, not per process start (biomech SPEC §7.4). Devices are created
    on first packet and released again — evicted when silent past SESSION_GAP_S,
    or displaced by a new device once MAX_DEVICES is reached, which needs only
    OFFLINE_AFTER_S of silence. A device released that way and reconnecting a
    few seconds later gets a brand-new DeviceState with empty session state,
    while `biomech:state:{id}` is still sitting in Redis and still inside the
    session gap. Loading the snapshots once at startup could not reach that
    case: it restored a device's first appearance only, so a trainer swapping
    wearables silently zeroed an athlete's accumulated load.

    The fetch is asynchronous, so a device's first few ticks may run before the
    snapshot lands; restore() overwrites the accumulators, so at 60 Hz that
    costs a few ms of dose. Redis being unavailable just means a fresh session,
    which is the same as having no snapshot.
    """

    def __init__(self, settings, limbs: tuple[str, ...], client=None) -> None:
        self._limbs = limbs
        self._session_gap_s = settings.session_gap_s
        if client is None:
            import redis.asyncio as aioredis  # noqa: PLC0415
            client = aioredis.from_url(settings.redis_url)
        self._client = client
        self._tasks: set[asyncio.Task] = set()
        self.restored = 0

    def device_added(self, device) -> None:
        """Registry callback (synchronous): kick off the fetch, never block."""
        task = asyncio.create_task(self._restore(device),
                                   name=f"restore-dev{device.device_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _restore(self, device) -> None:
        import orjson  # noqa: PLC0415

        from common import redis_keys  # noqa: PLC0415
        try:
            raw = await self._client.get(redis_keys.biomech_state(device.device_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("device %d: snapshot fetch failed (%s) — fresh session",
                        device.device_id, exc)
            return
        if not raw:
            return
        ok = biomech.restore(device.user_state, orjson.loads(raw), self._limbs,
                             time.time(), self._session_gap_s)
        if ok:
            self.restored += 1
            log.info("device %d: biomech session restored from snapshot",
                     device.device_id)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._client.aclose()


async def stats_loop(registry: Registry, udp_counters: UdpCounters,
                     interval: float = STATS_INTERVAL_S,
                     evict_after_s: float | None = None) -> None:
    while True:
        await asyncio.sleep(interval)
        if evict_after_s is not None:
            registry.evict_stale(time.time(), evict_after_s)
        registry.update_rates(interval)
        if registry.devices:
            for line in registry.summary_lines():
                log.info("%s", line)
            if udp_counters.buf_drop:
                log.warning("raw UDP buffer drops: %d", udp_counters.buf_drop)


async def amain() -> None:
    settings = get_settings()
    log.info("ingest starting: %s", _config_echo(settings))

    buf: deque[bytes] = deque(maxlen=RAW_BUF_MAXLEN)
    udp_counters = UdpCounters()
    registry = Registry(max_devices=settings.max_devices)
    registry.offline_after_s = settings.offline_after_s
    publisher = Publisher(settings, registry)

    # Warm-restart snapshots (biomech SPEC §7.4), fetched per device as each
    # one appears — including on reconnect, not only at process start.
    limbs = tuple(sorted(settings.limb_map.values()))
    restorer = BiomechRestorer(settings, limbs)

    def on_tick(tick: TickInput) -> None:
        device = registry.devices.get(tick.device_id)
        if device is None:
            return
        try:
            tick.metrics = biomech.compute(tick.frames, device.user_state, tick.times)
        except Exception:  # noqa: BLE001
            # This runs inside the device's ticker task; an escaping exception
            # would kill that task permanently and the device would silently go
            # dark. Degrade to a held tick instead and keep the stream alive.
            log.exception("device %d: biomech.compute failed — emitting held tick",
                          tick.device_id)
            tick.metrics = device.last_metrics or biomech.HELD_ZERO
        device.last_metrics = tick.metrics
        device.quality_ema = (
            tick.quality if device.quality_ema is None
            else 0.9 * device.quality_ema + 0.1 * tick.quality
        )
        publisher.publish_tick(tick, tick.metrics)

    ticker_manager = TickerManager(settings, on_tick)

    def on_new_device(device) -> None:
        restorer.device_added(device)      # before the ticker: restore, then tick
        ticker_manager.device_added(device)

    registry.on_new_device = on_new_device
    registry.on_device_removed = ticker_manager.device_removed

    transport = await start_udp_server(settings.udp_port, buf, udp_counters)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # not available on Windows
            loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(drain_loop(buf, registry), name="drain"),
        asyncio.create_task(
            stats_loop(registry, udp_counters,
                       evict_after_s=settings.session_gap_s),
            name="stats"),
        asyncio.create_task(publisher.stats_loop(), name="redis-stats"),
    ]
    try:
        await stop.wait()
    finally:
        log.info("shutting down")
        transport.close()
        await ticker_manager.shutdown()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await restorer.close()
        await publisher.close()


def main() -> None:
    try:
        import uvloop  # noqa: PLC0415
        uvloop.install()
        log.info("uvloop installed")
    except ImportError:
        log.info("uvloop unavailable (native Windows?) — using default loop")
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
