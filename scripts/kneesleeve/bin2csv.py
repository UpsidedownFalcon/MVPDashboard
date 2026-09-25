#!/usr/bin/env python3
"""bin2csv.py — convert LOG_NNNN.BIN (NYKS format_version 1) to CSV.

Usage:
    python bin2csv.py LOG_0001.BIN [LOG_0002.BIN ...] [-o OUTDIR] [--split] [--raw]

Output columns:
    device_id, sensor_id, seq, t_us, unix_us, ax, ay, az, gx, gy, gz
  device_id  from the file header; (device_id, sensor_id) identifies a sensor
  t_us     device microseconds (base_ts_us + dt_us)
  unix_us  UTC microseconds via time-sync block (empty if the file has none)
  ax..az   g   (raw i16 counts with --raw)
  gx..gz   dps (raw i16 counts with --raw)

Scales always come from the header of the file being decoded.
Blocks with bad magic/CRC are skipped with a warning; a trailing partial
block (dirty end) is ignored. Needs numpy.

Beside every CSV a `<name>.meta.json` sidecar records the file header
(fw, device_id, session, odr_hz, accel_fs_g, gyro_fs_dps, scales), whether the
CSV holds raw counts, and the integrity result (blocks, seq gaps, bad CRCs,
FIFO overflows, clean/dirty end). sensor_stats.py reads it.
"""
import json
import argparse
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

FILE_MAGIC = 0x534B594E
BLOCK_MAGIC = 0xB10C
HDR_SIZE = 512
BLK_SIZE = 4096
BLK_HDR = struct.Struct("<HBBIQHHIQ")            # 32 B
FILE_HDR = struct.Struct("<IHH16sBBBBIHHffIQB")  # 57 B, then reserved + crc
SYNC = struct.Struct("<QQB")
SAMPLE = np.dtype([("dt", "<u2"), ("ax", "<i2"), ("ay", "<i2"), ("az", "<i2"),
                   ("gx", "<i2"), ("gy", "<i2"), ("gz", "<i2")])
MAX_SAMPLES = 290
T_IMU, T_SYNC, T_END = 0, 1, 2


def warn(msg):
    print(f"  WARN: {msg}", file=sys.stderr)


def parse_file_header(buf):
    if len(buf) < HDR_SIZE:
        raise ValueError("file shorter than 512 B header")
    (magic, ver, hsize, fw, dev, src, nsens, wm, odr, afs, gfs,
     ascale, gscale, session, boot_us, utc_valid) = FILE_HDR.unpack_from(buf, 0)
    if magic != FILE_MAGIC:
        raise ValueError(f"bad file magic 0x{magic:08X}")
    if ver != 1 or hsize != HDR_SIZE:
        raise ValueError(f"unsupported format_version={ver} header_size={hsize}")
    crc_stored = struct.unpack_from("<I", buf, 508)[0]
    crc_calc = zlib.crc32(buf[:508]) & 0xFFFFFFFF
    if crc_stored != crc_calc:
        raise ValueError(f"header CRC mismatch (stored {crc_stored:08X}, calc {crc_calc:08X})")
    return dict(fw=fw.split(b"\0")[0].decode("ascii", "replace"), device_id=dev,
                source_id=src, sensor_count=nsens, odr_hz=odr, accel_fs_g=afs,
                gyro_fs_dps=gfs, accel_scale=float(ascale), gyro_scale=float(gscale),
                session_id=session)


def block_ok(blk):
    crc_stored = struct.unpack_from("<I", blk, 20)[0]
    zeroed = blk[:20] + b"\0\0\0\0" + blk[24:]
    return (zlib.crc32(zeroed) & 0xFFFFFFFF) == crc_stored


def scan_syncs(path):
    """Pre-pass: collect (esp_us, unix_us) from every valid time-sync block."""
    syncs = []
    with open(path, "rb") as f:
        f.seek(HDR_SIZE)
        while True:
            blk = f.read(BLK_SIZE)
            if len(blk) < BLK_SIZE:
                break
            magic, typ = struct.unpack_from("<HB", blk, 0)
            if magic == BLOCK_MAGIC and typ == T_SYNC and block_ok(blk):
                esp_us, unix_us, _src = SYNC.unpack_from(blk, 32)
                syncs.append((esp_us, unix_us))
    return sorted(syncs)


def pick_sync(syncs, t_us):
    """Latest sync at/before t_us, else the first one (extrapolate backwards)."""
    best = syncs[0]
    for s in syncs:
        if s[0] <= t_us:
            best = s
        else:
            break
    return best


