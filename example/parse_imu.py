#!/usr/bin/env python3
"""
Parse/decode an IMU SD log of back-to-back 21-byte little-endian
`imu_log_record_t` records.

On-disk record (21 bytes):
    [0]      device_id  (u8)
    [1]      source_id  (u8)
    [2..20]  19 wire bytes verbatim (no SOC byte logged)

Wire format (19 bytes, per screenshot):
    [0]      sync          = 0xA5
    [1]      header        bits[1:0]=sensor_id (1=IMU1,2=IMU2), bits[7:2]=version(1)
    [2..5]   timestamp_us  u32 LE (truncated 64-bit ISR time, wraps ~71.6 min)
    [6..7]   accel_x       i16 LE (raw counts)
    [8..9]   accel_y       i16 LE
    [10..11] accel_z       i16 LE
    [12..13] gyro_x        i16 LE
    [14..15] gyro_y        i16 LE
    [16..17] gyro_z        i16 LE
    [18]     crc8          CRC-8 poly=0x07 init=0x00 MSB-first over wire[1..17]

Usage:
    python parse_imu.py sensor_position.bin -o decoded.csv
    python parse_imu.py sensor_position.bin --quiet        # CSV only, no report
    python parse_imu.py sensor_position.bin --no-csv       # report only

The input file is only ever opened for reading; it is never modified.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- constants --

REC_SIZE = 21          # bytes per on-disk record
WIRE_OFF = 2           # offset of the wire payload inside a record
WIRE_LEN = 19          # bytes of wire payload
SYNC = 0xA5
CRC_POLY = 0x07
CRC_FIRST, CRC_LAST = 1, 17   # wire[1..17] inclusive are CRC-covered

DEFAULT_INPUT = "sensor_position.bin"

CSV_COLUMNS = [
    "index", "device_id", "source_id", "sync", "sensor_id", "version",
    "timestamp_us", "accel_x", "accel_y", "accel_z",
    "gyro_x", "gyro_y", "gyro_z", "crc_stored", "crc_calc", "crc_ok",
]

# Maps a CSV column to the key of the decoded dict it comes from.
_CSV_SOURCE = {
    "device_id": "device_id", "source_id": "source_id", "sync": "sync",
    "sensor_id": "sensor_id", "version": "version", "timestamp_us": "ts_us",
    "accel_x": "ax", "accel_y": "ay", "accel_z": "az",
    "gyro_x": "gx", "gyro_y": "gy", "gyro_z": "gz",
    "crc_stored": "crc_stored", "crc_calc": "crc_calc", "crc_ok": "crc_ok",
}

AXES = ("ax", "ay", "az", "gx", "gy", "gz")


# ------------------------------------------------------------------ helpers --

def crc8_table(poly: int = CRC_POLY) -> np.ndarray:
    """Byte-wise CRC-8 lookup table (MSB-first, init=0x00)."""
    tbl = np.empty(256, dtype=np.uint8)
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ poly) & 0xFF if (c & 0x80) else (c << 1) & 0xFF
        tbl[i] = c
    return tbl


def i16(lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Little-endian signed 16-bit from two u8 columns."""
    return (lo.astype(np.uint16) | (hi.astype(np.uint16) << 8)).astype(np.int16)


def u32(b0: np.ndarray, b1: np.ndarray, b2: np.ndarray, b3: np.ndarray) -> np.ndarray:
    """Little-endian unsigned 32-bit from four u8 columns."""
    return (b0.astype(np.uint32)
            | (b1.astype(np.uint32) << 8)
            | (b2.astype(np.uint32) << 16)
            | (b3.astype(np.uint32) << 24))


# --------------------------------------------------------------------- read --

