"""Per-sensor jitter buffer (TRD §4 step 4) — pure logic, no I/O.

Samples are held for JITTER_BUFFER_MS keyed by unwrapped device timestamp,
releasing reordered data in order. Late arrivals (older than the last released
sample) and duplicates are dropped and counted. Memory is bounded: beyond
~2x expected in-window occupancy the oldest sample is dropped (never blocks).
"""

from __future__ import annotations

import heapq
import math

import numpy as np


def default_capacity(expected_hz: float, window_ms: float) -> int:
    """~2x expected rate x window, with a small floor for tiny test windows."""
    return max(int(math.ceil(2.0 * expected_hz * (window_ms / 1000.0))), 64)


class JitterBuffer:
    """One sensor's reorder buffer. Insert chunks; release in timestamp order."""

    def __init__(self, window_ms: float, capacity: int) -> None:
        self._window_s = window_ms / 1000.0
        self._capacity = capacity
        self._heap: list[tuple[int, float, np.ndarray]] = []  # (ts_us, server_t, imu6)
        self._in_heap: set[int] = set()
        self._last_released_ts: int = -(2**63)
        self.late_drop = 0
        self.buf_drop = 0
        self.dup_drop = 0

    def __len__(self) -> int:
        return len(self._heap)

    def insert_chunk(
        self, ts_us: np.ndarray, server_time: np.ndarray, imu: np.ndarray
    ) -> None:
        for i in range(len(ts_us)):
            self._insert(int(ts_us[i]), float(server_time[i]), imu[i])

    def _insert(self, ts_us: int, server_time: float, imu6: np.ndarray) -> None:
        if ts_us <= self._last_released_ts:
            if ts_us == self._last_released_ts:
                self.dup_drop += 1
            else:
                self.late_drop += 1
            return
        if ts_us in self._in_heap:
            self.dup_drop += 1
            return
        if len(self._heap) >= self._capacity:
            old_ts, _, _ = heapq.heappop(self._heap)
            self._in_heap.discard(old_ts)
            self.buf_drop += 1
        heapq.heappush(self._heap, (ts_us, server_time, imu6))
        self._in_heap.add(ts_us)

    def release(self, now: float) -> list[tuple[int, float, np.ndarray]]:
        """Pop everything whose mapped server time < now - window, in ts order."""
        cutoff = now - self._window_s
        out: list[tuple[int, float, np.ndarray]] = []
        heap = self._heap
        while heap and heap[0][1] < cutoff:
            item = heapq.heappop(heap)
            self._in_heap.discard(item[0])
            self._last_released_ts = item[0]
            out.append(item)
        return out

    def reset(self) -> None:
        """Source rebooted: forget everything (fresh epoch has new timestamps)."""
        self._heap.clear()
        self._in_heap.clear()
        self._last_released_ts = -(2**63)
