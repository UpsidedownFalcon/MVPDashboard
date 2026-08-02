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


async def stats_loop(registry: Registry, udp_counters: UdpCounters,
                     interval: float = STATS_INTERVAL_S) -> None:
    while True:
        await asyncio.sleep(interval)
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
    registry = Registry()
    publisher = Publisher(settings, registry)

    def on_tick(tick: TickInput) -> None:
        device = registry.devices.get(tick.device_id)
        if device is None:
            return
        tick.metrics = biomech.compute(tick.frames, device.user_state)
        device.quality_ema = (
            tick.quality if device.quality_ema is None
            else 0.9 * device.quality_ema + 0.1 * tick.quality
        )
        publisher.publish_tick(tick, tick.metrics)

    ticker_manager = TickerManager(settings, on_tick)
    registry.on_new_device = ticker_manager.device_added

    transport = await start_udp_server(settings.udp_port, buf, udp_counters)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # not available on Windows
            loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(drain_loop(buf, registry), name="drain"),
        asyncio.create_task(stats_loop(registry, udp_counters), name="stats"),
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
