"""Wearable kinds: the bilateral unit and the unilateral knee sleeve.

Both kinds transmit the same 22-byte datagram (common/packet.py) and differ in
exactly three things, all fixed by the sleeve firmware (NYKnicksDataLogger:
src/app_config.h, src/acquisition.c append_sample_, ARCHITECTURE.md D13/D35):

  * the sync byte      -- 0xA5 bilateral, 0xA6 unilateral ("unilateral device
                          marker", firmware decision D13, 2026-09-14);
  * sensor_id meaning  -- a sleeve's sensor 1 is the THIGH (top) and 2 the SHIN
                          (bottom) on every source; the bilateral map is LIMB_MAP;
  * the IMU full-scale -- a sleeve's is configurable per unit (accel 2..32 g,
                          gyro 125..4000 dps, firmware defaults 32 / 4000) and
                          the datagram carries NO scale, so the receiver must
                          know it; the bilateral hardware is fixed at 16 g /
                          2000 dps (common/scaling.py, compile-time by decision).

Identity. A bilateral device is its device_id byte ("30"). A sleeve UNIT is one
MCU on one leg, identified on the wire by (device_id, source_id) and named
"u<device_id>-<source_id>" ("u30-0"), so a sleeve fleet and a bilateral fleet
sharing device ids never collide (decision D, 2026-09-23). Two sleeves with the
same device_id AND source_id are indistinguishable on the wire; operators keep
that pair unique. A RIG is what everything downstream is keyed by (tickers,
biomech sessions, `devices` rows, ticks, cards): a bilateral unit is its own rig,
an unpaired sleeve is its own rig, and two sleeves paired in the dashboard form
one rig named after the host sleeve (decision H). See PLAN_unilateral_devices.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

# --- sync bytes and kind codes -----------------------------------------------

SYNC_BILATERAL = 0xA5
SYNC_UNILATERAL = 0xA6

KIND_BILATERAL = 0
KIND_UNILATERAL = 1
KIND_INVALID = 255  # LUT value for a byte that is neither sync

KIND_NAMES = {KIND_BILATERAL: "bilateral", KIND_UNILATERAL: "unilateral"}
SYNC_BY_KIND = {KIND_BILATERAL: SYNC_BILATERAL, KIND_UNILATERAL: SYNC_UNILATERAL}

# Vectorised sync -> kind lookup used by packet._decode on the whole batch.
SYNC_LUT = np.full(256, KIND_INVALID, dtype=np.uint8)
SYNC_LUT[SYNC_BILATERAL] = KIND_BILATERAL
SYNC_LUT[SYNC_UNILATERAL] = KIND_UNILATERAL

# --- full-scale ---------------------------------------------------------------
# Allowed sets mirror the firmware's imu_fs_valid() (imu_icm45686.c) and the
# CONFIG.TXT keys accel_fs_g / gyro_fs_dps. The conversion is the firmware's:
# g = raw * fs_g / 32768, dps = raw * fs_dps / 32768 (README s10.2).

ACCEL_FS_ALLOWED = (2, 4, 8, 16, 32)
GYRO_FS_ALLOWED = (125, 250, 500, 1000, 2000, 4000)
COUNTS_PER_FULL_SCALE = 32768.0


def lsb_per_g(fs_g: int) -> float:
    return COUNTS_PER_FULL_SCALE / float(fs_g)


def lsb_per_dps(fs_dps: int) -> float:
    return COUNTS_PER_FULL_SCALE / float(fs_dps)


# --- rig shape ----------------------------------------------------------------

BILATERAL_EXPECTED_LIMBS = 4   # the bilateral hardware carries four IMUs
UNIT_LIMBS = 2                 # a sleeve carries thigh + shin
SIDES = ("left", "right")

# --- identities ---------------------------------------------------------------

UNIT_PREFIX = "u"
_UNIT_ID_RE = re.compile(r"^u(\d{1,3})-([01])$")


def unit_id(device_id: int, source_id: int) -> str:
    """Wire identity of one sleeve: "u<device_id>-<source_id>"."""
    return f"{UNIT_PREFIX}{int(device_id)}-{int(source_id)}"


def parse_unit_id(text: str) -> tuple[int, int] | None:
    """(device_id, source_id) for a well-formed unit id, else None."""
    m = _UNIT_ID_RE.match(text)
    if m is None:
        return None
    dev, src = int(m.group(1)), int(m.group(2))
    if dev > 255:
        return None
    return dev, src


def is_unit_id(text: str) -> bool:
    return parse_unit_id(text) is not None


def rig_kind(rig_id: str) -> str:
    """'unilateral' for a sleeve rig (unit-id shaped), else 'bilateral'.

    Bilateral rig ids are the decimal device byte, so the "u" prefix is
    unambiguous. Kind is derived, never stored (BACKEND_SCHEMA s1 unchanged).
    """
    if rig_id.startswith(UNIT_PREFIX):
        return KIND_NAMES[KIND_UNILATERAL]
    return KIND_NAMES[KIND_BILATERAL]


# --- per-unit configuration ---------------------------------------------------

UNIT_CONFIG_VERSION = 1


@dataclass(frozen=True)
class UnitConfig:
    """Dashboard-owned settings of one sleeve, mirrored api -> Redis -> ingest.

    `rig_id == unit_id` means unpaired. `side is None` means the operator has
    not set it yet (decision G): the unit then streams side-less limbs
    ("thigh", "shin") that biomech treats as neither left nor right.
    """

    unit_id: str
    rig_id: str
    side: str | None
    accel_fs_g: int
    gyro_fs_dps: int

    @classmethod
    def default(cls, unit: str, accel_fs_g: int, gyro_fs_dps: int) -> "UnitConfig":
        return cls(unit_id=unit, rig_id=unit, side=None,
                   accel_fs_g=accel_fs_g, gyro_fs_dps=gyro_fs_dps)

    @property
    def paired(self) -> bool:
        return self.rig_id != self.unit_id

    @property
    def vsrc(self) -> int:
        """Virtual source_id inside the rig: left or side-less -> 0, right -> 1,
        so a paired rig presents (0,1),(0,2),(1,1),(1,2) like a bilateral unit."""
        return 1 if self.side == "right" else 0

    def limb_map(self, sensor_map: dict[int, str]) -> dict[tuple[int, int], str]:
        """(vsrc, sensor_id) -> limb name for this unit's sensors."""
        return {
            (self.vsrc, sen): (f"{self.side}_{seg}" if self.side else seg)
            for sen, seg in sensor_map.items()
        }

    def limb_scale(self) -> tuple[float, float]:
        """(lsb_per_g, lsb_per_dps) the unit's raw counts are in."""
        return lsb_per_g(self.accel_fs_g), lsb_per_dps(self.gyro_fs_dps)

    def to_json(self) -> dict:
        return {
            "unit": self.unit_id,
            "rig": self.rig_id,
            "side": self.side,
            "accel_fs_g": self.accel_fs_g,
            "gyro_fs_dps": self.gyro_fs_dps,
            "v": UNIT_CONFIG_VERSION,
        }

    @classmethod
    def from_json(cls, data: dict) -> "UnitConfig":
        """Strict parse; raises ValueError on anything the pipeline cannot use."""
        try:
            unit = str(data["unit"])
            rig = str(data["rig"])
            side = data.get("side")
            accel = int(data["accel_fs_g"])
            gyro = int(data["gyro_fs_dps"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"unit config missing or malformed field: {exc}") from exc
        if not is_unit_id(unit) or not is_unit_id(rig):
            raise ValueError(f"unit config ids must be unit ids: {unit!r}, {rig!r}")
        if side is not None and side not in SIDES:
            raise ValueError(f"unit config side must be left/right/null, got {side!r}")
        if accel not in ACCEL_FS_ALLOWED:
            raise ValueError(f"accel_fs_g {accel} not in {ACCEL_FS_ALLOWED}")
        if gyro not in GYRO_FS_ALLOWED:
            raise ValueError(f"gyro_fs_dps {gyro} not in {GYRO_FS_ALLOWED}")
        return cls(unit_id=unit, rig_id=rig, side=side, accel_fs_g=accel, gyro_fs_dps=gyro)
