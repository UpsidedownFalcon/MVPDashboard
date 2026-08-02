"""S1-T08 tests: ordered release, late/dup drops, bounded memory."""

from __future__ import annotations

import numpy as np

from ingest.jitter import JitterBuffer, default_capacity

WINDOW_MS = 50.0


def _mk(ts_list, base_server=1000.0):
    ts = np.array(ts_list, dtype=np.int64)
    server = base_server + ts.astype(np.float64) * 1e-6
    imu = np.tile(np.arange(6, dtype=np.int16), (len(ts), 1))
    for i, t in enumerate(ts_list):
        imu[i, 0] = t % 32000  # tag rows so order is checkable
    return ts, server, imu


def test_shuffled_input_released_in_order() -> None:
    buf = JitterBuffer(WINDOW_MS, capacity=1000)
    rng = np.random.default_rng(5)
    ts_sorted = np.arange(100, dtype=np.int64) * 1666
    shuffled = ts_sorted.copy()
    rng.shuffle(shuffled)
    ts, server, imu = _mk(shuffled.tolist())
    buf.insert_chunk(ts, server, imu)

    released = buf.release(now=1000.0 + 100 * 1666e-6 + 1.0)  # everything past window
    out_ts = [r[0] for r in released]
    assert out_ts == sorted(out_ts) == ts_sorted.tolist()
    # payload rows travel with their timestamps
    for r_ts, _, r_imu in released:
        assert r_imu[0] == r_ts % 32000


def test_window_holds_recent_samples() -> None:
    buf = JitterBuffer(WINDOW_MS, capacity=1000)
    ts, server, imu = _mk([0, 1666, 3332])
    buf.insert_chunk(ts, server, imu)
    # 'now' just after the first sample's server time + window: only it releases
    released = buf.release(now=1000.0 + 0.0505)
    assert [r[0] for r in released] == [0]
    assert len(buf) == 2


def test_late_arrival_dropped_and_counted() -> None:
    buf = JitterBuffer(WINDOW_MS, capacity=1000)
    ts, server, imu = _mk([0, 1666, 3332])
    buf.insert_chunk(ts, server, imu)
    buf.release(now=2000.0)  # all released

    late_ts, late_server, late_imu = _mk([1000])  # older than last released (3332)
    buf.insert_chunk(late_ts, late_server, late_imu)
    assert buf.late_drop == 1
    assert len(buf) == 0


def test_duplicates_dropped() -> None:
    buf = JitterBuffer(WINDOW_MS, capacity=1000)
    ts, server, imu = _mk([0, 1666, 1666, 3332])
    buf.insert_chunk(ts, server, imu)
    assert len(buf) == 3
    assert buf.dup_drop == 1
    # duplicate of an already-released ts also counted
    buf.release(now=2000.0)
    ts2, server2, imu2 = _mk([3332])
    buf.insert_chunk(ts2, server2, imu2)
    assert buf.dup_drop == 2


def test_memory_bounded_drop_oldest() -> None:
    cap = default_capacity(600, WINDOW_MS)  # 2 * 600 * 0.05 = 60 -> floor 64
    buf = JitterBuffer(WINDOW_MS, capacity=cap)
    n = cap + 40
    ts, server, imu = _mk((np.arange(n, dtype=np.int64) * 1666).tolist())
    buf.insert_chunk(ts, server, imu)
    assert len(buf) == cap
    assert buf.buf_drop == 40
    # survivors are the newest cap samples, still in order
    released = buf.release(now=1e9)
    out_ts = [r[0] for r in released]
    assert out_ts == sorted(out_ts)
    assert out_ts[0] == 40 * 1666


def test_reset_clears_state() -> None:
    buf = JitterBuffer(WINDOW_MS, capacity=100)
    ts, server, imu = _mk([0, 1666])
    buf.insert_chunk(ts, server, imu)
    buf.release(now=2000.0)
    buf.reset()
    # post-reset, an "old" timestamp from the fresh epoch is accepted again
    ts2, server2, imu2 = _mk([100])
    buf.insert_chunk(ts2, server2, imu2)
    assert len(buf) == 1
    assert buf.late_drop == 0
