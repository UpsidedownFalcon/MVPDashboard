"""S1-T06 tests: UDP receive -> drain -> route, counters, drop-oldest raw buffer."""

from __future__ import annotations

import asyncio
import socket
from collections import deque

import pytest

from common import packet
from ingest.state import Registry, SampleChunk
from ingest.udp import RAW_BUF_MAXLEN, IngestProtocol, UdpCounters, drain_loop, start_udp_server


def _payload(device_id=30, source_id=0, sensor_id=1, ts_us=1000, imu6=(1, 2, 3, 4, 5, 6)):
    return packet.encode(device_id, source_id, sensor_id, ts_us, imu6)


async def _wait_until(predicate, timeout=2.0, step=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return predicate()


async def test_udp_end_to_end_routing():
    buf: deque[bytes] = deque(maxlen=RAW_BUF_MAXLEN)
    counters = UdpCounters()
    registry = Registry()
    transport = await start_udp_server(0, buf, counters, host="127.0.0.1")
    port = transport.get_extra_info("sockname")[1]
    drain = asyncio.create_task(drain_loop(buf, registry, interval=0.01))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 2 devices x 2 sensors, plus one corrupt-CRC and one bad-sync datagram
        for ts in range(100):
            sock.sendto(_payload(30, 0, 1, ts * 1666), ("127.0.0.1", port))
            sock.sendto(_payload(30, 1, 2, ts * 1666), ("127.0.0.1", port))
            sock.sendto(_payload(31, 0, 1, ts * 1666), ("127.0.0.1", port))
        bad_crc = bytearray(_payload())
        bad_crc[20] ^= 0xFF
        sock.sendto(bytes(bad_crc), ("127.0.0.1", port))
        bad_sync = bytearray(_payload())
        bad_sync[2] = 0x00
        sock.sendto(bytes(bad_sync), ("127.0.0.1", port))
        sock.sendto(b"junk-wrong-length", ("127.0.0.1", port))

        assert await _wait_until(
            lambda: 30 in registry.devices and 31 in registry.devices
            and registry.devices[30].sensor(0, 1).stats.recv >= 100
            and registry.devices[30].sensor(1, 2).stats.recv >= 100
            and registry.devices[31].sensor(0, 1).stats.recv >= 100
            and registry.crc_fail >= 1 and registry.bad_sync >= 1
            and registry.bad_len >= 1
        ), "expected all datagrams routed/counted"

        # routed chunks carry the right samples
        chunks = registry.devices[30].sensor(0, 1).drain_pending()
        total = sum(len(c.ts_us) for c in chunks)
        assert total == 100
        assert all(c.imu.shape[1] == 6 for c in chunks)
    finally:
        drain.cancel()
        transport.close()
        await asyncio.gather(drain, return_exceptions=True)


def test_raw_buffer_drop_oldest_counts():
    buf: deque[bytes] = deque(maxlen=RAW_BUF_MAXLEN)
    counters = UdpCounters()
    proto = IngestProtocol(buf, counters)
    for i in range(RAW_BUF_MAXLEN + 500):
        proto.datagram_received(b"x", ("127.0.0.1", 1))
    assert len(buf) == RAW_BUF_MAXLEN
    assert counters.buf_drop == 500


def test_registry_rate_update():
    registry = Registry()
    import numpy as np
    batch = packet.decode([_payload(30, 0, 1, ts) for ts in range(600)])
    registry.route(batch, recv_time=100.0)
    registry.update_rates(1.0)
    assert registry.devices[30].sensor(0, 1).stats.rate_hz == 600.0
    registry.update_rates(1.0)
    assert registry.devices[30].sensor(0, 1).stats.rate_hz == 0.0