def convert(path, outdir, split, raw):
    path = Path(path)
    print(f"{path.name}:")
    with open(path, "rb") as f:
        hdr = parse_file_header(f.read(HDR_SIZE))
    print(f"  fw={hdr['fw']} dev={hdr['device_id']} session={hdr['session_id']} "
          f"odr={hdr['odr_hz']}Hz  ±{hdr['accel_fs_g']}g  ±{hdr['gyro_fs_dps']}dps")
    a_s, g_s = hdr["accel_scale"], hdr["gyro_scale"]
    syncs = scan_syncs(path)
    if not syncs:
        warn("no time-sync block — unix_us column will be empty")

    outdir.mkdir(parents=True, exist_ok=True)
    header = "device_id,sensor_id,seq,t_us,unix_us,ax,ay,az,gx,gy,gz\n"
    outs = {}

    def out_for(sid):
        key = sid if split else 0
        if key not in outs:
            name = f"{path.stem}_S{sid}.csv" if split else f"{path.stem}.csv"
            fh = open(outdir / name, "w", newline="\n", buffering=1 << 20)
            fh.write(header)
            outs[key] = fh
        return outs[key]

    rows = {}
    n_bad = n_gap = n_blk = n_ovf = 0
    last_seq = None
    clean = False
    with open(path, "rb") as f:
        f.seek(HDR_SIZE)
        idx = -1
        while True:
            blk = f.read(BLK_SIZE)
            if not blk:
                break
            idx += 1
            if len(blk) < BLK_SIZE:
                warn(f"trailing partial block ({len(blk)} B) ignored")
                break
            magic, typ, sid, seq, base, count, flags, _crc, _res = BLK_HDR.unpack_from(blk, 0)
            if magic != BLOCK_MAGIC or not block_ok(blk):
                n_bad += 1
                warn(f"block {idx}: bad magic/CRC, skipped")
                continue
            n_blk += 1
            if last_seq is not None and seq != last_seq + 1:
                n_gap += 1
                warn(f"seq gap: {last_seq} -> {seq}")
            last_seq = seq
            if typ == T_END:
                clean = True
                continue
            if typ != T_IMU:
                continue
            if count > MAX_SAMPLES:
                warn(f"block {idx}: sample_count {count} > {MAX_SAMPLES}, skipped")
                continue
            if flags & 1:
                n_ovf += 1
                warn(f"block seq {seq}: FIFO overflow flag set")
            s = np.frombuffer(blk, dtype=SAMPLE, count=count, offset=32)
            t = (np.uint64(base) + s["dt"].astype(np.uint64)).tolist()
            if syncs:
                esp, unix = pick_sync(syncs, base)
                off = unix - esp
                u = [str(x + off) for x in t]
            else:
                u = [""] * count
            fh = out_for(sid)
            pre = f"{hdr['device_id']},{sid},{seq},"
            if raw:
                cols = [s[k].tolist() for k in ("ax", "ay", "az", "gx", "gy", "gz")]
                fh.write("".join(
                    f"{pre}{tt},{uu},{a},{b},{c},{d},{e},{g}\n"
                    for tt, uu, a, b, c, d, e, g in zip(t, u, *cols)))
            else:
                cols = [(s[k].astype(np.float64) * a_s).tolist() for k in ("ax", "ay", "az")]
                cols += [(s[k].astype(np.float64) * g_s).tolist() for k in ("gx", "gy", "gz")]
                fh.write("".join(
                    f"{pre}{tt},{uu},{a:.6f},{b:.6f},{c:.6f},{d:.4f},{e:.4f},{g:.4f}\n"
                    for tt, uu, a, b, c, d, e, g in zip(t, u, *cols)))
            rows[sid] = rows.get(sid, 0) + count
    for fh in outs.values():
        fh.close()
    meta = dict(hdr, source_file=path.name, units="raw" if raw else "physical",
                blocks_valid=n_blk, blocks_bad=n_bad, seq_gaps=n_gap,
                fifo_overflows=n_ovf, clean_end=clean,
                time_sync=[{"esp_us": e, "unix_us": u} for e, u in syncs],
                rows={str(k): v for k, v in rows.items()})
    for fh in outs.values():
        Path(fh.name).with_suffix(".meta.json").write_text(json.dumps(meta, indent=1))
    print(f"  {n_blk} valid blocks, {n_bad} bad, {n_gap} seq gaps, {n_ovf} FIFO overflows, "
          f"{'CLEAN' if clean else 'DIRTY'} end, rows per sensor: {rows}")
    return 0 if not (n_bad or n_gap) else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--outdir", default=None, help="default: next to each input")
    ap.add_argument("--split", action="store_true", help="one CSV per sensor")
    ap.add_argument("--raw", action="store_true", help="raw i16 counts, no scaling")
    a = ap.parse_args()
    rc = 0
    for p in a.files:
        try:
            if Path(p).stat().st_size == 0:
                print(f"{p}: empty session, skipped")
                continue
            rc |= convert(p, Path(a.outdir) if a.outdir else Path(p).parent, a.split, a.raw)
        except (ValueError, OSError) as e:
            print(f"{p}: ERROR {e}", file=sys.stderr)
            rc |= 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
