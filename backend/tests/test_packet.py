"""S1-T03 tests: round-trip, golden vs example/squats*, corruption counting."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from common import packet

REPO_ROOT = Path(__file__).resolve().parents[2]
SQUATS_BIN = REPO_ROOT / "example" / "squats.bin"
SQUATS_CSV = REPO_ROOT / "example" / "squats_decoded.csv"

GOLDEN_N = 1000


# --- round trip ----------------------------------------------------------------

def test_round_trip_encode_decode() -> None:
    rng = np.random.default_rng(42)
    records = []
    for i in range(200):
        records.append(
            dict(
                device_id=int(rng.integers(0, 256)),
                source_id=int(rng.integers(0, 2)),
                sensor_id=int(rng.integers(1, 3)),
                ts_us=int(rng.integers(0, 2**32)),
                imu6=[int(v) for v in rng.integers(-32768, 32768, size=6)],
            )
        )
    # edge cases: wrap boundary, extremes
    records.append(dict(device_id=30, source_id=0, sensor_id=1, ts_us=2**32 - 1,
                        imu6=[32767, -32768, 0, 1, -1, 12345]))
    records.append(dict(device_id=0, source_id=1, sensor_id=2, ts_us=0,
                        imu6=[0, 0, 0, 0, 0, 0]))

    payloads = [packet.encode(r["device_id"], r["source_id"], r["sensor_id"],
                              r["ts_us"], r["imu6"]) for r in records]
    assert all(len(p) == packet.REC_SIZE for p in payloads)

    batch = packet.decode(payloads)
    assert batch.n_in == len(records)
    assert batch.n == len(records)
    assert batch.n_bad_len == batch.n_bad_sync == batch.n_bad_crc == 0
    for i, r in enumerate(records):
        assert batch.device_id[i] == r["device_id"]
        assert batch.source_id[i] == r["source_id"]
        assert batch.sensor_id[i] == r["sensor_id"]
        assert batch.version[i] == packet.VERSION
        assert int(batch.ts_us[i]) == r["ts_us"]
        assert batch.imu[i].tolist() == r["imu6"]


# --- golden test against example/ ----------------------------------------------

@pytest.mark.skipif(not SQUATS_BIN.exists(), reason="example data not present")
def test_golden_against_example_decode() -> None:
    raw = SQUATS_BIN.read_bytes()
    payloads = [raw[i * packet.REC_SIZE:(i + 1) * packet.REC_SIZE] for i in range(GOLDEN_N)]
    batch = packet.decode(payloads)

    with SQUATS_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = [next(reader) for _ in range(GOLDEN_N)]

    good = [r for r in rows
            if r["crc_ok"] == "1" and int(r["sync"]) == packet.SYNC]
    n_bad_sync_csv = sum(1 for r in rows if int(r["sync"]) != packet.SYNC)
    n_bad_crc_csv = sum(1 for r in rows
                        if int(r["sync"]) == packet.SYNC and r["crc_ok"] == "0")

    assert batch.n_in == GOLDEN_N
    assert batch.n_bad_len == 0
    assert batch.n_bad_sync == n_bad_sync_csv
    assert batch.n_bad_crc == n_bad_crc_csv
    assert batch.n == len(good)

    for i, r in enumerate(good):
        assert int(batch.device_id[i]) == int(r["device_id"])
        assert int(batch.source_id[i]) == int(r["source_id"])
        assert int(batch.sensor_id[i]) == int(r["sensor_id"])
        assert int(batch.version[i]) == int(r["version"])
        assert int(batch.ts_us[i]) == int(r["timestamp_us"])
        expected_imu = [int(r[k]) for k in
                        ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")]
        assert batch.imu[i].tolist() == expected_imu


# --- corruption handling -------------------------------------------------------

def _valid_payload(**overrides) -> bytes:
    kw = dict(device_id=30, source_id=0, sensor_id=1, ts_us=123_456,
              imu6=[100, -200, 300, -400, 500, -600])
    kw.update(overrides)
    return packet.encode(**kw)


def test_corrupt_crc_counted_and_filtered() -> None:
    p = bytearray(_valid_payload())
    p[20] ^= 0xFF
    batch = packet.decode([_valid_payload(), bytes(p)])
    assert batch.n == 1
    assert batch.n_bad_crc == 1
    assert batch.n_bad_sync == 0


def test_corrupt_payload_body_fails_crc() -> None:
    p = bytearray(_valid_payload())
    p[10] ^= 0x01  # flip a data bit -> stored CRC no longer matches
    batch = packet.decode([bytes(p)])
    assert batch.n == 0
    assert batch.n_bad_crc == 1


def test_bad_sync_counted_and_filtered() -> None:
    p = bytearray(_valid_payload())
    p[2] = 0x00
    batch = packet.decode([bytes(p), _valid_payload()])
    assert batch.n == 1
    assert batch.n_bad_sync == 1
    assert batch.n_bad_crc == 0


def test_wrong_length_never_raises() -> None:
    batch = packet.decode([b"", b"\x01" * 5, b"\x01" * 100, _valid_payload()])
    assert batch.n_in == 4
    assert batch.n_bad_len == 3
    assert batch.n == 1


def test_all_bad_returns_empty_batch() -> None:
    batch = packet.decode([b"short", b"x" * 22])
    assert batch.n == 0
    assert batch.n_in == 2
    assert batch.n_bad_len == 2
    assert batch.imu.shape == (0, 6)


def test_empty_input() -> None:
    batch = packet.decode([])
    assert batch.n == 0
    assert batch.n_in == 0
