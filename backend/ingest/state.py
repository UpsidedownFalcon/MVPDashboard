"""Per-device / per-sensor registry, routing and stats (TRD §4, S1-T06).

The router receives decoded Batches and distributes samples to SensorState
pending queues, auto-creating device/sensor state on first sight. Downstream
stages (align/jitter/ticker, T07-T09) consume the pending chunks.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from common.packet import Batch
from common.scaling import SAT_THRESHOLD_COUNTS

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
    pending_drop: int = 0    # pending-queue overflow (this file)
    jitter_drop: int = 0     # jitter-buffer capacity overflow, mirrored by the ticker
    sat_count: int = 0       # samples within 1% of full scale (biomech SPEC §3.7)
    _recv_at_last_rate: int = 0

    @property
    def buf_drop(self) -> int:
        """Total buffer-overflow drops for this sensor (BACKEND_SCHEMA §4).

        Two independent bounded buffers can overflow — the pending queue here
        and the jitter buffer downstream — and they overflow for the same
        reason: load. They are summed rather than sharing one field because the
        ticker mirrors the jitter buffer's running total by assignment, which
        used to overwrite the pending count and hide it exactly when the system
        was busy enough for it to matter.
        """
        return self.pending_drop + self.jitter_drop


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
            self.stats.pending_drop += len(dropped.ts_us)
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
        self.tick_rate: float = 0.0
        self.quality_ema: float | None = None
        self._ticks_at_last_rate = 0
        self.last_seen: float = 0.0
        self.user_state: dict = {}   # biomech session state (T15)
        self.last_metrics = None     # latest Metrics, for the 1s diag publish
        # Battery state of charge, per leg MCU: {source_id: 0..100}. The wire
        # carries one `soc` byte per datagram (TRD §3) and each source_id is a
        # separate MCU with its own battery, so they are tracked separately and
        # the UI shows the LOWEST -- a dying leg unit must not hide behind a
        # healthy one.
        self.soc: dict[int, int] = {}

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

    def __init__(self, max_devices: int | None = None) -> None:
        self.devices: dict[int, DeviceState] = {}
        # Bad records lose trustworthy identity, so malformed counters are global.
        self.crc_fail = 0
        self.bad_sync = 0
        self.bad_len = 0
        self.max_devices = max_devices
        self.offline_after_s = 2.0   # set from Settings by the caller
        self.dev_dropped = 0         # packets for devices beyond the cap
        self._capped_logged: set[int] = set()
        self.on_new_device = None       # optional callback(device: DeviceState)
        self.on_device_removed = None   # optional callback(device_id: int)

    def _remove(self, device_id: int, why: str) -> None:
        self.devices.pop(device_id, None)
        self._capped_logged.discard(device_id)
        log.info("device %d released (%s)", device_id, why)
        if self.on_device_removed is not None:
            self.on_device_removed(device_id)

    def device(self, device_id: int, now: float | None = None) -> DeviceState | None:
        """Existing device, or a new one — displacing an OFFLINE one if at cap.

        Returns None only when the cap is reached and every tracked device is
        still live; those packets are dropped and counted, never silently mixed
        into another device's stream (biomech SPEC §7.2).

        Displacing the longest-silent offline device matters for usability: a
        trainer swapping a wearable would otherwise wait out the whole session
        gap before the replacement could register.
        """
        state = self.devices.get(device_id)
        if state is not None:
            return state
        if self.max_devices is not None and len(self.devices) >= self.max_devices:
            now = time.time() if now is None else now
            offline = [(d.last_seen, i) for i, d in self.devices.items()
                       if not d.last_seen or now - d.last_seen > self.offline_after_s]
            if not offline:
                self.dev_dropped += 1
                if device_id not in self._capped_logged:
                    self._capped_logged.add(device_id)
                    log.warning("device %d ignored: MAX_DEVICES=%d, all live",
                                device_id, self.max_devices)
                return None
            self._remove(min(offline)[1], f"displaced by device {device_id}")
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
            device = self.device(device_id, now=recv_time)
            if device is None:
                continue
            device.last_seen = recv_time
            # Battery: the newest datagram in this slice wins. Cheap (one
            # array index) and it is the only place the decoded `soc` is still
            # in scope -- SampleChunk deliberately does not carry it, since it
            # is per-device telemetry, not a per-sample signal.
            if len(batch.soc):
                device.soc[source_id] = int(batch.soc[idx[-1]])
            sensor = device.sensor(source_id, sensor_id)
            imu = batch.imu[idx]
            # Clipping is unrecoverable and a clipped impact still matters, so
            # count it rather than dropping it; biomech suppresses m1/m2 only
            # once the saturated fraction gets high (SPEC §3.7).
            sensor.stats.sat_count += int(
                (np.abs(imu) >= SAT_THRESHOLD_COUNTS).any(axis=1).sum()
            )
            sensor.append(SampleChunk(
                recv_time=recv_time,
                ts_us=batch.ts_us[idx],
                imu=imu,
            ))

    def evict_stale(self, now: float, max_age_s: float) -> list[int]:
        """Release devices silent for longer than max_age_s; returns their ids.

        Without this, MAX_DEVICES counts devices that went offline hours ago and
        a genuinely new device is refused a slot forever. The eviction horizon is
        SESSION_GAP_S, which is also when biomech would discard the session's
        accumulated load anyway — so nothing recoverable is lost, and a device
        returning inside the snapshot TTL still restores its session (SPEC §7.4).
        """
        stale = [
            device_id for device_id, device in self.devices.items()
            if device.last_seen and now - device.last_seen > max_age_s
        ]
        for device_id in stale:
            self._remove(device_id, f"silent {max_age_s:.0f}s")
        return stale

    def update_rates(self, interval_s: float) -> None:
        """Recompute per-sensor rate_hz over the last stats interval."""
        for device in self.devices.values():
            device.tick_rate = (device.ticks_out - device._ticks_at_last_rate) / interval_s
            device._ticks_at_last_rate = device.ticks_out
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
            late = sum(s.stats.late_drop for s in device.sensors.values())
            quality = f"{device.quality_ema:.2f}" if device.quality_ema is not None else "-"
            lines.append(
                f"dev {device_id}: {' '.join(parts)} tick={device.tick_rate:5.1f}Hz"
                f" q={quality} ticks_out={device.ticks_out}"
                f" late_drop={late} buf_drop={drops}"
            )
        lines.append(
            f"global: crc_fail={self.crc_fail} bad_sync={self.bad_sync} bad_len={self.bad_len}"
        )
        return lines
