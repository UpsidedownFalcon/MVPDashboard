#!/usr/bin/env python3
"""Wearable-device simulator: replays example/squats.bin as live UDP traffic.

N simulated devices × 4 sensor streams, decimated to --rate Hz/sensor, with
fresh running timestamps (per-(device,source) µs counters, u32 wrap preserved),
optional per-source clock drift, packet loss, reordering and jitter.

Runs on the host, no Docker:
    uv run python simulator/simulate.py --devices 2 --loss 5
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

SQUATS_BIN = REPO_ROOT / "example" / "squats.bin"
SLOT_S = 0.005  # batch scheduling slot (~5ms): sleep in slots, send everything due
STREAM_KEYS = ((0, 1), (0, 2), (1, 1), (1, 2))  # (source_id, sensor_id)


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
    """Emits payloads for one device's 4 streams; pure logic, no sockets."""

    def __init__(self, device_id: int, streams: dict[tuple[int, int], np.ndarray],
                 rate: float, drift_ppm: float, rng: random.Random, start: float,
                 dead_sensors: frozenset[tuple[int, int]] = frozenset()) -> None:
        self.device_id = device_id
        self.stats = DeviceStats()
        self._streams: list[_Stream] = []
        # one µs counter per (device, source): both sensors of a leg MCU share it
        source_ts0 = {src: float(rng.randrange(0, 2**32)) for src in (0, 1)}
        for src, sen in STREAM_KEYS:
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
        for st in self._streams:
            while st.next_send <= now:
                imu6 = st.imu[st.index % len(st.imu)]
                # device time at the scheduled sample moment, from the source clock
                ts_us = st.ts_base + (st.next_send - st.t0) * 1e6 * st.skew
                out.append(packet.encode(st.device_id, st.source_id, st.sensor_id,
                                         int(ts_us) & 0xFFFFFFFF, imu6))  # natural u32 wrap
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
                         dead_sensors=dead)
               for d in range(args.devices)]
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
                parts.append(f"dev{dev.device_id}: {s.window_sent / elapsed:6.0f}Hz "
                             f"sent={s.sent} drop={s.dropped} reord={s.reordered}")
                s.window_sent = 0
            print(" | ".join(parts), flush=True)
            last_report = now

        await asyncio.sleep(SLOT_S)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--devices", type=int, default=1, help="number of simulated devices")
    p.add_argument("--base-id", type=int, default=30, help="device_id of the first device")
    p.add_argument("--rate", type=float, default=600.0, help="per-sensor sample rate (Hz)")
    p.add_argument("--target", default="127.0.0.1:5005", help="UDP destination host:port")
    p.add_argument("--loss", type=float, default=0.0, help="%% of packets dropped before send")
    p.add_argument("--reorder", type=float, default=0.0, help="%% of packets held back 3-8 slots")
    p.add_argument("--jitter", type=float, default=0.0, help="max extra uniform delay (ms)")
    p.add_argument("--drift", type=float, default=0.0,
                   help="clock skew (ppm) split between the two sources (legs drift apart)")
    p.add_argument("--seed", type=int, default=None, help="RNG seed for reproducibility")
    p.add_argument("--duration", type=float, default=None, help="stop after N seconds (default: run forever)")
    p.add_argument("--dead-sensors", default="",
                   help="simulate sensor failure: comma-separated src:sen pairs that "
                        "never transmit, e.g. '0:1' or '0:1,0:2'. Exercises the biomech "
                        "degradation ladder (docs/biomech/SPEC.md §8) on live traffic.")
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
