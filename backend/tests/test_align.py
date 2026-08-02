"""S1-T07 tests: unwrap, offset recovery, drift tracking, wraparound, reboot."""

from __future__ import annotations

import numpy as np

from ingest.align import (
    RESET_CONSECUTIVE,
    U32_SPAN,
    SourceAligner,
    SourceClock,
    TimestampUnwrapper,
)

TOL_S = 0.002  # spec: recover server time within ±2ms


# --- unwrap --------------------------------------------------------------------

def test_unwrap_monotonic_within_chunk() -> None:
    u = TimestampUnwrapper()
    ts = np.array([100, 200, 300], dtype=np.uint32)
    out = u.unwrap(ts)
    assert out.tolist() == [100, 200, 300]


def test_unwrap_across_wrap_boundary() -> None:
    u = TimestampUnwrapper()
    ts = np.array([U32_SPAN - 100, U32_SPAN - 50, 10, 60], dtype=np.uint64).astype(np.uint32)
    out = u.unwrap(ts)
    assert out.tolist() == [U32_SPAN - 100, U32_SPAN - 50, U32_SPAN + 10, U32_SPAN + 60]


def test_unwrap_across_chunks() -> None:
    u = TimestampUnwrapper()
    first = u.unwrap(np.array([U32_SPAN - 10], dtype=np.uint64).astype(np.uint32))
    second = u.unwrap(np.array([5], dtype=np.uint32))
    assert first.tolist() == [U32_SPAN - 10]
    assert second.tolist() == [U32_SPAN + 5]


def test_small_backwards_step_is_not_a_wrap() -> None:
    u = TimestampUnwrapper()
    out = u.unwrap(np.array([1000, 900, 1100], dtype=np.uint32))
    assert out.tolist() == [1000, 900, 1100]  # reorder, not a wrap


# --- synthetic stream helper ---------------------------------------------------

def _feed_stream(
    clock: SourceClock,
    true_offset: float,
    n_chunks: int = 100,
    chunk_len: int = 6,
    period_us: float = 1666.0,
    drift_ppm: float = 0.0,
    delay_fn=None,
    start_us: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Simulate chunks arriving; returns (all_ts_us, all_true_server_times)."""
    rng = np.random.default_rng(3)
    skew = 1.0 + drift_ppm * 1e-6
    all_ts, all_true = [], []
    k = 0
    for _ in range(n_chunks):
        idx = np.arange(k, k + chunk_len, dtype=np.float64)
        k += chunk_len
        device_ts_us = start_us + idx * period_us * skew
        true_server = true_offset + idx * period_us * 1e-6  # true wall time of the samples
        queue_delay = delay_fn(rng) if delay_fn else float(rng.uniform(0.0002, 0.0015))
        recv_time = float(true_server[-1]) + queue_delay
        clock.observe(recv_time, device_ts_us.astype(np.int64))
        all_ts.append(device_ts_us.astype(np.int64))
        all_true.append(true_server)
    return np.concatenate(all_ts), np.concatenate(all_true)


def test_clock_recovers_known_offset() -> None:
    clock = SourceClock()
    true_offset = 1_700_000_000.0  # device ts starts at 0 at this wall time
    ts, true_server = _feed_stream(clock, true_offset)
    mapped = clock.server_time(ts)
    err = np.abs(mapped - true_server)
    # after warm-up (first ~2s window), mapping is within tolerance
    assert float(err[len(err) // 2 :].max()) <= TOL_S


def test_clock_tracks_slow_drift() -> None:
    clock = SourceClock()
    true_offset = 1_700_000_000.0
    # 200ppm device-fast clock over ~10s of stream time
    ts, true_server = _feed_stream(clock, true_offset, n_chunks=1000, drift_ppm=200.0)
    mapped = clock.server_time(ts[-6:])
    err = np.abs(mapped - true_server[-6:])
    # drift accumulated ~2ms over the run; rolling window keeps error bounded
    assert float(err.max()) <= TOL_S + 0.001
    assert clock.resets == 0


def test_reboot_detected_and_reset() -> None:
    clock = SourceClock(reset_jump_s=5.0)
    # device has been up ~200s when we first hear it
    _feed_stream(clock, true_offset=1_700_000_000.0, n_chunks=50, start_us=200e6)
    assert clock.resets == 0

    # device reboots: ts restarts near zero => delta jumps by ~200s (device uptime)
    rebooted = 0
    for chunk in range(5):
        ts = np.arange(chunk * 6, chunk * 6 + 6, dtype=np.int64) * 1666
        recv = 1_700_000_000.5 + chunk * 0.01
        if clock.observe(recv, ts):
            rebooted += 1
            break
    assert rebooted == 1
    assert clock.offset is None  # state cleared, ready for fresh epoch


def test_aligner_reprocesses_after_reset_and_flags() -> None:
    aligner = SourceAligner(reset_jump_s=5.0)
    period_us = 1666
    base_wall = 1_700_000_000.0
    uptime_us = 200_000_000  # device had been running ~200s before we hear it
    # warm up with sensor 1
    for chunk in range(40):
        ts = (uptime_us + np.arange(chunk * 6, chunk * 6 + 6) * period_us).astype(np.uint32)
        wall = base_wall + (chunk * 6 + 5) * period_us * 1e-6 + 0.001
        _, server, was_reset = aligner.process(1, wall, ts)
        assert not was_reset

    # reboot: timestamps restart, arrival continues
    saw_reset = False
    for chunk in range(5):
        ts = (np.arange(chunk * 6, chunk * 6 + 6) * period_us).astype(np.uint32)
        wall = base_wall + 0.5 + chunk * 0.01
        _, server, was_reset = aligner.process(1, wall, ts)
        saw_reset = saw_reset or was_reset
        if was_reset:
            # post-reset mapping is against the fresh epoch: near current wall time
            assert abs(float(server[-1]) - wall) < 0.1
    assert saw_reset
    assert aligner.clock.resets == 1


def test_two_sensors_share_source_clock() -> None:
    aligner = SourceAligner()
    period_us = 1666
    base_wall = 1_700_000_000.0
    for chunk in range(40):
        ts = (np.arange(chunk * 6, chunk * 6 + 6) * period_us).astype(np.uint32)
        wall = base_wall + (chunk * 6 + 5) * period_us * 1e-6 + 0.001
        _, s1, _ = aligner.process(1, wall, ts)
        _, s2, _ = aligner.process(2, wall, ts)
        # same device timestamps on the same source map to the same server time
        assert np.allclose(s1, s2)
