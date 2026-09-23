#!/usr/bin/env python3
"""Wearable-device simulator: replays example/squats.bin as live UDP traffic.

Two wearable kinds, from one capture (common/kinds.py):

  --devices N   N bilateral units, sync 0xA5, 4 streams each: the capture's
                (source_id, sensor_id) pairs replayed as-is, ids from --base-id.
  --sleeves N   N unilateral knee sleeves, sync 0xA6, 2 streams each.

Everything is decimated to --rate Hz/sensor and given fresh running timestamps
(per-(device,source) us counters, u32 wrap preserved), with optional per-source
clock drift, packet loss, reordering and jitter.

SLEEVE RECIPE (PLAN_unilateral_devices.md s7), so it can be read off here:
  * one sleeve = ONE MCU on ONE leg = one unit, wire identity (0xA6, device_id,
    source_id), named "u<device_id>-<source_id>" ("u31-0") in the reports.
    Consecutive sleeves get consecutive device ids from --sleeve-base-id
    (default --base-id + --devices, so they never collide with the bilateral
    run); all of them sit on --sleeve-source-id.
  * it emits exactly two streams on that source: sensor 1 = THIGH, sensor 2 =
    SHIN (the sleeve sensor map, on every source). Sensor 1 replays the
    capture's (0,2) stream (left_thigh in the default LIMB_MAP) and sensor 2 its
    (0,1) stream (left_shin), so a sleeve carries real thigh/shin motion.
  * the capture is +-16 g / +-2000 dps raw counts; a sleeve at
    --sleeve-accel-fs / --sleeve-gyro-fs (defaults 32 g / 4000 dps) is the SAME
    motion at a different scale, so its counts are multiplied once at load by
    16/fs_g (accel) and 2000/fs_dps (gyro) -- see rescale_counts().
  * --rate, --seed, --duration, --loss, --reorder, --jitter, --drift, --soc,
    --soc-drain and --dead-sensors apply to sleeves exactly as to devices
    (--dead-sensors matches a sleeve's own "src:sen", e.g. '0:2' kills its shin).

Runs on the host, no Docker:
    uv run python simulator/simulate.py --devices 2 --loss 5
    uv run python simulator/simulate.py --devices 1 --sleeves 2 --target 127.0.0.1:5005
"""

from __future__ import annotations

import argparse
import asyncio
import heapq
import random
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))  # allow running without an installed venv

from common import packet  # noqa: E402
from common.kinds import (  # noqa: E402
    ACCEL_FS_ALLOWED,
    GYRO_FS_ALLOWED,
    SYNC_BILATERAL,
    SYNC_UNILATERAL,
    unit_id,
)

SQUATS_BIN = REPO_ROOT / "example" / "squats.bin"
SLOT_S = 0.005  # batch scheduling slot (~5ms): sleep in slots, send everything due
STREAM_KEYS = ((0, 1), (0, 2), (1, 1), (1, 2))  # (source_id, sensor_id)

# The capture is bilateral hardware: fixed +-16 g / +-2000 dps (common/scaling.py).
CAPTURE_ACCEL_FS_G = 16
CAPTURE_GYRO_FS_DPS = 2000

# A sleeve's sensor 1 is the thigh and 2 the shin, on every source. Replay the
# capture's left leg: (0,2) is left_thigh and (0,1) left_shin in the default
# LIMB_MAP, so the sleeve's thigh really is thigh motion.
SLEEVE_SENSOR_SOURCE = {1: (0, 2), 2: (0, 1)}  # sleeve sensor_id -> capture stream


# ------------------------------------------------------------------ data load --

