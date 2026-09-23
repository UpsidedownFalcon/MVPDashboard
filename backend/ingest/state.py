"""Per-rig / per-sensor registry, routing and stats (TRD §4, S1-T06).

The router receives decoded Batches and distributes samples to SensorState
pending queues, auto-creating rig/sensor state on first sight. Downstream
stages (align/jitter/ticker, T07-T09) consume the pending chunks.

A RIG is everything downstream is keyed by: one ticker, one biomech session,
one `devices` row, one card. Two wearable kinds map onto it (common/kinds.py):

  * a bilateral unit is one rig, id = its device_id byte ("30"), sensors keyed
    by the real (source_id, sensor_id) through LIMB_MAP;
  * a knee sleeve is a UNIT, id "u<device>-<source>". Unpaired it is its own
    rig; paired in the dashboard, two sleeves share the host's rig id. Inside a
    sleeve rig each unit is mapped to a VIRTUAL source (left or side-less -> 0,
    right -> 1), so a paired rig presents (0,1),(0,2),(1,1),(1,2) exactly like
    a bilateral unit and every per-source mechanism below -- battery, sensor
    stats, last_seen keys -- keeps working unchanged.

Because a sleeve's rig membership, side and full-scale come from the dashboard
rather than the wire, the routing tables are rebuilt whenever a unit's config
changes (apply_unit_config), which restarts that rig's session by design.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from common.config import (
    DEFAULT_LIMB_MAP,
    DEFAULT_UNILATERAL_ACCEL_FS_G,
    DEFAULT_UNILATERAL_GYRO_FS_DPS,
    DEFAULT_UNILATERAL_SENSOR_MAP,
)
from common.kinds import (
    BILATERAL_EXPECTED_LIMBS,
    KIND_BILATERAL,
    KIND_NAMES,
    KIND_UNILATERAL,
    UNIT_LIMBS,
    UnitConfig,
    rig_kind,
    unit_id as make_unit_id,
)
from common.packet import Batch
from common.scaling import SAT_THRESHOLD_COUNTS
from ingest.unit_config import UnitConfigCache

log = logging.getLogger("ingest.state")

# Cap on buffered-but-unconsumed sample chunks per sensor (~2s at 600Hz comes to
# ~200 drain chunks; keep headroom, drop-oldest beyond and count).
PENDING_MAXCHUNKS = 512

# How long after a configuration change a rebuilt rig refuses to restore its
# Redis snapshot. A config change means the rig is a different shape or its
# counts mean something different, so the old dose and baselines are not
# comparable (user decision N). The snapshot is deleted too, but it was written
# up to a second earlier and the rig rebuilds within ~10 ms, so the restorer
# would otherwise race the delete and resurrect it.
RESET_SKIP_RESTORE_S = 2.0


@dataclass
class SensorStats:
    recv: int = 0            # valid samples routed to this sensor
    rate_hz: float = 0.0     # measured over the last stats interval
    crc_fail: int = 0        # batch-level share, attributed by the router owner
    bad_sync: int = 0
    late_drop: int = 0       # filled by the jitter buffer (T08)
    pending_drop: int = 0    # pending-queue overflow (this file)
    jitter_drop: int = 0     # jitter-buffer capacity overflow, mirrored by the ticker
    sat_count: int = 0       # samples within 1% of full scale (biomech SPEC §3.7)
    _recv_at_last_rate: int = 0

    @property
    def buf_drop(self) -> int:
        """Total buffer-overflow drops for this sensor (BACKEND_SCHEMA §4).

        Two independent bounded buffers can overflow — the pending queue here
        and the jitter buffer downstream — and they overflow for the same
        reason: load. They are summed rather than sharing one field because the
        ticker mirrors the jitter buffer's running total by assignment, which
        used to overwrite the pending count and hide it exactly when the system
        was busy enough for it to matter.
        """
        return self.pending_drop + self.jitter_drop


@dataclass
class SampleChunk:
    """A drained batch slice for one sensor: shared recv_time, per-sample ts/imu."""

    recv_time: float         # server wall-clock when the chunk was drained
    ts_us: np.ndarray        # u32[n] raw device timestamps
    imu: np.ndarray          # int16[n, 6]


class SensorState:
    def __init__(self, source_id: int, sensor_id: int) -> None:
        self.source_id = source_id
        self.sensor_id = sensor_id
        self.stats = SensorStats()
        self.pending: deque[SampleChunk] = deque(maxlen=PENDING_MAXCHUNKS)
        self.last_seen: float = 0.0

    def append(self, chunk: SampleChunk) -> None:
        if len(self.pending) >= PENDING_MAXCHUNKS:
            dropped = self.pending[0]
            self.stats.pending_drop += len(dropped.ts_us)
        self.pending.append(chunk)
        self.stats.recv += len(chunk.ts_us)
        self.last_seen = chunk.recv_time

    def drain_pending(self) -> list[SampleChunk]:
        chunks = list(self.pending)
        self.pending.clear()
        return chunks


class DeviceState:
    def __init__(
        self,
        device_id: str,
        *,
        kind: int = KIND_BILATERAL,
        limb_map: dict[tuple[int, int], str] | None = None,
        expected_limbs: int = BILATERAL_EXPECTED_LIMBS,
        limb_scale: dict[str, tuple[float, float]] | None = None,
        units: tuple[str, ...] = (),
        skip_restore: bool = False,
    ) -> None:
        self.device_id = device_id
        self.kind = kind
        # Routing tables for THIS rig, resolved once when it is created: the
        # ticker frames by `limb_map`, biomech judges completeness by
        # `expected_limbs` and converts counts with `limb_scale`. They are
        # per-rig rather than global because two rigs of different kinds stream
        # into the same process (see the module docstring).
        self.limb_map = limb_map if limb_map is not None else {}
        self.expected_limbs = expected_limbs
        self.limb_scale = limb_scale
        self.units = units                      # sleeve units, host first; () bilateral
        # Set when this rig was rebuilt right after a configuration change: its
        # stored session describes a different rig and must not come back.
        self.skip_restore = skip_restore
        self.unit_last_seen: dict[str, float] = {}
        self.unit_soc: dict[str, int] = {}
        self.sensors: dict[tuple[int, int], SensorState] = {}
        self.ticks_out = 0       # advanced by the ticker (T09)
        self.tick_rate: float = 0.0
        self.quality_ema: float | None = None
        self._ticks_at_last_rate = 0
        self.last_seen: float = 0.0
        self.user_state: dict = {}   # biomech session state (T15)
        self.last_metrics = None     # latest Metrics, for the 1s diag publish
        # Battery state of charge, per leg MCU: {source_id: 0..100}. The wire
        # carries one `soc` byte per datagram (TRD §3) and each source_id is a
        # separate MCU with its own battery, so they are tracked separately and
        # the UI shows the LOWEST -- a dying leg unit must not hide behind a
        # healthy one.
        self.soc: dict[int, int] = {}

    def sensor(self, source_id: int, sensor_id: int) -> SensorState:
        key = (source_id, sensor_id)
        state = self.sensors.get(key)
        if state is None:
            state = self.sensors[key] = SensorState(source_id, sensor_id)
            log.info("device %s: new sensor (source=%d, sensor=%d)",
                     self.device_id, source_id, sensor_id)
        return state


class Registry:
    """All device state + global counters; owns batch routing."""

    def __init__(
        self,
        max_devices: int | None = None,
        *,
        limb_map: dict[tuple[int, int], str] | None = None,
        unilateral_sensor_map: dict[int, str] | None = None,
        unit_cfg=None,
    ) -> None:
        self.devices: dict[str, DeviceState] = {}
        # Bad records lose trustworthy identity, so malformed counters are global.
        self.crc_fail = 0
        self.bad_sync = 0
        self.bad_len = 0
        self.recv_by_kind: dict[int, int] = {}
        self.max_devices = max_devices
        self.offline_after_s = 2.0   # set from Settings by the caller
        self.dev_dropped = 0         # packets for devices beyond the cap
        self._capped_logged: set[str] = set()
        self._collision_logged: set[str] = set()
        # Defaults keep a bare Registry() usable (tests, tools); ingest passes
        # the real Settings-derived tables.
        self.limb_map = dict(DEFAULT_LIMB_MAP if limb_map is None else limb_map)
        self.unilateral_sensor_map = dict(
            DEFAULT_UNILATERAL_SENSOR_MAP if unilateral_sensor_map is None
            else unilateral_sensor_map)
        self.unit_cfg = unit_cfg if unit_cfg is not None else UnitConfigCache(
            DEFAULT_UNILATERAL_ACCEL_FS_G, DEFAULT_UNILATERAL_GYRO_FS_DPS)
        self.reset_at: dict[str, float] = {}
        self.on_new_device = None       # optional callback(device: DeviceState)
        self.on_device_removed = None   # optional callback(device_id: str)
        self.on_rig_reset = None        # optional callback(rig_id: str)

    def _remove(self, device_id: str, why: str) -> None:
        self.devices.pop(device_id, None)
        self._capped_logged.discard(device_id)
        log.info("device %s released (%s)", device_id, why)
        if self.on_device_removed is not None:
            self.on_device_removed(device_id)

    # --- rig shape --------------------------------------------------------------

    def resolve(self, kind: int, device_id: int, source_id: int) -> tuple[str, int, str | None]:
        """Wire identity -> (rig_id, virtual source_id, unit_id or None).

        A bilateral datagram keeps its device byte and its real source. A sleeve
        datagram is looked up by unit id, so the dashboard decides which rig it
        joins and which side (hence which virtual source) it occupies.
        """
        if kind == KIND_BILATERAL:
            return str(device_id), source_id, None
        unit = make_unit_id(device_id, source_id)
        cfg: UnitConfig = self.unit_cfg.get(unit)
        return cfg.rig_id, cfg.vsrc, unit

    def _build_rig(self, rig_id: str, skip_restore: bool) -> DeviceState:
        """Resolve a rig's limb map, scale and expected size from configuration.

        Built from CONFIG, not from traffic: a paired rig knows both its sleeves
        before the second one has sent a packet, so its biomech session starts
        with the full limb set instead of being rebuilt (and zeroed) when the
        other leg joins.
        """
        if rig_kind(rig_id) == KIND_NAMES[KIND_BILATERAL]:
            return DeviceState(rig_id, kind=KIND_BILATERAL, limb_map=dict(self.limb_map),
                               expected_limbs=BILATERAL_EXPECTED_LIMBS)
        limb_map: dict[tuple[int, int], str] = {}
        limb_scale: dict[str, tuple[float, float]] = {}
        units: list[str] = []
        for cfg in self.unit_cfg.members(rig_id):
            candidate = cfg.limb_map(self.unilateral_sensor_map)
            # Duplicate limb names would make one sleeve overwrite the other's
            # frame and rebuild the biomech session 60 times a second (the
            # hazard common/config.py's uniqueness validator exists for). The
            # api refuses to create this state; if it appears anyway, drop the
            # offending member rather than build the broken map.
            if (candidate.keys() & limb_map.keys()
                    or set(candidate.values()) & set(limb_map.values())):
                if rig_id not in self._collision_logged:
                    self._collision_logged.add(rig_id)
                    log.error("rig %s: unit %s collides with the rig's existing limbs "
                              "%s — unit ignored. Set its side in the dashboard.",
                              rig_id, cfg.unit_id, sorted(limb_map.values()))
                continue
            limb_map.update(candidate)
            for limb in candidate.values():
                limb_scale[limb] = cfg.limb_scale()
            units.append(cfg.unit_id)
        if not limb_map:
            # Only reachable from an inconsistent configuration (a host that is
            # itself paired elsewhere). The rig exists so its packets are not
            # silently attributed to someone else, but it maps nothing.
            log.error("rig %s: no unit claims it — check its pairing in the "
                      "dashboard; it will produce no metrics", rig_id)
        return DeviceState(
            rig_id,
            kind=KIND_UNILATERAL,
            limb_map=limb_map,
            expected_limbs=UNIT_LIMBS * max(len(units), 1),
            limb_scale=limb_scale,
            units=tuple(units),
            skip_restore=skip_restore,
        )

    def apply_unit_config(self, cfg: UnitConfig, now: float | None = None) -> bool:
        """Adopt a sleeve's dashboard settings; returns True if anything changed.

        A change to pairing, side or full-scale changes the rig's shape or the
        meaning of its counts, so both the rig the unit left and the rig it
        joined are torn down. They rebuild on the next packet, with a fresh
        session: dose, learned baselines and calibration measured under the old
        configuration are not comparable to the new one (user decision N).
        """
        previous = self.unit_cfg.set(cfg)
        if previous is None:
            return False
        now = time.time() if now is None else now
        for rig_id in dict.fromkeys((previous.rig_id, cfg.rig_id)):
            self.reset_at[rig_id] = now
            if rig_id in self.devices:
                self._remove(rig_id, f"unit {cfg.unit_id} reconfigured")
            if self.on_rig_reset is not None:
                self.on_rig_reset(rig_id)
        return True

    def device(self, device_id: str, now: float | None = None) -> DeviceState | None:
        """Existing rig, or a new one — displacing an OFFLINE one if at cap.

        Returns None only when the cap is reached and every tracked rig is
        still live; those packets are dropped and counted, never silently mixed
        into another rig's stream (biomech SPEC §7.2).

        Displacing the longest-silent offline rig matters for usability: a
        trainer swapping a wearable would otherwise wait out the whole session
        gap before the replacement could register.
        """
        device_id = str(device_id)
        state = self.devices.get(device_id)
        if state is not None:
            return state
        now = time.time() if now is None else now
        if self.max_devices is not None and len(self.devices) >= self.max_devices:
            offline = [(d.last_seen, i) for i, d in self.devices.items()
                       if not d.last_seen or now - d.last_seen > self.offline_after_s]
            if not offline:
                self.dev_dropped += 1
                if device_id not in self._capped_logged:
                    self._capped_logged.add(device_id)
                    log.warning("device %s ignored: MAX_DEVICES=%d, all live",
                                device_id, self.max_devices)
                return None
            self._remove(min(offline)[1], f"displaced by device {device_id}")
        reset_at = self.reset_at.get(device_id)
        skip_restore = reset_at is not None and now - reset_at <= RESET_SKIP_RESTORE_S
        state = self.devices[device_id] = self._build_rig(device_id, skip_restore)
        log.info("new device: %s (%s, %d limb(s) mapped)", device_id,
                 rig_kind(device_id), len(state.limb_map))
        if self.on_new_device is not None:
            self.on_new_device(state)
        return state

    def route(self, batch: Batch, recv_time: float) -> None:
        """Distribute one decoded batch to per-sensor pending queues (vectorized)."""
        self.crc_fail += batch.n_bad_crc
        self.bad_sync += batch.n_bad_sync
        self.bad_len += batch.n_bad_len
        if batch.n == 0:
            return

        # The kind rides in bits 24+ (free: the other three fields are bytes),
        # so a bilateral device 30 and a sleeve with device byte 30 sort into
        # separate groups and can never be merged into one stream.
        key = (
            batch.kind.astype(np.int64) << 24
            | batch.device_id.astype(np.int64) << 16
            | batch.source_id.astype(np.int64) << 8
            | batch.sensor_id.astype(np.int64)
        )
        order = np.argsort(key, kind="stable")
        sorted_key = key[order]
        boundaries = np.nonzero(np.diff(sorted_key))[0] + 1
        starts = np.concatenate(([0], boundaries))
        ends = np.concatenate((boundaries, [len(sorted_key)]))

        for s, e in zip(starts, ends):
            idx = order[s:e]
            k = int(sorted_key[s])
            kind = k >> 24
            device_id, source_id, sensor_id = (k >> 16) & 0xFF, (k >> 8) & 0xFF, k & 0xFF
            self.recv_by_kind[kind] = self.recv_by_kind.get(kind, 0) + len(idx)
            rig_id, vsrc, unit = self.resolve(kind, device_id, source_id)
            device = self.device(rig_id, now=recv_time)
            if device is None:
                continue
            device.last_seen = recv_time
            if unit is not None:
                device.unit_last_seen[unit] = recv_time
            # Battery: the newest datagram in this slice wins. Cheap (one
            # array index) and it is the only place the decoded `soc` is still
            # in scope -- SampleChunk deliberately does not carry it, since it
            # is per-device telemetry, not a per-sample signal.
            if len(batch.soc):
                soc = int(batch.soc[idx[-1]])
                device.soc[vsrc] = soc
                if unit is not None:
                    device.unit_soc[unit] = soc
            sensor = device.sensor(vsrc, sensor_id)
            imu = batch.imu[idx]
            # Clipping is unrecoverable and a clipped impact still matters, so
            # count it rather than dropping it; once the saturated fraction gets
            # high, biomech MARKS m1/m2 AS LOWER BOUNDS (the `saturated` flag,
            # rendered ">= x") rather than suppressing them -- suppression to
            # null was removed 2026-08-03 (SPEC §3.7).
            sensor.stats.sat_count += int(
                (np.abs(imu) >= SAT_THRESHOLD_COUNTS).any(axis=1).sum()
            )
            sensor.append(SampleChunk(
                recv_time=recv_time,
                ts_us=batch.ts_us[idx],
                imu=imu,
            ))

    def evict_stale(self, now: float, max_age_s: float) -> list[str]:
        """Release devices silent for longer than max_age_s; returns their ids.

        Without this, MAX_DEVICES counts devices that went offline hours ago and
        a genuinely new device is refused a slot forever. The eviction horizon is
        SESSION_GAP_S, which is also when biomech would discard the session's
        accumulated load anyway — so nothing recoverable is lost, and a device
        returning inside the snapshot TTL still restores its session (SPEC §7.4).
        """
        stale = [
            device_id for device_id, device in self.devices.items()
            if device.last_seen and now - device.last_seen > max_age_s
        ]
        for device_id in stale:
            self._remove(device_id, f"silent {max_age_s:.0f}s")
        return stale

    def update_rates(self, interval_s: float) -> None:
        """Recompute per-sensor rate_hz over the last stats interval."""
        for device in self.devices.values():
            device.tick_rate = (device.ticks_out - device._ticks_at_last_rate) / interval_s
            device._ticks_at_last_rate = device.ticks_out
            for sensor in device.sensors.values():
                st = sensor.stats
                st.rate_hz = (st.recv - st._recv_at_last_rate) / interval_s
                st._recv_at_last_rate = st.recv

    def summary_lines(self) -> list[str]:
        lines = []
        for device_id in sorted(self.devices):
            device = self.devices[device_id]
            parts = [
                f"s({src},{sen})={sensor.stats.rate_hz:5.0f}Hz"
                for (src, sen), sensor in sorted(device.sensors.items())
            ]
            drops = sum(s.stats.buf_drop for s in device.sensors.values())
            late = sum(s.stats.late_drop for s in device.sensors.values())
            quality = f"{device.quality_ema:.2f}" if device.quality_ema is not None else "-"
            lines.append(
                f"dev {device_id}: {' '.join(parts)} tick={device.tick_rate:5.1f}Hz"
                f" q={quality} ticks_out={device.ticks_out}"
                f" late_drop={late} buf_drop={drops}"
            )
        by_kind = " ".join(
            f"{KIND_NAMES.get(kind, kind)}={n}"
            for kind, n in sorted(self.recv_by_kind.items())
        )
        lines.append(
            f"global: crc_fail={self.crc_fail} bad_sync={self.bad_sync} "
            f"bad_len={self.bad_len}" + (f" recv[{by_kind}]" if by_kind else "")
        )
        return lines
