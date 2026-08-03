"""Calibrate the biomech reference constants against a REAL worn capture.

Why this exists
---------------
Several constants in `backend/ingest/biomech.py` are anchored to a synthetic
gait generator rather than to this device. `A_DOSE_REF` / `W_DOSE_REF` in
particular set the whole scale of `m3` (accumulated load), and the `m1`/`m2`
bounds set where every activity lands on the 0-100 scale. Until they are
measured on real hardware they are literature-anchored estimates, and this
script is what turns them into measurements.

It replays a capture through the SHIPPED `compute()` -- not a reimplementation
-- so whatever it prints is what the model would actually have produced.

Usage
-----
    uv run python scripts/calibrate_capture.py CAPTURE.bin
    uv run python scripts/calibrate_capture.py CAPTURE.bin --segments segments.txt

`segments.txt` is one `start_s end_s label` per line, e.g.

    0    30   still
    30   90   walk
    90   150  jog
    150  210  run
    210  225  jumps

Without it the whole capture is treated as one segment, which still gives the
dose references but cannot separate activities.

Capture format
--------------
The SD-log layout used by `example/squats.bin`: 21-byte records, no trailing
`soc` byte (that byte exists only on the UDP wire -- TRD §3). Pass `--wire` for
a 22-byte UDP capture instead.

What to do with the output
--------------------------
* `A_DOSE_REF` / `W_DOSE_REF` should be the CUBE-MEAN row of your hardest
  sustained running segment. Cube-mean (E[x^3]^(1/3)), not median: the dose
  integrates every tick and the cubing lets impact ticks dominate, so a median
  understates the driving value several-fold.
* `M1_LO_FLOOR` should sit just below the ring-max of easy walking, so ordinary
  ambulation reads near zero.
* `M1_HI` cannot exceed what the sensor can represent -- see the SATURATION
  block in the output. A resultant above 16*sqrt(3) = 27.7 g cannot be measured
  at all with this part.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from common.config import get_settings          # noqa: E402
from ingest import biomech as B                 # noqa: E402

SD_RECORD = 21
WIRE_RECORD = 22


def load_streams(path: Path, record: int, limb_map: dict) -> tuple[dict, float]:
    """Decode a capture into {limb: float32[n,6]} plus the measured sample rate.

    Field layout is TRD §3: device_id u8 | source_id u8 | sync 0xA5 | header
    (sensor_id bits0-1) | timestamp_us u32 LE | ax..gz 6x i16 LE | crc8.
    """
    raw = np.fromfile(str(path), dtype=np.uint8)
    n = raw.size // record
    if n == 0:
        raise SystemExit(f"{path} holds no {record}-byte records")
    recs = raw[: n * record].reshape(n, record)
    if not (recs[:, 2] == 0xA5).all():
        bad = int((recs[:, 2] != 0xA5).sum())
        print(f"  warning: {bad}/{n} records fail the 0xA5 sync check "
              f"-- wrong --wire setting, or a lossy capture", file=sys.stderr)

    w = recs[:, 2:]

    def i16(lo: int, hi: int) -> np.ndarray:
        return (w[:, lo].astype(np.uint16)
                | (w[:, hi].astype(np.uint16) << 8)).astype(np.int16)

    ts = (w[:, 2].astype(np.uint32) | (w[:, 3].astype(np.uint32) << 8)
          | (w[:, 4].astype(np.uint32) << 16) | (w[:, 5].astype(np.uint32) << 24))
    sid = w[:, 1] & 3
    src = recs[:, 1]
    imu = np.stack([i16(6, 7), i16(8, 9), i16(10, 11),
                    i16(12, 13), i16(14, 15), i16(16, 17)], 1).astype(np.float32)

    streams: dict[str, np.ndarray] = {}
    rates = []
    for (s, k), limb in limb_map.items():
        sel = (src == s) & (sid == k)
        if not sel.any():
            print(f"  warning: no samples for {limb} (source {s}, sensor {k})",
                  file=sys.stderr)
            continue
        streams[limb] = imu[sel]
        t = ts[sel].astype(np.int64)
        dt = np.diff(t)
        dt = dt[(dt > 0) & (dt < 1_000_000)]        # drop wraps and gaps
        if dt.size:
            rates.append(1e6 / float(np.median(dt)))
    if not streams:
        raise SystemExit("no limb produced samples -- check LIMB_MAP and --wire")
    return streams, (float(np.median(rates)) if rates else 640.0)


def decimate(streams: dict, fs: float, target_hz: float) -> tuple[dict, float]:
    """Stride the capture down to the rate the DEVICE actually streams.

    The SD log runs at ~6.4 kHz; the device decimates to ~640 Hz before it
    transmits (TRD §3), and that is the rate the model sees in production.
    It matters here because `m1`/`m2` are a p90 WITHIN each 60 Hz tick, and a
    tick holding 107 samples has a very different p90 to one holding 11 -- so
    replaying the raw log would measure a pipeline that never runs.
    """
    if target_hz <= 0 or fs <= target_hz * 1.5:
        return streams, fs
    step = max(1, int(round(fs / target_hz)))
    return {l: v[::step] for l, v in streams.items()}, fs / step


def replay(streams: dict, fs: float, out_hz: int) -> list[tuple[float, object]]:
    """Feed the capture through the shipped compute() at OUTPUT_HZ."""
    ns = max(1, int(round(fs / out_hz)))
    n_min = min(len(v) for v in streams.values())
    state: dict = {}
    rows = []
    for tk in range(n_min // ns):
        a, b = tk * ns, (tk + 1) * ns
        frames = {l: streams[l][a:b] for l in streams}
        times = {l: 1000.0 + np.arange(a, b) / fs for l in streams}
        rows.append((a / fs, B.compute(frames, state, times)))
    return rows


def stat_block(rows, lo: float, hi: float, label: str) -> None:
    sel = [m for t, m in rows if lo <= t < hi]
    if not sel:
        print(f"  {label:<14} (no ticks in {lo:.0f}-{hi:.0f}s)")
        return
    p = B.DOSE_EXPONENT

    def arr(key):
        return np.array([m.raw.get(key, 0.0) for m in sel], dtype=float)

    a_int, w_int = arr("a_int"), arr("w_int")

    def cube(x):
        return float((np.maximum(x, 0.0) ** p).mean() ** (1.0 / p))

    def med(g):
        xs = [g(m) for m in sel if g(m) is not None]
        return float(np.median(xs)) if xs else float("nan")

    def mx(g):
        xs = [g(m) for m in sel if g(m) is not None]
        return float(np.max(xs)) if xs else float("nan")

    sat = float(np.max(arr("sat_frac")))
    print(f"  {label:<14} {lo:6.1f}-{hi:6.1f}s  n={len(sel):5d}")
    print(f"     a_dyn  tick-median {np.median(a_int):8.3f}   "
          f"cube-mean {cube(a_int):8.3f}   p99 {np.percentile(a_int,99):8.3f}  m/s^2")
    print(f"     |w|    tick-median {np.median(w_int):8.1f}   "
          f"cube-mean {cube(w_int):8.1f}   p99 {np.percentile(w_int,99):8.1f}  deg/s")
    print(f"     m1 med {med(lambda m: m.m1):5.1f} max {mx(lambda m: m.m1):5.1f}   "
          f"m2 med {med(lambda m: m.m2):5.1f} max {mx(lambda m: m.m2):5.1f}   "
          f"m3 max {mx(lambda m: m.m3):5.1f}")
    print(f"     m4 med {med(lambda m: m.m4):5.1f}   m5 med {med(lambda m: m.m5):5.1f}   "
          f"composite med {med(lambda m: m.composite):5.1f} "
          f"max {mx(lambda m: m.composite):5.1f}")
    print(f"     demand med {med(lambda m: m.raw.get('demand')):5.1f}   "
          f"max sat_frac {sat:.4f}"
          f"{'   <-- CLIPPING, m1/m2 are lower bounds' if sat > B.SAT_SUPPRESS_FRACTION else ''}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", type=Path)
    ap.add_argument("--segments", type=Path,
                    help="file of `start_s end_s label` lines")
    ap.add_argument("--wire", action="store_true",
                    help="22-byte UDP records instead of the 21-byte SD log")
    ap.add_argument("--decimate-to", type=float, default=None,
                    help="stride down to this Hz before replay "
                         "(default: EXPECTED_INPUT_HZ, the rate the device streams)")
    args = ap.parse_args()

    settings = get_settings()
    record = WIRE_RECORD if args.wire else SD_RECORD
    print(f"capture       {args.capture}  ({record}-byte records)")
    streams, fs = load_streams(args.capture, record, settings.limb_map)
    print(f"limbs         {', '.join(sorted(streams))}")
    print(f"sample rate   {fs:.1f} Hz measured "
          f"(EXPECTED_INPUT_HZ = {settings.expected_input_hz})")

    target = args.decimate_to if args.decimate_to is not None else settings.expected_input_hz
    streams, fs = decimate(streams, fs, target)
    print(f"replay rate   {fs:.1f} Hz after decimation to ~{target:.0f} Hz")

    rows = replay(streams, fs, settings.output_hz)
    dur = rows[-1][0] if rows else 0.0
    print(f"duration      {dur:.1f} s -> {len(rows)} ticks\n")
    print("current constants: "
          f"M1_LO_FLOOR={B.M1_LO_FLOOR} M1_HI={B.M1_HI} "
          f"M2_LO={B.M2_LO} M2_HI={B.M2_HI}")
    print(f"                   M3_LO={B.M3_LO} M3_HI={B.M3_HI} "
          f"A_DOSE_REF={B.A_DOSE_REF} W_DOSE_REF={B.W_DOSE_REF}\n")

    segs = []
    if args.segments:
        for line in args.segments.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            segs.append((float(parts[0]), float(parts[1]),
                         parts[2] if len(parts) > 2 else "?"))
    else:
        segs = [(0.0, dur + 1.0, "whole capture")]

    print("=" * 78)
    print("PER-SEGMENT STATISTICS")
    print("=" * 78)
    for lo, hi, label in segs:
        stat_block(rows, lo, hi, label)

    print("=" * 78)
    print("SATURATION — the hardware limit that cannot be tuned away")
    print("=" * 78)
    worst = max((m.raw.get("sat_frac", 0.0) for _, m in rows), default=0.0)
    clipped = sum(1 for _, m in rows
                  if m.raw.get("sat_frac", 0.0) > B.SAT_SUPPRESS_FRACTION)
    print(f"  worst tick sat_frac         {worst:.4f}")
    print(f"  ticks above SAT_SUPPRESS    {clipped}/{len(rows)} "
          f"({100.0*clipped/max(len(rows),1):.1f}%)")
    print("  The part is +-16 g per axis, so the largest RESULTANT it can")
    print("  represent is 16*sqrt(3) = 27.7 g. Anything above that is a lower")
    print("  bound however the constants are set (SPEC §3.7, open item 2).")
    print()
    print("=" * 78)
    print("SUGGESTED CONSTANTS")
    print("=" * 78)
    print("  Take A_DOSE_REF / W_DOSE_REF from the CUBE-MEAN row of your")
    print("  hardest sustained running segment, and M1_LO_FLOOR just under the")
    print("  easy-walking a_dyn so ordinary ambulation reads near zero.")
    print("  Then re-run the suite: the golden values WILL move, and per the")
    print("  project rule they are re-measured and recorded, never loosened.")


if __name__ == "__main__":
    main()
