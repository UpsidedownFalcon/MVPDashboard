"""UDP server + drain loop — the hot path (TRD §4 step 1).

datagram_received does nothing but append to a bounded deque; a separate task
drains and decodes in numpy batches every ~10ms.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections import deque
from dataclasses import dataclass

from common import packet

log = logging.getLogger("ingest.udp")

RAW_BUF_MAXLEN = 32768          # ~2s+ of raw datagrams at 12k pkt/s
SO_RCVBUF_BYTES = 4 * 1024 * 1024
DRAIN_INTERVAL_S = 0.010


@dataclass
class UdpCounters:
    buf_drop: int = 0           # raw-deque overflow (drop-oldest)


class IngestProtocol(asyncio.DatagramProtocol):
    """Receive callback kept trivial: length-agnostic append, nothing else."""

    def __init__(self, buf: deque[bytes], counters: UdpCounters) -> None:
        self._buf = buf
        self._counters = counters

    def datagram_received(self, data: bytes, addr: tuple) -> None:  # noqa: ARG002
        buf = self._buf
        if len(buf) >= RAW_BUF_MAXLEN:
            self._counters.buf_drop += 1  # deque(maxlen=...) evicts the oldest
        buf.append(data)

    def error_received(self, exc: OSError) -> None:
        log.warning("UDP error: %s", exc)


async def start_udp_server(
    port: int, buf: deque[bytes], counters: UdpCounters, host: str = "0.0.0.0"
) -> asyncio.DatagramTransport:
    """Bind the UDP socket with a raised SO_RCVBUF and start the protocol."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SO_RCVBUF_BYTES)
    achieved = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    sock.setblocking(False)
    sock.bind((host, port))
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: IngestProtocol(buf, counters), sock=sock
    )
    log.info("UDP listening on %s:%d (SO_RCVBUF requested=%d achieved=%d)",
             host, port, SO_RCVBUF_BYTES, achieved)
    return transport


async def drain_loop(buf: deque[bytes], router, interval: float = DRAIN_INTERVAL_S) -> None:
    """Every ~10ms: swap the deque contents out, batch-decode, hand to the router."""
    while True:
        if buf:
            payloads = list(buf)
            buf.clear()
            batch = packet.decode(payloads)
            router.route(batch, recv_time=time.time())
        await asyncio.sleep(interval)