def read_records(path: str | Path) -> tuple[np.ndarray, int]:
    """Read the log read-only and reshape into an (n, REC_SIZE) byte matrix.

    Returns (records, trailing_bytes) where trailing_bytes is the size of the
    partial record at EOF, if any.
    """
    raw = np.fromfile(str(path), dtype=np.uint8)
    if raw.size == 0:
        raise ValueError(f"{path}: file is empty or unreadable")
    trailing = raw.size % REC_SIZE
    n = raw.size // REC_SIZE
    if n == 0:
        raise ValueError(f"{path}: {raw.size} bytes is less than one {REC_SIZE}-byte record")
    return raw[:n * REC_SIZE].reshape(n, REC_SIZE), trailing


# ------------------------------------------------------------------- decode --

def compute_crc(wire: np.ndarray) -> np.ndarray:
    """CRC-8 over wire[CRC_FIRST..CRC_LAST] for every record (vectorised)."""
    tbl = crc8_table()
    crc = np.zeros(wire.shape[0], dtype=np.uint8)
    for b in range(CRC_FIRST, CRC_LAST + 1):
        crc = tbl[crc ^ wire[:, b]]
    return crc


def decode_records(recs: np.ndarray, trailing: int = 0) -> dict:
    """Decode an (n, REC_SIZE) byte matrix into named field arrays."""
    device_id = recs[:, 0]
    source_id = recs[:, 1]
    w = recs[:, WIRE_OFF:WIRE_OFF + WIRE_LEN]      # 19 wire bytes

    header = w[:, 1]
    crc_stored = w[:, 18]
    crc_calc = compute_crc(w)

    return dict(
        n=recs.shape[0],
        trailing=trailing,
        device_id=device_id,
        source_id=source_id,
        sync=w[:, 0],
        header=header,
        sensor_id=header & 0x03,
        version=header >> 2,
        ts_us=u32(w[:, 2], w[:, 3], w[:, 4], w[:, 5]),
        ax=i16(w[:, 6],  w[:, 7]),
        ay=i16(w[:, 8],  w[:, 9]),
        az=i16(w[:, 10], w[:, 11]),
        gx=i16(w[:, 12], w[:, 13]),
        gy=i16(w[:, 14], w[:, 15]),
        gz=i16(w[:, 16], w[:, 17]),
        crc_stored=crc_stored,
        crc_calc=crc_calc,
        crc_ok=crc_calc == crc_stored,
    )


def parse(path: str | Path) -> dict:
    """Read and decode a log file in one call."""
    recs, trailing = read_records(path)
    return decode_records(recs, trailing)


# -------------------------------------------------------------------- write --

def to_matrix(d: dict) -> np.ndarray:
    """Stack the decoded fields into an (n, len(CSV_COLUMNS)) int64 matrix."""
    n = d["n"]
    cols = [np.arange(n, dtype=np.int64)]
    cols += [d[_CSV_SOURCE[name]].astype(np.int64) for name in CSV_COLUMNS[1:]]
    return np.column_stack(cols)


