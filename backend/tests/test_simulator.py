"""S1-T05 tests: simulator emits decodable payloads with correct ids and rates."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from simulator.simulate import STREAM_KEYS, DeviceSim  # noqa: E402

from common import packet  # noqa: E402


def _fake_streams(n: int = 50) -> dict[tuple[int, int], np.ndarray]:
    rng = np.random.default_rng(7)
    return {key: rng.integers(-1000, 1000, size=(n, 6), dtype=np.int16)
            for key in STREAM_KEYS}


def test_emitted_payloads_decode_with_correct_ids_and_rates() -> None:
    rate = 600.0
    dev = DeviceSim(device_id=31, streams=_fake_streams(), rate=rate,
                    drift_ppm=0.0, rng=random.Random(1), start=0.0)

    payloads: list[bytes] = []
    now = 0.0
    while len(payloads) < 100:
        now += 0.005
        payloads.extend(dev.due_payloads(now))

    batch = packet.decode(payloads)
    assert batch.n == batch.n_in == len(payloads) >= 100
    assert batch.n_bad_len == batch.n_bad_sync == batch.n_bad_crc == 0
    assert set(batch.device_id.tolist()) == {31}
    assert set(batch.version.tolist()) == {packet.VERSION}

    seen_keys = set(zip(batch.source_id.tolist(), batch.sensor_id.tolist()))
    assert seen_keys == set(STREAM_KEYS)

    # per-stream timestamp deltas match the sample period (1e6/rate microseconds)
    expected_period_us = 1e6 / rate
    for src, sen in STREAM_KEYS:
        mask = (batch.source_id == src) & (batch.sensor_id == sen)
        ts = batch.ts_us[mask].astype(np.int64)
        deltas = np.diff(ts)
        deltas = deltas[deltas > 0]  # ignore a potential u32 wrap
        assert deltas.size > 0
        assert abs(float(np.median(deltas)) - expected_period_us) <= 2.0

    # ~25ms of wall time per stream at 600Hz -> about 15 samples each
    per_stream = len(payloads) / len(STREAM_KEYS)
    assert per_stream * (1.0 / rate) <= now + 0.01


def test_drift_skews_sources_apart() -> None:
    dev = DeviceSim(device_id=30, streams=_fake_streams(), rate=600.0,
                    drift_ppm=1000.0, rng=random.Random(2), start=0.0)
    payloads: list[bytes] = []
    now = 0.0
    for _ in range(100):
        now += 0.005
        payloads.extend(dev.due_payloads(now))
    batch = packet.decode(payloads)

    periods = {}
    for src in (0, 1):
        mask = (batch.source_id == src) & (batch.sensor_id == 1)
        ts = batch.ts_us[mask].astype(np.int64)
        deltas = np.diff(ts)
        periods[src] = float(np.median(deltas[deltas > 0]))
    # source 0 runs fast (+500ppm), source 1 slow (-500ppm)
    assert periods[0] > periods[1]