def load_streams(path: Path = SQUATS_BIN) -> dict[tuple[int, int], np.ndarray]:
    """squats.bin -> {(source_id, sensor_id): imu int16[n, 6]} sorted by unwrapped ts."""
    # squats.bin holds 21-byte SD-LOG records, not the 22-byte UDP datagrams this
    # simulator transmits — decode_log() reads the file, encode() emits the wire form.
    size = packet.LOG_REC_SIZE
    raw = np.fromfile(str(path), dtype=np.uint8)
    n = raw.size // size
    data = raw[: n * size].tobytes()
    payloads = [data[i * size : (i + 1) * size] for i in range(n)]
    batch = packet.decode_log(payloads)

    streams: dict[tuple[int, int], np.ndarray] = {}
    native_hz: dict[tuple[int, int], float] = {}
    for key in STREAM_KEYS:
        src, sen = key
        mask = (batch.source_id == src) & (batch.sensor_id == sen)
        if not mask.any():
            raise ValueError(f"{path}: no samples for source={src} sensor={sen}")
        ts = batch.ts_us[mask].astype(np.int64)
        # unwrap u32 -> monotonic i64
        wraps = np.cumsum(np.diff(ts, prepend=ts[0]) < -(2**31))
        ts_unwrapped = ts + wraps * (2**32)
        order = np.argsort(ts_unwrapped, kind="stable")
        streams[key] = batch.imu[mask][order]
        deltas = np.diff(ts_unwrapped[order])
        native_hz[key] = 1e6 / float(np.median(deltas[deltas > 0]))
    load_streams.native_hz = native_hz  # type: ignore[attr-defined]  # for reporting
    return streams


def decimate(streams: dict[tuple[int, int], np.ndarray],
             native_hz: dict[tuple[int, int], float],
             rate: float) -> dict[tuple[int, int], np.ndarray]:
    """Stride-decimate each stream from its native rate to ~rate Hz."""
    out = {}
    for key, imu in streams.items():
        stride = max(1, round(native_hz[key] / rate))
        out[key] = np.ascontiguousarray(imu[::stride])
    return out


def rescale_counts(imu: np.ndarray, accel_fs_g: int, gyro_fs_dps: int) -> np.ndarray:
    """Re-express the capture's raw counts at another IMU full-scale.

    WHY: the datagram carries no scale. squats.bin comes off +-16 g / +-2000 dps
    hardware, where one LSB is 16/32768 g. A sleeve configured at +-32 g /
    +-4000 dps covers twice the range with the same 16 bits, so the SAME
    physical motion reaches the wire as HALVED counts (multiplier 16/fs_g for
    the accel columns, 2000/fs_dps for the gyro columns). Emit the capture's
    counts unscaled and the receiver, which decodes 0xA6 at the unit's
    configured full-scale, would report twice the real acceleration -- and that
    silent factor of two would also mask a genuine ingest scaling bug, which is
    exactly what these packets are meant to exercise.

    Applied once at load/decimate time rather than per packet, for speed.
    Clipped to int16: a full-scale BELOW the capture's (e.g. 2 g) amplifies the
    counts and real hardware would saturate there too.
    """
    accel_mult = CAPTURE_ACCEL_FS_G / float(accel_fs_g)
    gyro_mult = CAPTURE_GYRO_FS_DPS / float(gyro_fs_dps)
    if accel_mult == 1.0 and gyro_mult == 1.0:
        return imu  # same scale as the capture: nothing to do (never mutated)
    scaled = imu.astype(np.float64)
    scaled[:, :3] *= accel_mult
    scaled[:, 3:] *= gyro_mult
    info = np.iinfo(np.int16)
    return np.clip(np.rint(scaled), info.min, info.max).astype(np.int16)


def build_sleeve_streams(streams: dict[tuple[int, int], np.ndarray], source_id: int,
                         accel_fs_g: int, gyro_fs_dps: int,
                         ) -> dict[tuple[int, int], np.ndarray]:
    """The two streams of one sleeve unit, keyed by its own (source_id, sensor_id).

    Sensor 1 (thigh) and 2 (shin) both live on the sleeve's single source, at the
    sleeve's full-scale. Shared by every sleeve: DeviceSim only reads these.
    """
    return {(source_id, sen): rescale_counts(streams[key], accel_fs_g, gyro_fs_dps)
            for sen, key in SLEEVE_SENSOR_SOURCE.items()}


def resolve_sleeve_base_id(args: argparse.Namespace) -> int:
    """--sleeve-base-id, defaulting to just past the bilateral run's ids."""
    if args.sleeve_base_id is not None:
        return int(args.sleeve_base_id)
    return int(args.base_id) + int(args.devices)


# ------------------------------------------------------------------ emitters ---

