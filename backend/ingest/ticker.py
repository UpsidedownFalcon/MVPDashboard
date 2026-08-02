"""60Hz per-device ticker + limb framing + quality (TRD §4 steps 5, 7, 8).

One DeviceTicker per device, started on first packet. Wall-clock absolute
scheduling (next_t = start + k/OUTPUT_HZ) so cadence never drifts. Each tick
drains the device's pending sample chunks through align -> jitter, releases
in-order samples, groups them into limb frames and emits a TickInput to the
callback (biomech in T10). Emits every tick regardless of input (held ticks);
suspends after OFFLINE_AFTER_S without packets, resumes + resets on traffic.

Clock/sleep are injectable for deterministic tests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import numpy as np

from common.config import Settings
from ingest.align import SourceAligner
from ingest.jitter import JitterBuffer, default_capacity
from ingest.state import DeviceState

log = logging.getLogger("ingest.ticker")


@dataclass
class TickInput:
    device_id: int
    t_server: float                 # wall-clock tick time (unix seconds)
    frames: dict[str, np.ndarray]   # limb -> float32[n, 6] raw counts
    quality: float                  # 0..1 share of expected samples
    held: bool                      # True when no fresh samples this tick
    metrics: object | None = None   # Metrics, attached by the biomech callback (T10)


class DeviceTicker:
    """Owns align/jitter state and the 60Hz emit loop for one device."""

    def __init__(
        self,
        device: DeviceState,
        settings: Settings,
        callback,                    # callable(TickInput) -> None
        now_fn=time.time,
        sleep_fn=asyncio.sleep,
    ) -> None:
        self._device = device
        self._settings = settings
        self._callback = callback
        self._now = now_fn
        self._sleep = sleep_fn
        self._period = 1.0 / settings.output_hz
        self._expected_per_tick = (
            4.0 * settings.expected_input_hz / settings.output_hz
        )
        self._aligners: dict[int, SourceAligner] = {}
        self._jitters: dict[tuple[int, int], JitterBuffer] = {}
        self._unknown_keys_logged: set[tuple[int, int]] = set()
        self.resets = 0
        self.suspended = False

    # --- pipeline plumbing ------------------------------------------------------

    def _aligner(self, source_id: int) -> SourceAligner:
        a = self._aligners.get(source_id)
        if a is None:
            a = self._aligners[source_id] = SourceAligner(
                reset_jump_s=self._settings.reset_offset_jump_s
            )
        return a

    def _jitter(self, source_id: int, sensor_id: int) -> JitterBuffer:
        key = (source_id, sensor_id)
        j = self._jitters.get(key)
        if j is None:
            j = self._jitters[key] = JitterBuffer(
                self._settings.jitter_buffer_ms,
                default_capacity(
                    self._settings.expected_input_hz, self._settings.jitter_buffer_ms
                ),
            )
        return j

    def _process_pending(self) -> None:
        """Drain routed chunks through align -> jitter for all sensors."""
        for (src, sen), sensor in list(self._device.sensors.items()):
            chunks = sensor.drain_pending()
            if not chunks:
                continue
            aligner = self._aligner(src)
            jitter = self._jitter(src, sen)
            for chunk in chunks:
                ts_unwrapped, server_t, was_reset = aligner.process(
                    sen, chunk.recv_time, chunk.ts_us
                )
                if was_reset:
                    self.resets += 1
                    log.warning(
                        "device %d source %d: clock reset (reboot?) — buffers cleared",
                        self._device.device_id, src,
                    )
                    for (jsrc, jsen), jbuf in self._jitters.items():
                        if jsrc == src:
                            jbuf.reset()
                jitter.insert_chunk(ts_unwrapped, server_t, chunk.imu)
            # surface jitter counters on the sensor stats
            sensor.stats.late_drop = jitter.late_drop
            sensor.stats.buf_drop = jitter.buf_drop

    def _tick(self, t: float) -> TickInput:
        self._process_pending()
        limb_map = self._settings.limb_map
        frames: dict[str, np.ndarray] = {
            limb: np.empty((0, 6), dtype=np.float32) for limb in limb_map.values()
        }
        total = 0
        for (src, sen), jitter in self._jitters.items():
            released = jitter.release(t)
            limb = limb_map.get((src, sen))
            if limb is None:
                if (src, sen) not in self._unknown_keys_logged:
                    self._unknown_keys_logged.add((src, sen))
                    log.warning("device %d: sensor (%d,%d) not in LIMB_MAP — ignored",
                                self._device.device_id, src, sen)
                continue
            if released:
                frames[limb] = np.array([r[2] for r in released], dtype=np.float32)
                total += len(released)
        quality = min(max(total / self._expected_per_tick, 0.0), 1.0)
        held = total == 0
        tick = TickInput(
            device_id=self._device.device_id,
            t_server=t,
            frames=frames,
            quality=quality,
            held=held,
        )
        self._device.ticks_out += 1
        self._callback(tick)
        return tick

    def _reset_pipeline(self) -> None:
        """Device came back after being offline: start from clean state."""
        self._aligners.clear()
        for jitter in self._jitters.values():
            jitter.reset()

    # --- run loop ---------------------------------------------------------------

    async def run(self) -> None:
        offline_after = self._settings.offline_after_s
        start = self._now()
        k = 0
        while True:
            k += 1
            next_t = start + k * self._period
            delay = next_t - self._now()
            if delay > 0:
                await self._sleep(delay)

            silent_for = next_t - self._device.last_seen
            if silent_for > offline_after:
                if not self.suspended:
                    self.suspended = True
                    log.info("device %d: offline (silent %.1fs) — ticker suspended",
                             self._device.device_id, silent_for)
                # idle-poll until traffic returns, then reset state + tick epoch
                while self._now() - self._device.last_seen > offline_after:
                    await self._sleep(self._period)
                self.suspended = False
                self._reset_pipeline()
                start = self._now()
                k = 0
                log.info("device %d: back online — ticker resumed (state reset)",
                         self._device.device_id)
                continue

            self._tick(next_t)


class TickerManager:
    """Creates a DeviceTicker task per device on first packet."""

    def __init__(self, settings: Settings, callback) -> None:
        self._settings = settings
        self._callback = callback
        self.tickers: dict[int, DeviceTicker] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    def device_added(self, device: DeviceState) -> None:
        ticker = DeviceTicker(device, self._settings, self._callback)
        self.tickers[device.device_id] = ticker
        self._tasks[device.device_id] = asyncio.create_task(
            ticker.run(), name=f"ticker-dev{device.device_id}"
        )

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
