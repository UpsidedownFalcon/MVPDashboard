"""Biomech metric computation — STUB (S1-T10), replaced wholesale in S1-T15.

Stable interface (BACKEND_SCHEMA.md §5): compute(frames, state) -> Metrics.
Only this file changes when the real algorithm lands (from docs/biomech/SPEC.md).

Stub mapping (shape-realistic placeholder):
  m1..m4 = per-limb gyro RMS (left_thigh, left_shin, right_thigh, right_shin)
  m5     = mean accel-magnitude RMS across limbs
  all EMA-smoothed (alpha ~0.2) and squashed to ~0..1 via x/(x+k);
  composite = weighted mean (0.15/0.15/0.15/0.15/0.4).
Empty frames -> repeat previous values (held).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Limb order for m1..m4 (fixed by the task spec)
M_LIMBS = ("left_thigh", "left_shin", "right_thigh", "right_shin")

EMA_ALPHA = 0.2
# Squash scale k: raw gyro counts RMS during motion is O(1000-10000);
# x/(x+k) maps that into a useful 0..1 range.
GYRO_K = 4000.0
ACCEL_K = 6000.0
WEIGHTS = np.array([0.15, 0.15, 0.15, 0.15, 0.4])


@dataclass
class Metrics:
    m1: float
    m2: float
    m3: float
    m4: float
    m5: float
    composite: float

    def as_list(self) -> list[float]:
        return [self.m1, self.m2, self.m3, self.m4, self.m5]


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if x.size else 0.0


def compute(frames: dict[str, np.ndarray], state: dict) -> Metrics:
    """frames: limb name -> float32[n_samples, 6] (ax..gz, raw counts) since last
    tick, already time-aligned across limbs. Called at OUTPUT_HZ per device.
    Returns Metrics(m1..m5, composite). `state` persists across calls per device
    (for filters/calibration)."""
    total_samples = sum(f.shape[0] for f in frames.values())
    prev: Metrics | None = state.get("prev")
    if total_samples == 0:
        # held tick: repeat previous values
        if prev is not None:
            return prev
        zero = Metrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        state["prev"] = zero
        return zero

    # raw per-limb energies
    gyro_rms = np.zeros(4)
    accel_rms_parts = []
    for i, limb in enumerate(M_LIMBS):
        f = frames.get(limb)
        if f is None or f.shape[0] == 0:
            continue
        gyro_rms[i] = _rms(f[:, 3:6])
        accel_mag = np.linalg.norm(f[:, 0:3].astype(np.float64), axis=1)
        accel_rms_parts.append(float(np.sqrt(np.mean(accel_mag**2))))
    accel_rms = float(np.mean(accel_rms_parts)) if accel_rms_parts else 0.0

    raw = np.empty(5)
    raw[:4] = gyro_rms / (gyro_rms + GYRO_K)
    raw[4] = accel_rms / (accel_rms + ACCEL_K)

    ema = state.get("ema")
    ema = raw.copy() if ema is None else (1 - EMA_ALPHA) * ema + EMA_ALPHA * raw
    state["ema"] = ema

    composite = float(np.dot(WEIGHTS, ema))
    metrics = Metrics(*(float(v) for v in ema), composite)
    state["prev"] = metrics
    return metrics
