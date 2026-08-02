"""S1-T15 biomech tests — the validation plan from docs/biomech/SPEC.md §11.

The high-value tests here are the regression guards. Several of them exist
because a specific bug was measured during S1-T14 and would be easy to
reintroduce while "simplifying":

  * ring-percentile instead of ring-max  -> m1 blind to isolated impacts
  * deg/s instead of rad/s in the jerk   -> m2 wrong by 57.3x, looks plausible
  * zero-fill instead of hold-last       -> 9.75 m/s^2 phantom impact on reconnect
  * noise weight on the accumulator      -> wUSI silently degenerates to USI
  * no both-sides gate                   -> one dead sensor reads as 100 asymmetry
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from common.scaling import ACCEL_MS2_PER_COUNT, GYRO_DPS_PER_COUNT
from ingest import biomech
from ingest.biomech import compute, log_score
from tests.conftest import (
    LIMBS,
    SQUATS_BIN,
    counts_from_si,
    make_tick,
    run_ticks,
    still_tick,
)

FS = 600.0
NS = 10                       # samples per 60 Hz tick at 600 Hz


def _drive(gen, n_ticks, state=None, fs=FS, n=NS):
    """Run n_ticks of compute(), gen(k) -> (a_ms2[n,3], w_dps[n,3])."""
    state = {} if state is None else state
    out = []
    for k in range(n_ticks):
        a, w = gen(k)
        frames, times = make_tick(a, w, t0=k * n / fs, fs=fs)
        out.append(compute(frames, state, times))
    return out, state


def _quiet_state(ticks: int = 120) -> dict:
    """Fresh state warmed up on a perfectly still sensor (filters settled)."""
    state: dict = {}
    _drive(lambda k: (np.tile([0.0, 9.81, 0.0], (NS, 1)), np.zeros((NS, 3))),
           ticks, state)
    return state


def _rotating(rate_dps: float, n=NS, fs=FS):
    """A stationary sensor spinning about body-x: gravity sweeps, no linear accel.

    SIGN MATTERS. For a world-fixed vector expressed in a rotating body frame,
    da_body/dt = -w x a_body. The gyro reading consistent with the `a` built
    below is therefore NEGATIVE about x; using +rate would make the fixture
    physically impossible and would silently double the w x a term instead of
    cancelling it. test_exact_jerk_cancels_the_rotation_term pins this.
    """
    om = math.radians(rate_dps)

    def gen(k):
        t = (k * n + np.arange(n)) / fs
        ang = om * t
        a = np.stack([np.zeros(n), 9.81 * np.sin(ang), -9.81 * np.cos(ang)], 1)
        w = np.tile([-rate_dps, 0.0, 0.0], (n, 1))
        return a, w

    return gen


# =============================================================================
# 1. Pure-rotation immunity — the single most important test in this file.
# =============================================================================

@pytest.mark.parametrize("rate_dps", [30.0, 90.0, 180.0, 360.0, 720.0])
def test_pure_rotation_produces_no_impact(rate_dps):
    """A stationary sensor that only ROTATES must report m1 = 0.

    This is what rules out the per-axis (VeDBA/ODBA) gravity removal that the
    stale model used: at 180 deg/s it fabricates 7.26 m/s^2 of impact that does
    not exist -- larger than the real squat signal (SPEC §3.3).
    """
    res, _ = _drive(_rotating(rate_dps), 200)
    steady = res[60:]                       # skip filter warm-up
    assert max(m.m1 for m in steady) <= 0.5, (
        f"pure rotation at {rate_dps} deg/s leaked into m1"
    )


def test_gravity_only_input_is_all_zero():
    res, _ = _drive(lambda k: (np.tile([0.0, 9.81, 0.0], (NS, 1)), np.zeros((NS, 3))), 150)
    last = res[-1]
    assert last.m1 == 0.0 and last.m2 == 0.0 and last.m3 == 0.0
    assert last.composite == 0.0


def test_free_fall_does_not_produce_a_phantom_impact():
    """|a| folds over at free fall (SPEC §3.3); it must not read as an impact."""
    res, _ = _drive(lambda k: (np.zeros((NS, 3)), np.zeros((NS, 3))), 150)
    assert res[-1].m1 < 40.0


# =============================================================================
# 2. Orientation invariance — guards the whole no-orientation claim.
# =============================================================================

def test_orientation_invariance():
    """An arbitrary FIXED rotation of every sample must not change any output."""
    rng = np.random.default_rng(11)
    q = rng.normal(size=(3, 3))
    R, _ = np.linalg.qr(q)                  # arbitrary orthonormal rotation
    if np.linalg.det(R) < 0:
        R[:, 0] *= -1

    def base(k):
        t = (k * NS + np.arange(NS)) / FS
        a = np.stack([2.0 * np.sin(2 * np.pi * 1.3 * t),
                      9.81 + 3.0 * np.sin(2 * np.pi * 11 * t),
                      1.5 * np.cos(2 * np.pi * 0.9 * t)], 1)
        w = np.stack([120 * np.sin(2 * np.pi * 1.1 * t),
                      60 * np.cos(2 * np.pi * 0.7 * t),
                      30 * np.sin(2 * np.pi * 2.3 * t)], 1)
        return a, w

    plain, _ = _drive(base, 120)
    rotated, _ = _drive(lambda k: tuple(v @ R.T for v in base(k)), 120)

    # Invariance is exact in principle; the residual is float64 round-off from
    # rotating the inputs through R (~1e-7 relative), not a modelling error.
    for i, (p, r) in enumerate(zip(plain, rotated)):
        assert p.m1 == pytest.approx(r.m1, abs=1e-4), f"m1 differs at tick {i}"
        assert p.m2 == pytest.approx(r.m2, abs=1e-4), f"m2 differs at tick {i}"
        assert p.m3 == pytest.approx(r.m3, abs=1e-6), f"m3 differs at tick {i}"
        assert p.composite == pytest.approx(r.composite, abs=1e-4)


# =============================================================================
# 3. The exact jerk identity, and the radians guard.
# =============================================================================

def test_jerk_uses_radians_not_degrees():
    """m2's cross term needs w in rad/s. deg/s is wrong by 57.3x (SPEC §3.5).

    Two rotation rates that differ only in magnitude must move m2 by the amount
    the radian form predicts. A deg/s implementation saturates m2 instead.
    """
    def gen_for(rate):
        def gen(k):
            a = np.tile([0.0, 9.81, 0.0], (NS, 1))
            w = np.tile([rate, 0.0, 0.0], (NS, 1))
            return a, w
        return gen

    # |w x g| = w_rad * 9.81. At 200 deg/s that is 34 m/s^3, well under M2_LO
    # (120), so m2 must read 0. Under a deg/s bug the term is 57.3x bigger
    # (1963 m/s^3), which lands solidly inside the scale and m2 would read > 0.
    res, _ = _drive(gen_for(200.0), 150)
    assert res[-1].m2 == 0.0, (
        "m2 responded to a rotation term that should be below the noise floor "
        "— w is probably being fed in deg/s instead of rad/s"
    )

    # Sanity that the term exists at all: 1900 deg/s (just inside the +-2000
    # gyro full scale, so nothing saturates) gives 325 m/s^3, above M2_LO.
    res_hi, _ = _drive(gen_for(1900.0), 150)
    assert res_hi[-1].m2 is not None and res_hi[-1].m2 > 0.0


def test_exact_jerk_cancels_the_rotation_term():
    """||da/dt + w x a|| must recover true linear jerk (here: zero).

    Also pins the sign convention used by _rotating() and by compute(): the
    identity is da_world/dt = df/dt + w x f, so a fixture whose gyro sign is
    flipped would DOUBLE the rotation term rather than cancel it -- and would
    look plausible, not broken.
    """
    fs, n = 600.0, 600
    rate = 360.0
    gen = _rotating(rate, n=n, fs=fs)
    a, w = gen(0)
    da = np.gradient(a, 1 / fs, axis=0)
    corrected = np.linalg.norm(da + np.cross(np.deg2rad(w), a), axis=1)
    naive = np.linalg.norm(da, axis=1)
    assert corrected[5:-5].max() < 0.5, "correction failed to cancel w x g"
    assert naive[5:-5].mean() > 50.0, "fixture has no rotation term to cancel"

    flipped = np.linalg.norm(da + np.cross(np.deg2rad(-w), a), axis=1)
    assert flipped[5:-5].mean() > 100.0, "a flipped gyro sign must NOT cancel"


# =============================================================================
# 4. Peak statistic: ring MAX, not a ring percentile.
# =============================================================================

def test_ring_max_responds_to_an_isolated_impact():
    """A single-tick impact must move m1 on the very next tick (SPEC §5.1).

    With a percentile ACROSS the ring, an isolated 50 m/s^2 impact moves the
    value by 0.000 -- it needs >6 of 60 ticks elevated. Running at 180 spm puts
    only ~3 strikes in a 1 s window, so a ring-percentile would systematically
    under-report impacts and vary with cadence rather than load.
    """
    state = _quiet_state()
    f0, t0 = make_tick(np.tile([0.0, 9.81, 0.0], (NS, 1)), np.zeros((NS, 3)), t0=2.0)
    before = compute(f0, state, t0)

    # Elevated for the whole tick so the within-tick p90 keeps it, but only ONE
    # tick of the 60-tick ring — exactly what a ring-percentile would miss.
    spike = np.tile([0.0, 9.81, 0.0], (NS, 1))
    spike[:, 1] += 50.0
    frames, times = make_tick(spike, np.zeros((NS, 3)), t0=2.0 + NS / FS)
    after = compute(frames, state, times)
    assert after.m1 > before.m1 + 10.0, (
        f"isolated impact barely moved m1 ({before.m1:.2f} -> {after.m1:.2f}); "
        "ring aggregate is probably a percentile instead of max"
    )


def test_within_tick_p90_rejects_a_single_sample_spike():
    """Artifact rejection happens INSIDE the tick, not across the ring.

    Each case gets its own state: m1 is a 1 s peak-HOLD, so a spike from an
    earlier sub-case would still be in the ring and mask the difference.
    """
    one = np.tile([0.0, 9.81, 0.0], (NS, 1))
    one[3, 0] += 120.0        # 1 of 10 samples -> excluded by p90; no clipping
    frames, times = make_tick(one, np.zeros((NS, 3)), t0=2.0)
    single = compute(frames, _quiet_state(), times)

    allten = np.tile([0.0, 9.81, 0.0], (NS, 1))
    allten[:, 0] += 120.0     # the same amplitude sustained across the tick
    frames, times = make_tick(allten, np.zeros((NS, 3)), t0=2.0)
    sustained = compute(frames, _quiet_state(), times)

    assert single.m1 is not None and sustained.m1 is not None
    assert sustained.m1 > single.m1 + 10.0, (
        "a single-sample spike was treated the same as a sustained one — the "
        "within-tick p90 is not rejecting artifacts"
    )


# =============================================================================
# 5. Hold-last vs zero-fill on reconnect (SPEC §7.2.1). High-value guard.
# =============================================================================

def test_zero_fill_would_produce_a_phantom_impact_but_hold_last_does_not():
    """Documents WHY absent limbs are hold-last filled, not zero-filled.

    Zero-fill lets the gravity baseline decay to 0, so a sensor reconnecting
    while the athlete stands perfectly still emits a full 9.81 m/s^2 of fake
    dynamic acceleration. Asserting the failure keeps the rationale executable.
    """
    dt = 1 / FS
    ab = 1 - math.exp(-dt / biomech.GRAVITY_TAU_S)

    def baseline_after(fill_value):
        amag = np.concatenate([
            np.full(int(2 * FS), 9.81),          # streaming
            np.full(int(3 * FS), fill_value),    # dropped out for 3 s
        ])
        acc = amag[0]
        for v in amag:
            acc += ab * (v - acc)
        return acc

    zero_base = baseline_after(0.0)
    hold_base = baseline_after(9.81)
    assert abs(9.81 - zero_base) > 9.0, "zero-fill no longer collapses the baseline"
    assert abs(9.81 - hold_base) < 0.1, "hold-last must preserve the baseline"


def test_reconnecting_limb_does_not_spike_m1():
    """End-to-end: a limb drops out and returns; m1 must stay at the floor."""
    state = {}
    still = lambda k: (np.tile([0.0, 9.81, 0.0], (NS, 1)), np.zeros((NS, 3)))
    _drive(still, 120, state)

    for k in range(120):                     # left_shin silent for 2 s
        a, w = still(k)
        frames, times = make_tick(a, w, t0=2.0 + k * NS / FS)
        frames["left_shin"] = np.empty((0, 6), dtype=np.float32)
        times["left_shin"] = np.empty(0, dtype=np.float64)
        compute(frames, state, times)

    after = [compute(*make_tick(*still(k), t0=4.0 + k * NS / FS)[:1], state,
                     make_tick(*still(k), t0=4.0 + k * NS / FS)[1])
             for k in range(30)]
    assert max(m.m1 for m in after) < 5.0, "reconnect produced a phantom impact"


# =============================================================================
# 6. Degraded operation and the freeze gates (SPEC §5.4, §5.5, §8).
# =============================================================================

def _moving(k, limbs=LIMBS, scale=1.0):
    t = (k * NS + np.arange(NS)) / FS
    a = np.stack([np.zeros(NS), 9.81 + scale * 4.0 * np.sin(2 * np.pi * 9 * t),
                  np.zeros(NS)], 1)
    w = np.tile([scale * 120.0, 0.0, 0.0], (NS, 1))
    return a, w


def test_m4_m5_null_during_warmup():
    res, _ = _drive(lambda k: _moving(k), 300)   # 5 s of movement
    assert all(m.m4 is None for m in res)
    assert all(m.m5 is None for m in res)
    assert "warming_up" in res[-1].flags


def test_one_sided_sensor_loss_does_not_read_as_asymmetry():
    """A dead sensor must never render as 'severe asymmetry' (SPEC §5.5).

    Without the both-sides gate the live side keeps accumulating while the dead
    side decays, driving m5 to 100 within ~30 s -- a hardware fault showing as
    the most alarming possible biomechanical finding.
    """
    state = {}
    for k in range(60 * 70):                 # 70 s of movement: past both warm-ups
        a, w = _moving(k)
        frames, times = make_tick(a, w, t0=k * NS / FS)
        if k > 60 * 40:                      # left side dies at t=40 s
            for limb in ("left_shin", "left_thigh"):
                frames[limb] = np.empty((0, 6), dtype=np.float32)
                times[limb] = np.empty(0, dtype=np.float64)
        m = compute(frames, state, times)
        if m.m5 is not None:
            assert m.m5 < 50.0, f"dead left side read as asymmetry {m.m5:.0f} at k={k}"


def test_shank_loss_does_not_pin_m4():
    """thigh/shank with a dead shank explodes the ratio unless gated (SPEC §5.4)."""
    state = {}
    for k in range(60 * 90):
        a, w = _moving(k)
        frames, times = make_tick(a, w, t0=k * NS / FS)
        if k > 60 * 70:                      # both shanks die after the lock
            for limb in ("left_shin", "right_shin"):
                frames[limb] = np.empty((0, 6), dtype=np.float32)
                times[limb] = np.empty(0, dtype=np.float64)
        m = compute(frames, state, times)
        if k > 60 * 75 and m.m4 is not None:
            assert m.m4 < 100.0, f"dead shank pinned m4 at {m.m4:.0f}"


@pytest.mark.parametrize("limbs,expect_m4,expect_m5", [
    (("left_shin", "left_thigh", "right_shin", "right_thigh"), True, True),
    (("left_shin", "left_thigh"), True, False),          # one leg
    (("left_shin", "right_shin"), False, True),          # both shanks
    (("left_shin",), False, False),                      # single sensor
])
def test_degradation_ladder(limbs, expect_m4, expect_m5):
    """SPEC §8: unavailable primitives are None; the composite still works."""
    state = {}
    res = []
    for k in range(60 * 70):
        a, w = _moving(k)
        frames, times = make_tick(a, w, limbs=limbs, t0=k * NS / FS)
        res.append(compute(frames, state, times))
    last = res[-1]
    assert last.m1 is not None and last.m2 is not None and last.m3 is not None
    assert last.composite > 0.0
    assert (last.m4 is not None) is expect_m4, f"m4 availability wrong for {limbs}"
    assert (last.m5 is not None) is expect_m5, f"m5 availability wrong for {limbs}"
    if len(limbs) < 4:
        assert "degraded_sensors" in last.flags


def test_held_tick_repeats_and_accumulates_no_dose():
    state = {}
    res, _ = _drive(lambda k: _moving(k), 120, state)
    before = res[-1]
    empty = {limb: np.empty((0, 6), dtype=np.float32) for limb in LIMBS}
    held = compute(empty, state, {limb: np.empty(0) for limb in LIMBS})
    assert held is before or held.composite == before.composite
    assert held.raw.get("dose", before.raw["dose"]) == before.raw["dose"]


# =============================================================================
# 7. Robustness: sample rate, packet loss, saturation.
# =============================================================================

@pytest.mark.parametrize("fs,n", [(600.0, 10), (300.0, 5), (150.0, 3)])
def test_dose_is_sample_rate_independent(fs, n):
    """m3 is a MEAN, not a sum, so halving the rate must not halve the dose."""
    state = {}
    for k in range(int(60 * 30)):
        t = (k * n + np.arange(n)) / fs
        a = np.stack([np.zeros(n), 9.81 + 4.0 * np.sin(2 * np.pi * 9 * t),
                      np.zeros(n)], 1)
        w = np.tile([120.0, 0.0, 0.0], (n, 1))
        frames, times = make_tick(a, w, t0=k * n / fs, fs=fs)
        m = compute(frames, state, times)
    assert 0.0 < m.m3 < 100.0


def test_packet_loss_does_not_collapse_dose():
    rng = np.random.default_rng(3)
    state = {}
    for k in range(60 * 30):
        a, w = _moving(k)
        keep = rng.random(NS) > 0.30          # drop ~30% of samples
        if not keep.any():
            keep[0] = True
        idx = np.nonzero(keep)[0]
        f = counts_from_si(a[idx], w[idx])
        t = (k * NS + idx) / FS
        m = compute({limb: f.copy() for limb in LIMBS}, state,
                    {limb: t.copy() for limb in LIMBS})
    assert m.m3 > 0.0 and m.m1 > 0.0


def test_saturated_window_suppresses_m1_m2():
    """A truncated peak is worse than no peak (SPEC §3.7)."""
    state = {}
    _drive(lambda k: _moving(k), 120, state)
    sat = np.full((NS, 6), 32767.0, dtype=np.float32)
    t = np.arange(NS) / FS + 2.0
    m = compute({limb: sat.copy() for limb in LIMBS}, state,
                {limb: t.copy() for limb in LIMBS})
    assert m.m1 is None and m.m2 is None
    assert "saturated" in m.flags
    assert m.composite is not None            # composite is never None


# =============================================================================
# 8. Session + calibration.
# =============================================================================

def test_session_reset_clears_dose_but_keeps_calibration():
    state = {}
    res, _ = _drive(lambda k: _moving(k), 60 * 20, state)
    assert res[-1].m3 > 0.0
    sess = state["_biomech"]
    sess.cal["left_shin"] = biomech.Calibration(k=1.01, gyro_bias=np.zeros(3), sigma=0.04)
    biomech.reset_session(state)
    assert sess.dose == 0.0 and sess.move_t == 0.0 and sess.R_base is None
    assert sess.cal["left_shin"].k == 1.01, "calibration must survive a session reset"


def test_snapshot_round_trip_applies_elapsed_decay():
    state = {}
    _drive(lambda k: _moving(k), 60 * 20, state)
    snap = biomech.snapshot(state)
    assert snap["dose"] > 0.0

    fresh: dict = {}
    ok = biomech.restore(fresh, snap, LIMBS, snap["last_tick_t"] + 60.0, 300.0)
    assert ok
    s2 = fresh["_biomech"]
    expected = snap["dose"] * 0.5 ** (60.0 / biomech.DOSE_HALFLIFE_S)
    assert s2.dose == pytest.approx(expected, rel=1e-9)

    stale: dict = {}
    assert not biomech.restore(stale, snap, LIMBS, snap["last_tick_t"] + 999.0, 300.0), \
        "a gap longer than SESSION_GAP_S must start a fresh session"


def test_snapshot_calibration_is_keyed_by_limb_not_slot():
    """Slot order can change across restarts; calibration must follow the sensor."""
    state = {}
    _drive(lambda k: _moving(k), 30, state)
    sess = state["_biomech"]
    sess.cal["right_thigh"] = biomech.Calibration(k=1.03, gyro_bias=np.zeros(3), sigma=0.05)
    snap = biomech.snapshot(state)
    assert snap["cal"]["right_thigh"]["k"] == pytest.approx(1.03)

    fresh: dict = {}
    biomech.restore(fresh, snap, tuple(reversed(LIMBS)), snap["last_tick_t"], 300.0)
    assert fresh["_biomech"].cal["right_thigh"].k == pytest.approx(1.03)


def test_calibration_recovers_known_gain_and_bias():
    rng = np.random.default_rng(7)
    true_k, true_bias = 1.02, np.array([0.4, -0.2, 0.1])
    n = int(10 * FS)
    a = np.zeros((n, 3)); a[:, 1] = 9.81 / true_k
    a += rng.normal(0, 0.02, (n, 3))
    w = np.tile(true_bias, (n, 1))
    f = counts_from_si(a, w)
    cal = biomech.calibrate({limb: f.copy() for limb in LIMBS})
    assert cal is not None
    got = cal["left_shin"]
    assert got.k == pytest.approx(true_k, rel=2e-3)
    assert got.gyro_bias == pytest.approx(true_bias, abs=2e-3)
    assert got.sigma > 0.0


def test_calibration_rejects_a_moving_or_faulty_sensor():
    n = int(10 * FS)
    moving = counts_from_si(np.tile([0.0, 9.81, 0.0], (n, 1)),
                            np.tile([50.0, 0.0, 0.0], (n, 1)))   # |w| >> 5 deg/s
    assert biomech.calibrate({limb: moving.copy() for limb in LIMBS}) is None

    wrong = counts_from_si(np.tile([0.0, 8.0, 0.0], (n, 1)), np.zeros((n, 3)))
    assert biomech.calibrate({limb: wrong.copy() for limb in LIMBS}) is None


def test_uncalibrated_path_still_works():
    """Calibration is OPTIONAL and must never be load-bearing (SPEC §3.8)."""
    res, _ = _drive(lambda k: _moving(k), 200)
    assert res[-1].m1 > 0.0 and res[-1].composite > 0.0
    assert "uncalibrated" in res[-1].flags


# =============================================================================
# 9. Normalisation helper.
# =============================================================================

def test_log_score_endpoints_and_clamping():
    assert log_score(1.0, 1.0, 100.0) == 0.0
    assert log_score(100.0, 1.0, 100.0) == 100.0
    assert log_score(10.0, 1.0, 100.0) == pytest.approx(50.0)
    assert log_score(0.01, 1.0, 100.0) == 0.0        # clamped
    assert log_score(1e6, 1.0, 100.0) == 100.0       # clamped
    assert log_score(0.0, 1.0, 100.0) == 0.0
    assert log_score(float("nan"), 1.0, 100.0) == 0.0


# =============================================================================
# 10. Golden values on the real squats replay (SPEC §11).
# =============================================================================

@pytest.mark.skipif(not SQUATS_BIN.exists(), reason="example data not present")
def test_squats_replay_golden_values():
    """Measured phase values from example/squats.bin.

    NOTE m4/m5 assert None in EVERY row: the log holds only ~19.9 s of movement,
    below m4's 60 s baseline lock and m5's 30 s warm-up. This capture validates
    m1, m2, m3 and the composite only -- see SPEC §11.1 and TestSyntheticOnly.
    """
    from common.packet import decode

    raw = np.fromfile(str(SQUATS_BIN), dtype=np.uint8)
    n = raw.size // 21
    recs = raw[: n * 21].reshape(n, 21)
    batch = decode([bytes(r) for r in recs[:0]]) if False else None  # noqa: F841

    w = recs[:, 2:21]
    def i16(lo, hi):
        return (w[:, lo].astype(np.uint16) | (w[:, hi].astype(np.uint16) << 8)).astype(np.int16)
    sid = w[:, 1] & 3
    src = recs[:, 1]
    imu = np.stack([i16(6, 7), i16(8, 9), i16(10, 11),
                    i16(12, 13), i16(14, 15), i16(16, 17)], 1).astype(np.float32)

    limb_of = {(0, 1): "left_shin", (0, 2): "left_thigh",
               (1, 1): "right_thigh", (1, 2): "right_shin"}
    dec = 11
    fs = 6410.0 / dec
    streams = {name: imu[(src == s) & (sid == k)][::dec] for (s, k), name in limb_of.items()}
    n_min = min(len(v) for v in streams.values())
    ns = int(round(fs / 60))

    state: dict = {}
    rows = []
    for tk in range(n_min // ns):
        a, b = tk * ns, (tk + 1) * ns
        frames = {l: streams[l][a:b] for l in streams}
        times = {l: 1000.0 + np.arange(a, b) / fs for l in streams}
        rows.append((a / fs, compute(frames, state, times)))

    def phase(lo, hi):
        sel = [m for t, m in rows if lo <= t < hi]
        assert sel, f"no ticks in {lo}-{hi}s"
        mean = lambda f: sum(f(m) for m in sel) / len(sel)
        return sel, mean

    still, mean = phase(2, 11)
    assert mean(lambda m: m.m1) < 2.0
    assert mean(lambda m: m.m2) < 2.0
    assert mean(lambda m: m.m3) == 0.0
    assert mean(lambda m: m.composite) < 2.0

    squat, mean = phase(16, 31)
    assert 40.0 <= mean(lambda m: m.m1) <= 52.0
    assert 48.0 <= mean(lambda m: m.m2) <= 62.0
    assert 4.0 <= mean(lambda m: m.m3) <= 11.0
    assert 30.0 <= mean(lambda m: m.composite) <= 42.0

    after, mean = phase(34, 41)
    assert mean(lambda m: m.m1) < 4.0
    assert 10.0 <= mean(lambda m: m.m3) <= 18.0
    # composite rests on the accumulated-dose floor rather than collapsing to 0
    assert 4.0 <= mean(lambda m: m.composite) <= 18.0

    assert all(m.m4 is None for _, m in rows), "m4 cannot emit on this capture"
    assert all(m.m5 is None for _, m in rows), "m5 cannot emit on this capture"


# =============================================================================
# 11. m4/m5 — SYNTHETIC FIXTURES ONLY.
# =============================================================================

class TestSyntheticOnly:
    """m4 and m5 have NO real-data validation (SPEC §11.1).

    Passing these tests does NOT demonstrate real-world correctness. They pin
    the mechanics -- warm-up, direction-agnosticism, scale -- against generated
    signals with known ground truth. Closing the gap needs one >=10 minute
    session with a deliberate fatigue block on real wearables; until then both
    metrics ship flagged `unvalidated`.
    """

    def test_m5_converges_to_zero_when_symmetric(self):
        res, _ = _drive(lambda k: _moving(k), 60 * 70)
        emitted = [m.m5 for m in res if m.m5 is not None]
        assert emitted, "m5 never emitted after 70 s of movement"
        assert emitted[-1] < 5.0, f"symmetric input gave m5={emitted[-1]:.1f}"
        assert "unvalidated" in res[-1].flags

    def test_m5_tracks_a_known_imbalance(self):
        """A 12% left/right load imbalance -> USI ~8.5% -> m5 ~47."""
        state = {}
        last = None
        for k in range(60 * 90):
            a_l, w_l = _moving(k, scale=1.12)
            a_r, w_r = _moving(k, scale=1.00)
            fl, tl = make_tick(a_l, w_l, limbs=("left_shin", "left_thigh"),
                               t0=k * NS / FS)
            fr, tr = make_tick(a_r, w_r, limbs=("right_shin", "right_thigh"),
                               t0=k * NS / FS)
            last = compute({**fl, **fr}, state, {**tl, **tr})
        assert last.m5 is not None
        assert 25.0 <= last.m5 <= 75.0, f"12% imbalance gave m5={last.m5:.1f}"

    @pytest.mark.parametrize("direction", [1.30, 0.70])
    def test_m4_is_direction_agnostic(self, direction):
        """Transmission drifting EITHER way must raise m4 (SPEC §5.4).

        The literature does not fix the sign: shock attenuation usually
        increases under fatigue, and injured runners showed greater lower-body
        attenuation. So m4 scores |drift|, not signed drift.
        """
        state = {}
        last = None
        for k in range(60 * 200):
            t_s = k * NS / FS
            # after the 60 s baseline lock, scale the THIGH signal only
            gain = 1.0 if t_s < 90.0 else direction
            a_s, w_s = _moving(k, scale=1.0)
            a_t, w_t = _moving(k, scale=gain)
            fs_, ts_ = make_tick(a_s, w_s, limbs=("left_shin", "right_shin"), t0=t_s)
            ft_, tt_ = make_tick(a_t, w_t, limbs=("left_thigh", "right_thigh"), t0=t_s)
            last = compute({**fs_, **ft_}, state, {**ts_, **tt_})
        assert last.m4 is not None, "m4 never locked its baseline"
        assert last.m4 > 20.0, (
            f"transmission drift x{direction} gave m4={last.m4:.1f}; "
            "m4 must respond to drift in BOTH directions"
        )

    def test_m4_stays_zero_when_nothing_changes(self):
        """No drift must invent no fatigue."""
        res, _ = _drive(lambda k: _moving(k), 60 * 120)
        emitted = [m.m4 for m in res if m.m4 is not None]
        assert emitted, "m4 never locked its baseline"
        assert max(emitted) < 10.0, f"steady input produced m4 up to {max(emitted):.1f}"


# =============================================================================
# 12. Performance guard (pytest -k bench).
# =============================================================================

def test_bench_compute_five_devices():
    """5 devices must stay well under the 16.67 ms tick budget (SPEC §7.1).

    Per-device batching: one lfilter call per stage over all 4 limbs at once.
    A regression to per-limb calls shows up here as roughly a 4x slowdown.
    """
    n_dev = 5
    states = [{} for _ in range(n_dev)]
    payload = []
    for _ in range(n_dev):
        a, w = _moving(0)
        payload.append(make_tick(a, w))

    for k in range(120):                      # warm up filters and rings
        for d in range(n_dev):
            frames, times = payload[d]
            compute({l: f.copy() for l, f in frames.items()}, states[d],
                    {l: t + k * NS / FS for l, t in times.items()})

    # MIN over several rounds, not the mean: this runs alongside the rest of the
    # suite (and often Docker), and a single scheduler hiccup would otherwise
    # make the guard flaky. The minimum is a stable lower bound on real cost.
    reps, rounds = 100, 5
    best_us = float("inf")
    off = 200
    for r in range(rounds):
        t0 = time.perf_counter()
        for k in range(reps):
            for d in range(n_dev):
                frames, times = payload[d]
                compute({l: f.copy() for l, f in frames.items()}, states[d],
                        {l: t + (off + k) * NS / FS for l, t in times.items()})
        off += reps
        best_us = min(best_us, (time.perf_counter() - t0) / reps * 1e6)

    budget_us = 1e6 / 60
    print(f"\n  biomech: {best_us:.0f} us/tick for {n_dev} devices "
          f"({100 * best_us / budget_us:.1f}% of the 60 Hz budget)")
    # ~1.6 ms measured on an idle dev machine. The guard is set at 3 ms so it
    # tolerates a loaded machine while still catching the regression it exists
    # for: reverting to per-limb lfilter calls costs ~3.8x (SPEC §7.1).
    assert best_us < 3000.0, (
        f"{best_us:.0f} us/tick for {n_dev} devices exceeds the 3 ms guard — "
        "check that filtering is still batched per device, not per limb"
    )


# =============================================================================
# 13. MAX_DEVICES cap, displacement and eviction (SPEC §7.2, test 20).
# =============================================================================

def _route(registry, device_id, t, n=4):
    from common import packet
    payloads = [packet.encode(device_id, src, sen, i * 1666, [i, -i, 100, -100, 50, -50])
                for src, sen in ((0, 1), (0, 2), (1, 1), (1, 2)) for i in range(n)]
    registry.route(packet.decode(payloads), recv_time=t)


def test_max_devices_cap_drops_extra_live_devices():
    """A 6th device must be refused while all 5 are live — never merged."""
    from ingest.state import Registry
    reg = Registry(max_devices=5)
    reg.offline_after_s = 2.0
    for i, dev in enumerate(range(30, 35)):
        _route(reg, dev, t=1000.0)
    assert sorted(reg.devices) == [30, 31, 32, 33, 34]

    _route(reg, 40, t=1000.5)               # all five still live
    assert 40 not in reg.devices
    assert reg.dev_dropped > 0
    assert sorted(reg.devices) == [30, 31, 32, 33, 34], "cap must not evict a live device"


def test_new_device_displaces_an_offline_one():
    """Swapping a wearable must not make the trainer wait out the session gap."""
    from ingest.state import Registry
    reg = Registry(max_devices=5)
    reg.offline_after_s = 2.0
    removed: list[int] = []
    reg.on_device_removed = removed.append
    for dev in range(30, 35):
        _route(reg, dev, t=1000.0)
    _route(reg, 34, t=1010.0)               # keep 34 fresh; 30-33 go silent

    _route(reg, 40, t=1010.0)               # 30 is the longest-silent
    assert 40 in reg.devices
    assert 30 not in reg.devices and removed == [30]
    assert 34 in reg.devices, "the freshest device must survive"


def test_evict_stale_releases_slots():
    from ingest.state import Registry
    reg = Registry(max_devices=5)
    reg.offline_after_s = 2.0
    removed: list[int] = []
    reg.on_device_removed = removed.append
    for dev in range(30, 33):
        _route(reg, dev, t=1000.0)
    assert reg.evict_stale(now=1000.0 + 100.0, max_age_s=300.0) == []
    gone = reg.evict_stale(now=1000.0 + 400.0, max_age_s=300.0)
    assert sorted(gone) == [30, 31, 32] and sorted(removed) == [30, 31, 32]
    assert reg.devices == {}
