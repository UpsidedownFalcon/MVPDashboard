"""Raw IMU counts -> SI units, and the per-sensor calibration block.

Sensor is an ICM-45686 configured at +-16 g / +-2000 deg/s (docs/biomech/SPEC.md
Section 3.1). Both scale factors are compile-time constants, not config keys:
the ranges are fixed by firmware, and letting them drift per deployment would
make stored metrics incomparable across sessions.

The +-16 g figure is verified against real data, not just the datasheet: the
median resultant accel magnitude over the whole of example/squats.bin is
2046-2050 counts, i.e. 1 g at 2048 LSB/g.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# --- scale factors (SPEC Section 3.1) ---------------------------------------

GRAVITY_MS2 = 9.81
ACCEL_LSB_PER_G = 2048.0          # +-16 g full scale
GYRO_LSB_PER_DPS = 16.384         # +-2000 deg/s full scale

ACCEL_MS2_PER_COUNT = GRAVITY_MS2 / ACCEL_LSB_PER_G     # 4.7900e-3
GYRO_DPS_PER_COUNT = 1.0 / GYRO_LSB_PER_DPS             # 6.1035e-2

# --- saturation (SPEC Section 3.7) ------------------------------------------
# int16 clips at +-32767. A sample is "saturated" when ANY axis is within 1% of
# full scale. Saturated samples are counted, not discarded -- a clipped impact
# is still a real large impact -- but a window with too many of them suppresses
# m1/m2 rather than reporting a silently truncated peak.
FS_COUNTS = 32767.0
SAT_THRESHOLD_COUNTS = 0.99 * FS_COUNTS

# Fraction of saturated samples above which m1/m2 are suppressed.
# NOTE: borrowed, not derived. Comes from a gyroscope clipping study on
# foot-mounted running sensors (cumulative error stays <5% below this fraction)
# and applied here to accelerometer suppression on the shank. Plausible but
# unvalidated for this use -- retune once real running data exists.
SAT_SUPPRESS_FRACTION = 0.026


@dataclass
class Calibration:
    """Per-sensor corrections from the optional still-stand routine (SPEC 3.8).

    Calibration is OPTIONAL: these defaults are a fully working system. It is
    worth doing because m4 and m5 are INTER-SENSOR ratios, so any per-sensor
    gain mismatch lands directly on them as a bias nothing downstream removes.

    k          accel gain, 9.81 / mean(|a|) measured standing still
    gyro_bias  deg/s offset per axis, subtracted before use
    sigma      per-sample dynamic-accel noise SD [m/s^2]; feeds both m1's
               noise-adaptive lower bound and m5's wUSI noise weighting
    """

    k: float = 1.0
    gyro_bias: np.ndarray = field(
        default_factory=lambda: np.zeros(3, dtype=np.float64)
    )
    sigma: float = 0.035

    def as_dict(self) -> dict:
        return {
            "k": float(self.k),
            "gyro_bias": [float(v) for v in self.gyro_bias],
            "sigma": float(self.sigma),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        return cls(
            k=float(d["k"]),
            gyro_bias=np.asarray(d["gyro_bias"], dtype=np.float64),
            sigma=float(d["sigma"]),
        )


DEFAULT_CALIBRATION = Calibration()

# --- validity guards for calibrate() (SPEC Section 3.8) ---------------------
# Reject rather than bake in a bad correction: a rejected calibration keeps the
# defaults, which are known-safe.
#
# CAL_MAX_G_ERROR must be WIDER than the correctable gain range [CAL_K_MIN,
# CAL_K_MAX]: the whole point of the still window is to measure a gain error,
# so the window has to ACCEPT the very sensors calibration exists to fix. At
# the original 0.02 a motionless sensor 2-5% off gravity failed the acceptance
# test every tick, never accumulated a window, and was branded cal_failed at
# 20 s -- while k up to 5% off was declared correctable. 0.06 covers the k
# range plus noise margin; the k guard in finish_calibration stays the
# decisive validator, and beyond 6% the motionless-but-wrong-|a| fault path
# still applies.
CAL_MAX_G_ERROR = 0.06        # |mean|a| - 9.81| / 9.81 above this => faulty
CAL_MAX_ROTATION_DPS = 5.0    # mean |w| above this => athlete moved
CAL_K_MIN, CAL_K_MAX = 0.95, 1.05
