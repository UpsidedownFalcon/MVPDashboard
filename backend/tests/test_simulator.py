"""S1-T05 tests: simulator emits decodable payloads with correct ids and rates."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from simulator.simulate import (  # noqa: E402
    CAPTURE_ACCEL_FS_G,
    CAPTURE_GYRO_FS_DPS,
    SLEEVE_SENSOR_SOURCE,
    STREAM_KEYS,
    DeviceSim,
    build_parser,
    build_sleeve_streams,
    rescale_counts,
    resolve_sleeve_base_id,
)

from common import kinds, packet  # noqa: E402


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


def test_same_source_sensors_share_time_base() -> None:
    dev = DeviceSim(device_id=30, streams=_fake_streams(), rate=600.0,
                    drift_ppm=0.0, rng=random.Random(9), start=0.0)
    payloads: list[bytes] = []
    now = 0.0
    for _ in range(20):
        now += 0.005
        payloads.extend(dev.due_payloads(now))
    batch = packet.decode(payloads)
    for src in (0, 1):
        first_ts = {}
        for sen in (1, 2):
            mask = (batch.source_id == src) & (batch.sensor_id == sen)
            first_ts[sen] = int(batch.ts_us[mask][0])
        # both sensors of one leg MCU run on the same clock: first samples are
        # within a few sample periods of each other (phase offset only)
        assert abs(first_ts[1] - first_ts[2]) < 10_000


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


# ----------------------------------------------------- unilateral sleeves ---
# One sleeve = one MCU on one leg: sync 0xA6, two streams (sensor 1 thigh,
# sensor 2 shin) on its own source, counts re-expressed at its full-scale.

def _sleeve_streams(source_id: int = 0, accel_fs: int = 32, gyro_fs: int = 4000,
                    n: int = 50) -> dict[tuple[int, int], np.ndarray]:
    return build_sleeve_streams(_fake_streams(n), source_id, accel_fs, gyro_fs)


def _sleeve(device_id: int = 41, source_id: int = 0, accel_fs: int = 32,
            gyro_fs: int = 4000, seed: int = 3, **kwargs) -> DeviceSim:
    return DeviceSim(device_id=device_id,
                     streams=_sleeve_streams(source_id, accel_fs, gyro_fs),
                     rate=600.0, drift_ppm=0.0, rng=random.Random(seed), start=0.0,
                     sync=kinds.SYNC_UNILATERAL, **kwargs)


def _pump(dev: DeviceSim, steps: int = 40, step: float = 0.005) -> list[bytes]:
    """All payloads a fresh emitter produces over `steps` scheduling slots."""
    payloads: list[bytes] = []
    now = 0.0
    for _ in range(steps):
        now += step
        payloads.extend(dev.due_payloads(now))
    return payloads


def test_sleeve_payloads_decode_as_unilateral_kind() -> None:
    dev = _sleeve(device_id=41)
    payloads = _pump(dev)
    assert payloads

    # the wire really carries 0xA6, not just a kind the decoder inferred
    assert {p[2] for p in payloads} == {kinds.SYNC_UNILATERAL}

    batch = packet.decode(payloads)
    assert batch.n == batch.n_in == len(payloads)
    assert batch.n_bad_len == batch.n_bad_sync == batch.n_bad_crc == 0
    assert set(batch.kind.tolist()) == {kinds.KIND_UNILATERAL}
    assert batch.n_unilateral == batch.n and batch.n_bilateral == 0
    assert set(batch.version.tolist()) == {packet.VERSION}


def test_sleeve_emits_exactly_thigh_and_shin_on_its_own_source() -> None:
    for source_id in (0, 1):
        dev = _sleeve(device_id=42, source_id=source_id)
        batch = packet.decode(_pump(dev))
        assert set(batch.device_id.tolist()) == {42}
        seen = set(zip(batch.source_id.tolist(), batch.sensor_id.tolist()))
        assert seen == {(source_id, 1), (source_id, 2)}   # 1 = thigh, 2 = shin
        assert dev.label == f"u42-{source_id}"            # reports name the unit


def test_sleeve_ids_run_consecutively_from_the_sleeve_base_id() -> None:
    args = build_parser().parse_args([])
    assert args.sleeves == 0
    assert args.sleeve_source_id == 0
    assert args.sleeve_accel_fs == 32 and args.sleeve_gyro_fs == 4000
    # unset base id: just past the bilateral run, so ids never collide
    assert resolve_sleeve_base_id(args) == args.base_id + args.devices
    assert resolve_sleeve_base_id(
        build_parser().parse_args(["--devices", "3", "--sleeves", "2"])) == 33
    assert resolve_sleeve_base_id(
        build_parser().parse_args(["--sleeve-base-id", "77"])) == 77

    base = resolve_sleeve_base_id(build_parser().parse_args(["--devices", "1"]))
    assert [_sleeve(device_id=base + s).label for s in range(3)] \
        == ["u31-0", "u32-0", "u33-0"]


def test_sleeve_sensors_share_one_time_base() -> None:
    # one MCU drives both IMUs, so thigh and shin run on the same clock
    dev = _sleeve(device_id=43, seed=9)
    batch = packet.decode(_pump(dev, steps=20))
    first_ts = {}
    for sen in (1, 2):
        mask = batch.sensor_id == sen
        assert mask.any()
        first_ts[sen] = int(batch.ts_us[mask][0])
    assert abs(first_ts[1] - first_ts[2]) < 10_000


def test_sleeve_counts_are_halved_at_32g_4000dps() -> None:
    src = _fake_streams()
    sleeve = _sleeve_streams(source_id=0, accel_fs=32, gyro_fs=4000)

    for sen, key in SLEEVE_SENSOR_SOURCE.items():
        original = src[key].astype(np.int64)
        got = sleeve[(0, sen)]
        assert got.dtype == np.int16
        assert got.shape == original.shape
        # the same motion at twice the full-scale is half the counts (+-1 rounding)
        assert np.all(np.abs(got.astype(np.int64) * 2 - original) <= 1)
        even = original % 2 == 0
        assert even.any()
        assert np.array_equal(got.astype(np.int64)[even], original[even] // 2)
        assert np.any(got != 0)   # a fixture of zeros would pass everything above


def test_sleeve_rescaling_is_per_axis_group_and_survives_the_wire() -> None:
    # accel columns scale by 16/fs_g, gyro columns by 2000/fs_dps, separately
    src = _fake_streams()
    mixed = build_sleeve_streams(src, 0, 32, 2000)          # accel halved, gyro kept
    thigh = mixed[(0, 1)].astype(np.int64)
    capture = src[SLEEVE_SENSOR_SOURCE[1]].astype(np.int64)
    assert np.all(np.abs(thigh[:, :3] * 2 - capture[:, :3]) <= 1)
    assert np.array_equal(thigh[:, 3:], capture[:, 3:])

    # and the halving is what a receiver actually decodes
    dev = _sleeve(device_id=44, seed=5)
    batch = packet.decode(_pump(dev, steps=10))
    shin = src[SLEEVE_SENSOR_SOURCE[2]].astype(np.int64)
    decoded = batch.imu[batch.sensor_id == 2][0].astype(np.int64)
    assert np.all(np.abs(decoded * 2 - shin[0]) <= 1)


def test_rescale_is_a_no_op_at_the_capture_full_scale() -> None:
    src = _fake_streams()
    kept = rescale_counts(src[(0, 1)], CAPTURE_ACCEL_FS_G, CAPTURE_GYRO_FS_DPS)
    assert np.array_equal(kept, src[(0, 1)])

    sleeve = _sleeve_streams(source_id=0, accel_fs=16, gyro_fs=2000)
    assert np.array_equal(sleeve[(0, 1)], src[SLEEVE_SENSOR_SOURCE[1]])  # thigh
    assert np.array_equal(sleeve[(0, 2)], src[SLEEVE_SENSOR_SOURCE[2]])  # shin


def test_rescale_clips_to_int16_below_the_capture_full_scale() -> None:
    # +-2 g is 8x the counts: real hardware would saturate there, so do we
    imu = np.array([[20_000, -20_000, 1, 30_000, -30_000, 2]], dtype=np.int16)
    out = rescale_counts(imu, 2, 125)
    assert out.dtype == np.int16
    assert out[0, 0] == 32_767 and out[0, 1] == -32_768
    assert out[0, 2] == 8
    assert out[0, 3] == 32_767 and out[0, 4] == -32_768
    assert out[0, 5] == 32


def test_dead_sensors_and_soc_apply_to_a_sleeve() -> None:
    dev = _sleeve(device_id=45, dead_sensors=frozenset({(0, 2)}), soc=80)
    batch = packet.decode(_pump(dev))
    assert set(zip(batch.source_id.tolist(), batch.sensor_id.tolist())) == {(0, 1)}
    # one MCU, one battery: every packet of the unit reports the same soc
    assert set(batch.soc.tolist()) == {80}


def test_bilateral_emission_is_unchanged_when_no_sleeves_are_requested() -> None:
    streams = _fake_streams()

    def build(**kwargs) -> DeviceSim:
        return DeviceSim(device_id=31, streams=streams, rate=600.0, drift_ppm=0.0,
                         rng=random.Random(1), start=0.0, **kwargs)

    # the default sync is still the bilateral one, byte for byte
    plain = build()
    payloads = _pump(plain, steps=30)
    assert payloads == _pump(build(sync=kinds.SYNC_BILATERAL), steps=30)
    assert plain.label == "dev31" and not plain.unilateral
    assert {p[2] for p in payloads} == {kinds.SYNC_BILATERAL}

    batch = packet.decode(payloads)
    assert set(batch.kind.tolist()) == {kinds.KIND_BILATERAL}
    assert batch.n_bilateral == batch.n and batch.n_unilateral == 0
    assert set(zip(batch.source_id.tolist(), batch.sensor_id.tolist())) \
        == set(STREAM_KEYS)