@dataclass
class _Stream:
    """One (device, source, sensor) emit schedule.

    The device timestamp is derived from the per-(device,source) clock —
    both sensors of a source share the same MCU time base (ts_base/skew) —
    with natural u32 wraparound applied on emit.
    """

    device_id: int
    source_id: int
    sensor_id: int
    imu: np.ndarray            # int16[n, 6] replay samples
    period_s: float            # wall-clock seconds between samples
    next_send: float           # wall-clock time of next sample
    t0: float                  # wall-clock epoch of the source clock
    ts_base: float             # device µs at t0 (shared per source)
    skew: float                # device-clock rate factor (drift, shared per source)
    index: int = 0


@dataclass
class DeviceStats:
    sent: int = 0
    dropped: int = 0
    reordered: int = 0
    window_sent: int = 0


class DeviceSim:
    """Emits payloads for one unit's streams; pure logic, no sockets.

    A bilateral device (sync 0xA5) is built with the capture's four STREAM_KEYS.
    A unilateral sleeve (sync 0xA6) is built with the two-entry dict from
    build_sleeve_streams(), keyed by its own (source_id, 1|2) -- which is all it
    takes for dead sensors, drift, battery, jitter and the emit schedule below
    to apply to both kinds unchanged.
    """

    def __init__(self, device_id: int, streams: dict[tuple[int, int], np.ndarray],
                 rate: float, drift_ppm: float, rng: random.Random, start: float,
                 dead_sensors: frozenset[tuple[int, int]] = frozenset(),
                 soc: int = 100, soc_drain_per_min: float = 0.0,
                 sync: int = SYNC_BILATERAL) -> None:
        self.device_id = device_id
        self.sync = sync
        self.unilateral = sync == SYNC_UNILATERAL
        self.stats = DeviceStats()
        self._streams: list[_Stream] = []
        self._start = start
        self._soc0 = soc
        self._soc_drain = soc_drain_per_min
        # bilateral: the four STREAM_KEYS, in that order; sleeve: its own two
        keys = tuple(sorted(streams))
        sources = sorted({src for src, _ in keys})
        # Reports name a sleeve by its unit id, so "u31-0" is never mistaken for
        # the bilateral device 31 (common/kinds.py, decision D).
        self.label = (unit_id(device_id, sources[0] if sources else 0)
                      if self.unilateral else f"dev{device_id}")
        # one µs counter per (device, source): both sensors of a leg MCU share it
        source_ts0 = {src: float(rng.randrange(0, 2**32)) for src in (0, 1)}
        for src, sen in keys:
            if (src, sen) in dead_sensors:
                continue          # simulated sensor failure: this stream never sends
            # legs drift apart: source 0 runs fast, source 1 slow by drift_ppm/2 each
            skew = 1.0 + (drift_ppm * 1e-6 / 2.0) * (1 if src == 0 else -1)
            period_s = 1.0 / rate
            self._streams.append(_Stream(
                device_id=device_id, source_id=src, sensor_id=sen,
                imu=streams[(src, sen)],
                period_s=period_s,
                next_send=start + rng.uniform(0, period_s),
                t0=start,
                ts_base=source_ts0[src],
                skew=skew,
            ))

    def due_payloads(self, now: float) -> list[bytes]:
        """All payloads whose send time has arrived; advances clocks (loops file)."""
        out: list[bytes] = []
        # Battery: on a bilateral device source 1 drains at 1.5x source 0, so the
        # two leg MCUs differ and the UI's "show the lowest" rule is actually
        # exercised. A sleeve is one MCU with one battery, so it drains evenly.
        elapsed_min = max(0.0, (now - self._start)) / 60.0
        for st in self._streams:
            while st.next_send <= now:
                imu6 = st.imu[st.index % len(st.imu)]
                # device time at the scheduled sample moment, from the source clock
                ts_us = st.ts_base + (st.next_send - st.t0) * 1e6 * st.skew
                faster = st.source_id and not self.unilateral
                drain = self._soc_drain * elapsed_min * (1.5 if faster else 1.0)
                soc = int(max(0, min(100, round(self._soc0 - drain))))
                out.append(packet.encode(st.device_id, st.source_id, st.sensor_id,
                                         int(ts_us) & 0xFFFFFFFF, imu6,  # natural u32 wrap
                                         soc=soc, sync=self.sync))
                st.index += 1
                st.next_send += st.period_s
        return out


