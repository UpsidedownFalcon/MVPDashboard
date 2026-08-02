"""Per-device / per-sensor registry, routing and stats (TRD §4, S1-T06).

The router receives decoded Batches and distributes samples to SensorState
pending queues, auto-creating device/sensor state on first sight. Downstream
stages (align/jitter/ticker, T07-T09) consume the pending chunks.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from common.packet import Batch

log = logging.getLogger("ingest.state")

# Cap on buffered-but-unconsumed sample chunks per sensor (~2s at 600Hz comes to
# ~200 drain chunks; keep headroom, drop-oldest beyond and count).
PENDING_MAXCHUNKS = 512


@dataclass
class SensorStats:
    recv: int = 0            # valid samples routed to this sensor
    rate_hz: float = 0.0     # measured over the last stats interval
    crc_fail: int = 0        # batch-level share, attributed by the router owner
    bad_sync: int = 0
    late_drop: int = 0       # filled by the jitter buffer (T08)
    buf_drop: int = 0        # pending-queue overflow
    _recv_at_last_rate: int = 0


@dataclass
class SampleChunk:
    """A drained batch slice for one sensor: shared recv_time, per-sample ts/imu."""

    recv_time: float         # server wall-clock when the chunk was drained
    ts_us: np.ndarray        # u32[n] raw device timestamps
    imu: np.ndarray          # int16[n, 6]


class SensorState:
    def __init__(self, source_id: int, sensor_id: int) -> None:
        self.source_id = source_id
        self.sensor_id = sensor_id
        self.stats = SensorStats()
        self.pending: deque[SampleChunk] = deque(maxlen=PENDING_MAXCHUNKS)
        self.last_seen: float = 0.0

    def append(self, chunk: SampleChunk) -> None:
        if len(self.pending) >= PENDING_MAXCHUNKS:
            dropped = self.pending[0]
            self.stats.buf_drop += len(dropped.ts_us)
        self.pending.append(chunk)
        self.stats.recv += len(chunk.ts_us)
        self.last_seen = chunk.recv_time

    def drain_pending(self) -> list[SampleChunk]:
        chunks = list(self.pending)
        self.pending.clear()
        return chunks


class DeviceState:
    def __init__(self, device_id: int) -> None:
        self.device_id = device_id
        self.sensors: dict[tuple[int, int], SensorState] = {}
        self.ticks_out = 0       # advanced by the ticker (T09)
        self.last_seen: float = 0.0
        self.user_state: dict = {}   # biomech scratch state (T10)

    def sensor(self, source_id: int, sensor_id: int) -> SensorState:
        key = (source_id, sensor_id)
        state = self.sensors.get(key)
        if state is None:
            state = self.sensors[key] = SensorState(source_id, sensor_id)
            log.info("device %d: new sensor (source=%d, sensor=%d)",
                     self.device_id, source_id, sensor_id)
        return state


class Registry:
    """All device state + global counters; owns batch routing."""

    def __init__(self) -> None:
        self.devices: dict[int, DeviceState] = {}
        # Bad records lose trustworthy identity, so malformed counters are global.
        self.crc_fail = 0
        self.bad_sync = 0
        self.bad_len = 0
        self.on_new_device = None    # optional callback(device: DeviceState)

    def device(self, device_id: int) -> DeviceState:
        state = self.devices.get(device_id)
        if state is None:
            state = self.devices[device_id] = DeviceState(device_id)
            log.info("new device: %d", device_id)
            if self.on_new_device is not None:
                self.on_new_device(state)
        return state

    def route(self, batch: Batch, recv_time: float) -> None:
        """Distribute one decoded batch to per-sensor pending queues (vectorized)."""
        self.crc_fail += batch.n_bad_crc
        self.bad_sync += batch.n_bad_sync
        self.bad_len += batch.n_bad_len
        if batch.n == 0:
            return

        key = (
            batch.device_id.astype(np.int64) << 16
            | batch.source_id.astype(np.int64) << 8
            | batch.sensor_id.astype(np.int64)
        )
        order = np.argsort(key, kind="stable")
        sorted_key = key[order]
        boundaries = np.nonzero(np.diff(sorted_key))[0] + 1
        starts = np.concatenate(([0], boundaries))
        ends = np.concatenate((boundaries, [len(sorted_key)]))

        for s, e in zip(starts, ends):
            idx = order[s:e]
            k = int(sorted_key[s])
            device_id, source_id, sensor_id = k >> 16, (k >> 8) & 0xFF, k & 0xFF
            device = self.device(device_id)
            device.last_seen = recv_time
            sensor = device.sensor(source_id, sensor_id)
            sensor.append(SampleChunk(
                recv_time=recv_time,
                ts_us=batch.ts_us[idx],
                imu=batch.imu[idx],
            ))

    def update_rates(self, interval_s: float) -> None:
        """Recompute per-sensor rate_hz over the last stats interval."""
        for device in self.devices.values():
            for sensor in device.sensors.values():
                st = sensor.stats
                st.rate_hz = (st.recv - st._recv_at_last_rate) / interval_s
                st._recv_at_last_rate = st.recv

    def summary_lines(self) -> list[str]:
        lines = []
        for device_id in sorted(self.devices):
            device = self.devices[device_id]
            parts = [
                f"s({src},{sen})={sensor.stats.rate_hz:5.0f}Hz"
                for (src, sen), sensor in sorted(device.sensors.items())
            ]
            drops = sum(s.stats.buf_drop for s in device.sensors.values())
            lines.append(
                f"dev {device_id}: {' '.join(parts)} ticks_out={device.ticks_out}"
                f" buf_drop={drops}"
            )
        lines.append(
            f"global: crc_fail={self.crc_fail} bad_sync={self.bad_sync} bad_len={self.bad_len}"
        )
        return lines
