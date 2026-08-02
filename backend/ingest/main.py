"""Ingest service entrypoint (real, S1-T06) — UDP -> decode -> route + stats log.

Alignment/jitter/ticker/biomech/publish attach in T07-T11.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections import deque

from common.config import get_settings
from ingest.state import Registry
from ingest.udp import RAW_BUF_MAXLEN, UdpCounters, drain_loop, start_udp_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ingest")

STATS_INTERVAL_S = 1.0


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
    log.info("ingest starting: udp_port=%d expected_input_hz=%s output_hz=%d",
             settings.udp_port, settings.expected_input_hz, settings.output_hz)

    buf: deque[bytes] = deque(maxlen=RAW_BUF_MAXLEN)
    udp_counters = UdpCounters()
    registry = Registry()

    transport = await start_udp_server(settings.udp_port, buf, udp_counters)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # not available on Windows
            loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(drain_loop(buf, registry), name="drain"),
        asyncio.create_task(stats_loop(registry, udp_counters), name="stats"),
    ]
    try:
        await stop.wait()
    finally:
        log.info("shutting down")
        transport.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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