def write_csv(d: dict, out_path: str | Path, only_valid: bool = False) -> int:
    """Write the full decode to CSV. Returns the number of data rows written."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    mat = to_matrix(d)
    if only_valid:
        mat = mat[d["crc_ok"]]

    with open(out_path, "w", newline="") as f:
        f.write(",".join(CSV_COLUMNS) + "\n")
        # Chunked join-and-write: ~an order of magnitude faster than csv.writer
        # per row, and keeps peak memory bounded for multi-million-record logs.
        CHUNK = 50_000
        for start in range(0, mat.shape[0], CHUNK):
            block = mat[start:start + CHUNK]
            f.write("\n".join(",".join(map(str, row)) for row in block.tolist()))
            f.write("\n")
    return mat.shape[0]


# ------------------------------------------------------------------- report --

def fmt_table(d: dict, idx) -> str:
    """Fixed-width preview table for the given record indices."""
    cols = ["#", "dev", "src", "sync", "sid", "ver", "ts_us",
            "ax", "ay", "az", "gx", "gy", "gz", "crc", "ok"]
    rows = []
    for i in idx:
        rows.append([
            str(i),
            str(int(d["device_id"][i])),
            str(int(d["source_id"][i])),
            f"{int(d['sync'][i]):02X}",
            str(int(d["sensor_id"][i])),
            str(int(d["version"][i])),
            str(int(d["ts_us"][i])),
            str(int(d["ax"][i])), str(int(d["ay"][i])), str(int(d["az"][i])),
            str(int(d["gx"][i])), str(int(d["gy"][i])), str(int(d["gz"][i])),
            f"{int(d['crc_stored'][i]):02X}",
            "Y" if d["crc_ok"][i] else "N",
        ])
    if not rows:
        return "(no records)"
    widths = [max(len(cols[c]), max(len(r[c]) for r in rows)) for c in range(len(cols))]
    line = lambda vals: "  ".join(v.rjust(widths[c]) for c, v in enumerate(vals))
    out = [line(cols), "  ".join("-" * widths[c] for c in range(len(cols)))]
    out += [line(r) for r in rows]
    return "\n".join(out)


def report(d: dict, path: str | Path, preview: int = 20) -> str:
    """Human-readable integrity/statistics summary of a decoded log."""
    n = d["n"]
    bad = int((~d["crc_ok"]).sum())
    L = [
        f"File            : {path}",
        f"Records         : {n:,}  (trailing bytes: {d['trailing']})",
        f"sync == 0xA5    : {int((d['sync'] == SYNC).sum()):,} / {n:,}",
        f"CRC valid       : {int(d['crc_ok'].sum()):,} / {n:,}"
        f"  ({100 * d['crc_ok'].mean():.4f}%)",
        f"CRC failures    : {bad:,}",
        "",
        "Field distributions:",
    ]
    for name in ("device_id", "source_id", "sensor_id", "version"):
        vals, cnts = np.unique(d[name], return_counts=True)
        pairs = ", ".join(f"{int(v)}:{int(c):,}" for v, c in zip(vals, cnts))
        L.append(f"  {name:<10}: {pairs}")

    for sid in (1, 2):
        m = d["sensor_id"] == sid
        if not m.any():
            continue
        ts = d["ts_us"][m].astype(np.int64)
        L += [
            "",
            f"IMU{sid} (sensor_id={sid}): {int(m.sum()):,} records",
            f"  ts_us range : {int(ts.min())} .. {int(ts.max())}",
            f"  ts wraps    : {int((np.diff(ts) < 0).sum())}",
        ]
        for axis in AXES:
            v = d[axis][m]
            L.append(f"  {axis:<3}: min={int(v.min()):>7}  max={int(v.max()):>7}  "
                     f"mean={v.mean():>9.2f}")

    if preview > 0:
        L += ["", f"First {min(preview, n)} records:", fmt_table(d, range(min(preview, n)))]

    if bad:
        badidx = np.where(~d["crc_ok"])[0][:preview or 20]
        L += ["", f"First CRC-failing records (showing {len(badidx)}):",
              fmt_table(d, badidx)]

    return "\n".join(L)


# ---------------------------------------------------------------------- cli --

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Decode a 21-byte-record IMU binary log to CSV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input", nargs="?", default=DEFAULT_INPUT,
                   help="binary log to decode (read-only, never modified)")
    p.add_argument("-o", "--output", default=None,
                   help="CSV output path [default: <input>_decoded.csv]")
    p.add_argument("--no-csv", action="store_true", help="report only, skip CSV export")
    p.add_argument("--only-valid", action="store_true",
                   help="export only records whose CRC checks out")
    p.add_argument("--preview", type=int, default=20,
                   help="records to show in the preview table (0 disables)")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress the report")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"error: no such file: {in_path}", file=sys.stderr)
        return 2

    d = parse(in_path)

    if not args.quiet:
        print(report(d, in_path, preview=args.preview))

    if not args.no_csv:
        out_csv = Path(args.output) if args.output else \
            in_path.with_name(in_path.stem + "_decoded.csv")
        rows = write_csv(d, out_csv, only_valid=args.only_valid)
        print(f"\nWrote {rows:,} rows to: {out_csv.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
