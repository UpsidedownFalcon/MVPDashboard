"""S1-T10 tests: stub biomech produces stable expected values on known input."""

from __future__ import annotations

import numpy as np
import pytest

from ingest.biomech import EMA_ALPHA, GYRO_K, M_LIMBS, Metrics, compute


def _sinusoid_frames(amp_gyro: float = 2000.0, amp_accel: float = 3000.0,
                     n: int = 10) -> dict[str, np.ndarray]:
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    frames = {}
    for limb in M_LIMBS:
        f = np.zeros((n, 6), dtype=np.float32)
        f[:, 0] = amp_accel * np.sin(t)          # ax
        f[:, 3] = amp_gyro * np.sin(t)           # gx
        frames[limb] = f
    return frames


def test_known_sinusoid_converges_to_expected_values() -> None:
    state: dict = {}
    frames = _sinusoid_frames()
    for _ in range(200):  # let the EMA converge
        m = compute(frames, state)

    # gyro RMS over 3 axes with only gx driven: amp/sqrt(2)/sqrt(3); squash x/(x+k)
    gyro_rms = 2000.0 / np.sqrt(2) / np.sqrt(3)
    expected_m14 = gyro_rms / (gyro_rms + GYRO_K)
    # accel magnitude = |amp*sin| -> rms = amp/sqrt(2)
    accel_rms = 3000.0 / np.sqrt(2)
    expected_m5 = accel_rms / (accel_rms + 6000.0)

    for v in m.as_list()[:4]:
        assert v == pytest.approx(expected_m14, rel=1e-3)
    assert m.m5 == pytest.approx(expected_m5, rel=1e-3)
    expected_composite = 0.6 * expected_m14 + 0.4 * expected_m5
    assert m.composite == pytest.approx(expected_composite, rel=1e-3)
    assert 0.0 <= m.composite <= 1.0


def test_idle_input_reads_near_zero() -> None:
    state: dict = {}
    frames = {limb: np.zeros((10, 6), dtype=np.float32) for limb in M_LIMBS}
    m = compute(frames, state)
    assert m.composite == pytest.approx(0.0, abs=1e-9)


def test_empty_frames_hold_previous_values() -> None:
    state: dict = {}
    for _ in range(50):
        m_active = compute(_sinusoid_frames(), state)
    empty = {limb: np.empty((0, 6), dtype=np.float32) for limb in M_LIMBS}
    m_held = compute(empty, state)
    assert m_held == m_active


def test_first_tick_with_no_data_is_zero() -> None:
    state: dict = {}
    empty = {limb: np.empty((0, 6), dtype=np.float32) for limb in M_LIMBS}
    m = compute(empty, state)
    assert m == Metrics(0, 0, 0, 0, 0, 0)


def test_ema_smoothing_applies() -> None:
    state: dict = {}
    m1 = compute(_sinusoid_frames(), state)
    m2 = compute({limb: np.zeros((10, 6), dtype=np.float32) for limb in M_LIMBS}, state)
    # one zero tick shrinks the EMA by (1-alpha), not to zero
    assert m2.m1 == pytest.approx(m1.m1 * (1 - EMA_ALPHA), rel=1e-6)
    assert m2.m1 > 0.0


def test_missing_limb_treated_as_zero_energy() -> None:
    state: dict = {}
    frames = _sinusoid_frames()
    frames.pop("left_thigh")
    m = compute(frames, state)
    assert m.m1 == 0.0     # left_thigh missing
    assert m.m2 > 0.0
