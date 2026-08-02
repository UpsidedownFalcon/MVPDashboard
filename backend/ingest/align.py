"""Timestamp unwrap + clock alignment (TRD §4 steps 2-3) — pure logic, no I/O.

Per sensor, u32 µs device timestamps are unwrapped to monotonic i64. Per
(device, source), a SourceClock maps device time onto server time using
`offset = server_recv_time - device_ts_seconds` tracked as a rolling minimum
(the least-queued packets) over a ~2s window, with slow drift handled by
re-evaluating the window minimum as it slides. An offset jump larger than
RESET_OFFSET_JUMP_S sustained for more than RESET_CONSECUTIVE samples means the
leg MCU rebooted: all per-source state resets.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

U32_SPAN = 2**32
WRAP_THRESHOLD = 2**31
OFFSET_WINDOW_S = 2.0
RESET_CONSECUTIVE = 10   # ">10 consecutive samples" (TRD §4 step 3)


class TimestampUnwrapper:
    """Per-sensor u32 -> i64 µs unwrap (decrease > 2^31 means +2^32)."""

    def __init__(self) -> None:
        self._epoch = 0
        self._last_raw: int | None = None

    def unwrap(self, ts_raw: np.ndarray) -> np.ndarray:
        ts = ts_raw.astype(np.int64)
        prev = self._last_raw if self._last_raw is not None else int(ts[0])
        deltas = np.diff(ts, prepend=np.int64(prev))
        wraps = np.cumsum(deltas < -WRAP_THRESHOLD)
        out = ts + (self._epoch + wraps) * U32_SPAN
        self._epoch += int(wraps[-1])
        self._last_raw = int(ts[-1])
        return out

    def reset(self) -> None:
        self._epoch = 0
        self._last_raw = None


@dataclass
class _WindowEntry:
    recv_time: float
    min_delta: float


class SourceClock:
    """Rolling-minimum offset estimator for one (device, source)."""

    def __init__(self, reset_jump_s: float = 5.0, window_s: float = OFFSET_WINDOW_S) -> None:
        self._reset_jump_s = reset_jump_s
        self._window_s = window_s
        self._window: deque[_WindowEntry] = deque()
        self.offset: float | None = None
        self._outlier_run = 0
        self.resets = 0

    def observe(self, recv_time: float, ts_unwrapped_us: np.ndarray) -> bool:
        """Feed one chunk; returns True if a reboot was detected (state was reset).

        The caller must then also reset the unwrappers/jitter buffers for this
        source and re-feed the chunk (fresh epoch starts with it).
        """
        ts_s = ts_unwrapped_us.astype(np.float64) * 1e-6
        deltas = recv_time - ts_s

        if self.offset is not None:
            outliers = np.abs(deltas - self.offset) > self._reset_jump_s
            if outliers.all():
                self._outlier_run += len(deltas)
            else:
                # run continues only through a trailing outlier streak
                trailing = 0
                for flag in outliers[::-1]:
                    if not flag:
                        break
                    trailing += 1
                self._outlier_run = trailing
            if self._outlier_run > RESET_CONSECUTIVE:
                self.reset()
                self.resets += 1
                return True
            if outliers.all():
                # suspected reboot in progress: keep outliers out of the offset window
                return False
            deltas = deltas[~outliers]

        self._window.append(_WindowEntry(recv_time, float(deltas.min())))
        while self._window and self._window[0].recv_time < recv_time - self._window_s:
            self._window.popleft()
        self.offset = min(entry.min_delta for entry in self._window)
        return False

    def server_time(self, ts_unwrapped_us: np.ndarray) -> np.ndarray:
        """Map unwrapped device µs to server epoch seconds (float64)."""
        if self.offset is None:
            raise RuntimeError("server_time() before any observe()")
        return ts_unwrapped_us.astype(np.float64) * 1e-6 + self.offset

    def reset(self) -> None:
        self._window.clear()
        self.offset = None
        self._outlier_run = 0


class SourceAligner:
    """One (device, source): shared clock + per-sensor unwrappers.

    process() returns (ts_unwrapped_us, server_time_seconds, was_reset). On a
    detected reboot all per-source state is cleared and the chunk is
    re-processed against the fresh epoch, so the caller only needs to clear its
    jitter buffers when was_reset is True.
    """

    def __init__(self, reset_jump_s: float = 5.0) -> None:
        self._reset_jump_s = reset_jump_s
        self._unwrappers: dict[int, TimestampUnwrapper] = {}
        self.clock = SourceClock(reset_jump_s)

    def _unwrapper(self, sensor_id: int) -> TimestampUnwrapper:
        u = self._unwrappers.get(sensor_id)
        if u is None:
            u = self._unwrappers[sensor_id] = TimestampUnwrapper()
        return u

    def process(
        self, sensor_id: int, recv_time: float, ts_raw: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, bool]:
        ts_unwrapped = self._unwrapper(sensor_id).unwrap(ts_raw)
        was_reset = self.clock.observe(recv_time, ts_unwrapped)
        if was_reset:
            for u in self._unwrappers.values():
                u.reset()
            ts_unwrapped = self._unwrapper(sensor_id).unwrap(ts_raw)
            self.clock.observe(recv_time, ts_unwrapped)
        return ts_unwrapped, self.clock.server_time(ts_unwrapped), was_reset
