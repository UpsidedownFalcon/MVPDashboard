"""Shared fixtures and synthetic-frame builders for the backend tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from common.config import Settings
from common.scaling import ACCEL_MS2_PER_COUNT, GYRO_DPS_PER_COUNT

REPO_ROOT = Path(__file__).resolve().parents[2]
SQUATS_BIN = REPO_ROOT / "example" / "squats.bin"

LIMBS = ("left_shin", "left_thigh", "right_shin", "right_thigh")


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


class FakeClock:
    """Deterministic clock: sleep() advances time instantly (yields once)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    async def sleep(self, d: float) -> None:
        self.t += max(d, 0.0)
        await asyncio.sleep(0)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


# --- synthetic frame builders ------------------------------------------------
# Frames are RAW COUNTS, matching what the ticker hands biomech.compute().

def counts_from_si(a_ms2: np.ndarray, w_dps: np.ndarray) -> np.ndarray:
    """[n,3] m/s^2 + [n,3] deg/s -> float32[n,6] raw counts."""
    n = len(a_ms2)
    out = np.zeros((n, 6), dtype=np.float32)
    out[:, 0:3] = a_ms2 / ACCEL_MS2_PER_COUNT
    out[:, 3:6] = w_dps / GYRO_DPS_PER_COUNT
    return out


def make_tick(a_ms2, w_dps, limbs=LIMBS, t0: float = 0.0, fs: float = 600.0):
    """Same SI signal on every limb -> (frames, times) for one compute() call."""
    n = len(a_ms2)
    f = counts_from_si(np.asarray(a_ms2, float), np.asarray(w_dps, float))
    times = t0 + np.arange(n) / fs
    return ({limb: f.copy() for limb in limbs},
            {limb: times.copy() for limb in limbs})


def still_tick(n: int = 10, t0: float = 0.0, fs: float = 600.0, g_axis: int = 1,
               limbs=LIMBS, noise: float = 0.0, rng=None):
    """A stationary sensor: gravity on one axis, no rotation."""
    a = np.zeros((n, 3))
    a[:, g_axis] = 9.81
    if noise and rng is not None:
        a += rng.normal(0.0, noise, (n, 3))
    return make_tick(a, np.zeros((n, 3)), limbs=limbs, t0=t0, fs=fs)


def run_ticks(frames_times, state, n_ticks: int, biomech):
    """Feed the same (frames, times) repeatedly, advancing the clock."""
    frames, times = frames_times
    n = len(next(iter(times.values())))
    fs = 600.0
    out = []
    for k in range(n_ticks):
        t = {limb: v + k * n / fs for limb, v in times.items()}
        out.append(biomech.compute({l: f.copy() for l, f in frames.items()}, state, t))
    return out
