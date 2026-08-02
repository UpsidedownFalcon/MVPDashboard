"""Wire-format decoder/encoder — single source of truth for the UDP packet layout.

TWO layouts exist and they differ by one byte. Keeping them apart is the whole
point of this module:

  UDP datagram — 22 bytes (DGRAM_SIZE), what the wearable actually transmits
  SD-log record — 21 bytes (LOG_REC_SIZE), what example/squats.bin contains

    [0]      device_id  (u8)
    [1]      source_id  (u8)  — leg MCU, 0 or 1
    [2..20]  19 wire bytes:
        [0]      sync          = 0xA5
        [1]      header        bits[1:0]=sensor_id (1|2), bits[7:2]=version (=1)
        [2..5]   timestamp_us  u32 LE (wraps ~71.6 min, monotonic per source)
        [6..17]  ax ay az gx gy gz  (6 × i16 LE, raw counts)
        [18]     crc8          poly=0x07 init=0x00 MSB-first over wire[1..17]
    [21]     soc  (u8)  — UDP ONLY; absent from the SD log

The trailing byte is the "SOC byte" that example/parse_imu.py notes is **not
logged** to SD. It sat unaccounted for until a live capture from the real device
showed 22-byte datagrams: decode() required exactly 21 and silently rejected
every packet as bad_len, so the whole pipeline stayed dark with a healthy device
streaming. It is decoded and reported but never validated, and nothing here
depends on its meaning — observed live it varies sample to sample (20..30 over
two captures), which rules out a device-id echo but does not confirm "state of
charge" either. Treat the name as the SD-logger's intent, not a verified fact.

decode() reads UDP datagrams; decode_log() reads SD-log records; encode() is the
exact inverse of decode() — all kept in this file so they cannot drift.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

DGRAM_SIZE = 22  # UDP datagram: log record + trailing soc byte
LOG_REC_SIZE = 21  # SD-log record (example/squats.bin)
SOC_OFF = 21
WIRE_OFF = 2
WIRE_LEN = 19
SYNC = 0xA5
CRC_POLY = 0x07
CRC_FIRST, CRC_LAST = 1, 17  # wire byte range covered by the CRC, inclusive
VERSION = 1

IMU_FIELDS = ("ax", "ay", "az", "gx", "gy", "gz")


def crc8_table(poly: int = CRC_POLY) -> np.ndarray:
    """Byte-wise CRC-8 lookup table (MSB-first, init=0x00). Ported from example/."""
    tbl = np.empty(256, dtype=np.uint8)
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ poly) & 0xFF if (c & 0x80) else (c << 1) & 0xFF
        tbl[i] = c
    return tbl


_CRC_TABLE = crc8_table()


def compute_crc(wire: np.ndarray) -> np.ndarray:
    """CRC-8 over wire[CRC_FIRST..CRC_LAST] for every row of an (n, 19) matrix."""
    crc = np.zeros(wire.shape[0], dtype=np.uint8)
    for b in range(CRC_FIRST, CRC_LAST + 1):
        crc = _CRC_TABLE[crc ^ wire[:, b]]
    return crc


def _i16(lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Little-endian signed 16-bit from two u8 columns."""
    return (lo.astype(np.uint16) | (hi.astype(np.uint16) << 8)).astype(np.int16)


def _u32(b0: np.ndarray, b1: np.ndarray, b2: np.ndarray, b3: np.ndarray) -> np.ndarray:
    """Little-endian unsigned 32-bit from four u8 columns."""
    return (
        b0.astype(np.uint32)
        | (b1.astype(np.uint32) << 8)
        | (b2.astype(np.uint32) << 16)
        | (b3.astype(np.uint32) << 24)
    )


@dataclass
class Batch:
    """Decoded, validated records (bad ones filtered out, only counted)."""

    device_id: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.uint8))
    source_id: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.uint8))
    sensor_id: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.uint8))
    version: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.uint8))
    ts_us: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.uint32))
    imu: np.ndarray = field(default_factory=lambda: np.empty((0, 6), dtype=np.int16))
    soc: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.uint8))
    n_in: int = 0
    n_bad_len: int = 0
    n_bad_sync: int = 0
    n_bad_crc: int = 0

    @property
    def n(self) -> int:
        return self.device_id.shape[0]


def decode(payloads: list[bytes]) -> Batch:
    """Decode a batch of UDP datagrams (22 B). Wrong-length payloads never raise."""
    return _decode(payloads, DGRAM_SIZE)


def decode_log(payloads: list[bytes]) -> Batch:
    """Decode SD-log records (21 B, no soc byte) — example/squats.bin only."""
    return _decode(payloads, LOG_REC_SIZE)


def _decode(payloads: list[bytes], rec_size: int) -> Batch:
    n_in = len(payloads)
    valid = [p for p in payloads if len(p) == rec_size]
    n_bad_len = n_in - len(valid)
    if not valid:
        return Batch(n_in=n_in, n_bad_len=n_bad_len)

    recs = np.frombuffer(b"".join(valid), dtype=np.uint8).reshape(len(valid), rec_size)
    wire = recs[:, WIRE_OFF : WIRE_OFF + WIRE_LEN]

    sync_ok = wire[:, 0] == SYNC
    n_bad_sync = int(np.count_nonzero(~sync_ok))
    if n_bad_sync:
        recs, wire = recs[sync_ok], wire[sync_ok]

    crc_ok = compute_crc(wire) == wire[:, 18]
    n_bad_crc = int(np.count_nonzero(~crc_ok))
    if n_bad_crc:
        recs, wire = recs[crc_ok], wire[crc_ok]

    header = wire[:, 1]
    imu = np.column_stack(
        [_i16(wire[:, 6 + 2 * k], wire[:, 7 + 2 * k]) for k in range(6)]
    ).astype(np.int16, copy=False)

    soc = (
        recs[:, SOC_OFF].copy()
        if rec_size > SOC_OFF
        else np.zeros(recs.shape[0], dtype=np.uint8)
    )

    return Batch(
        device_id=recs[:, 0].copy(),
        source_id=recs[:, 1].copy(),
        soc=soc,
        sensor_id=header & 0x03,
        version=header >> 2,
        ts_us=_u32(wire[:, 2], wire[:, 3], wire[:, 4], wire[:, 5]),
        imu=imu,
        n_in=n_in,
        n_bad_len=n_bad_len,
        n_bad_sync=n_bad_sync,
        n_bad_crc=n_bad_crc,
    )


def encode(
    device_id: int,
    source_id: int,
    sensor_id: int,
    ts_us: int,
    imu6,
    soc: int = 0,
) -> bytes:
    """Build one valid 22-byte UDP datagram (correct CRC) — inverse of decode()."""
    header = ((VERSION << 2) | (sensor_id & 0x03)) & 0xFF
    rec = bytearray(DGRAM_SIZE)
    rec[0] = device_id & 0xFF
    rec[1] = source_id & 0xFF
    rec[2] = SYNC
    rec[3] = header
    rec[4:8] = (int(ts_us) & 0xFFFFFFFF).to_bytes(4, "little")
    rec[8:20] = struct.pack("<6h", *(int(v) for v in imu6))
    crc = 0
    for b in rec[WIRE_OFF + CRC_FIRST : WIRE_OFF + CRC_LAST + 1]:
        crc = int(_CRC_TABLE[crc ^ b])
    rec[20] = crc
    rec[SOC_OFF] = soc & 0xFF
    return bytes(rec)