# ---------------------------------------------------------------------- main ---

async def run(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    host, _, port = args.target.partition(":")
    addr = (host or "127.0.0.1", int(port or 5005))

    print(f"loading {SQUATS_BIN.name} ...", flush=True)
    raw_streams = load_streams()
    native = load_streams.native_hz  # type: ignore[attr-defined]
    streams = decimate(raw_streams, native, args.rate)
    per_stream = {k: len(v) for k, v in streams.items()}
    print(f"native rates: { {k: round(v, 1) for k, v in native.items()} }; "
          f"decimated to ~{args.rate}Hz; samples/stream: {per_stream}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    loop = asyncio.get_running_loop()
    start = loop.time()

    dead = frozenset(
        (int(pair.split(":")[0]), int(pair.split(":")[1]))
        for pair in (x.strip() for x in args.dead_sensors.split(",")) if pair
    )
    if dead:
        print(f"simulating dead sensors (src,sen): {sorted(dead)}")
    devices = [DeviceSim(args.base_id + d, streams, args.rate, args.drift, rng, start,
                         dead_sensors=dead, soc=args.soc,
                         soc_drain_per_min=args.soc_drain)
               for d in range(args.devices)]
    if args.sleeves:
        sleeve_base = resolve_sleeve_base_id(args)
        sleeve_streams = build_sleeve_streams(streams, args.sleeve_source_id,
                                              args.sleeve_accel_fs, args.sleeve_gyro_fs)
        units = [unit_id(sleeve_base + s, args.sleeve_source_id)
                 for s in range(args.sleeves)]
        print(f"sleeves (sync 0x{SYNC_UNILATERAL:02X}): {', '.join(units)} at "
              f"+-{args.sleeve_accel_fs}g / +-{args.sleeve_gyro_fs}dps; counts x"
              f"{CAPTURE_ACCEL_FS_G / args.sleeve_accel_fs:g} accel, x"
              f"{CAPTURE_GYRO_FS_DPS / args.sleeve_gyro_fs:g} gyro vs the capture; "
              f"sensor 1=thigh from {SLEEVE_SENSOR_SOURCE[1]}, "
              f"2=shin from {SLEEVE_SENSOR_SOURCE[2]}")
        devices += [DeviceSim(sleeve_base + s, sleeve_streams, args.rate, args.drift,
                              rng, start, dead_sensors=dead, soc=args.soc,
                              soc_drain_per_min=args.soc_drain, sync=SYNC_UNILATERAL)
                    for s in range(args.sleeves)]
    delayed: list[tuple[float, int, bytes, int]] = []  # (release_time, seq, payload, dev_idx)
    seq = 0
    last_report = start
    deadline = start + args.duration if args.duration else None

    while deadline is None or loop.time() < deadline:
        now = loop.time()
        for di, dev in enumerate(devices):
            for payload in dev.due_payloads(now):
                if args.loss and rng.random() * 100.0 < args.loss:
                    dev.stats.dropped += 1
                    continue
                delay = 0.0
                if args.reorder and rng.random() * 100.0 < args.reorder:
                    delay = SLOT_S * rng.randint(3, 8)  # hold back 3-8 slots
                    dev.stats.reordered += 1
                if args.jitter:
                    delay += rng.uniform(0, args.jitter / 1000.0)
                if delay > 0.0:
                    seq += 1
                    heapq.heappush(delayed, (now + delay, seq, payload, di))
                else:
                    sock.sendto(payload, addr)
                    dev.stats.sent += 1
                    dev.stats.window_sent += 1
        while delayed and delayed[0][0] <= now:
            _, _, payload, di = heapq.heappop(delayed)
            sock.sendto(payload, addr)
            devices[di].stats.sent += 1
            devices[di].stats.window_sent += 1

        if now - last_report >= 1.0:
            elapsed = now - last_report
            parts = []
            for dev in devices:
                s = dev.stats
                parts.append(f"{dev.label}: {s.window_sent / elapsed:6.0f}Hz "
                             f"sent={s.sent} drop={s.dropped} reord={s.reordered}")
                s.window_sent = 0
            print(" | ".join(parts), flush=True)
            last_report = now

        await asyncio.sleep(SLOT_S)


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter,
                     argparse.RawDescriptionHelpFormatter):
    """Every option's default, plus the module docstring (the sleeve recipe)
    laid out as written instead of reflowed into one block."""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=_HelpFormatter)
    p.add_argument("--devices", type=int, default=1,
                   help="number of simulated bilateral devices (sync 0xA5, 4 sensors)")
    p.add_argument("--base-id", type=int, default=30, help="device_id of the first device")
    p.add_argument("--sleeves", type=int, default=0,
                   help="number of simulated unilateral knee sleeves (sync 0xA6). One "
                        "sleeve is ONE MCU on ONE leg: it emits exactly two streams on "
                        "--sleeve-source-id, sensor 1 = thigh (replaying the capture's "
                        "left_thigh) and sensor 2 = shin (left_shin), and appears "
                        "downstream as the unit 'u<device_id>-<source_id>'")
    p.add_argument("--sleeve-base-id", type=int, default=None,
                   help="device_id of the first sleeve; consecutive sleeves take "
                        "consecutive ids. Unset means --base-id + --devices, so sleeve "
                        "ids never collide with the bilateral run")
    p.add_argument("--sleeve-source-id", type=int, default=0, choices=(0, 1),
                   help="source_id every sleeve transmits on (CONFIG.TXT source_id)")
    p.add_argument("--sleeve-accel-fs", type=int, default=32, choices=ACCEL_FS_ALLOWED,
                   metavar="G",
                   help=f"sleeve accelerometer full-scale in g, one of "
                        f"{list(ACCEL_FS_ALLOWED)}. The capture is "
                        f"+-{CAPTURE_ACCEL_FS_G} g, so its accel counts are multiplied "
                        f"by {CAPTURE_ACCEL_FS_G}/G at load: the same motion, at the "
                        f"sleeve's scale (the datagram carries none)")
    p.add_argument("--sleeve-gyro-fs", type=int, default=4000, choices=GYRO_FS_ALLOWED,
                   metavar="DPS",
                   help=f"sleeve gyroscope full-scale in dps, one of "
                        f"{list(GYRO_FS_ALLOWED)}. The capture is "
                        f"+-{CAPTURE_GYRO_FS_DPS} dps, so its gyro counts are "
                        f"multiplied by {CAPTURE_GYRO_FS_DPS}/DPS at load")
    p.add_argument("--rate", type=float, default=640.0,
                   help="per-sensor sample rate (Hz); 640 = measured device rate")
    p.add_argument("--target", default="127.0.0.1:5010",
                   help="UDP destination host:port")
    p.add_argument("--loss", type=float, default=0.0, help="%% of packets dropped before send")
    p.add_argument("--reorder", type=float, default=0.0, help="%% of packets held back 3-8 slots")
    p.add_argument("--jitter", type=float, default=0.0, help="max extra uniform delay (ms)")
    p.add_argument("--drift", type=float, default=0.0,
                   help="clock skew (ppm) split between the two sources (legs drift "
                        "apart); a sleeve has one source, so it just runs off by ppm/2")
    p.add_argument("--seed", type=int, default=None, help="RNG seed for reproducibility")
    p.add_argument("--duration", type=float, default=None, help="stop after N seconds (default: run forever)")
    p.add_argument("--soc", type=int, default=100,
                   help="starting battery state of charge (0-100) in the trailing "
                        "soc byte of every datagram (TRD §3)")
    p.add_argument("--soc-drain", type=float, default=0.0, dest="soc_drain",
                   help="battery drain per minute; on a bilateral device source 1 drains "
                        "1.5x faster than source 0, so the two leg MCUs diverge and the "
                        "UI's 'show the lowest' rule is exercised. A sleeve is one MCU "
                        "with one battery, so it drains evenly")
    p.add_argument("--dead-sensors", default="",
                   help="simulate sensor failure: comma-separated src:sen pairs that "
                        "never transmit, e.g. '0:1' or '0:1,0:2'. Applies to sleeves "
                        "too, which only have sensors 1 and 2 on --sleeve-source-id. "
                        "Exercises the biomech degradation ladder "
                        "(docs/biomech/SPEC.md §8) on live traffic.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
