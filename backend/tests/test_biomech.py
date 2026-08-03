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

from common.scaling import GYRO_DPS_PER_COUNT
from ingest import biomech
from ingest.biomech import compute, log_score
from tests.conftest import LIMBS, SQUATS_BIN, counts_from_si, make_tick

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
    # |w x a| scales with BOTH the rotation rate and |a|. Gravity alone cannot
    # clear M2_LO anywhere inside the +-2000 deg/s gyro range, so the fixture
    # holds a large constant |a| instead. Constant magnitude means da/dt is zero
    # and the baseline converges, so m1 stays 0 and the rotation term is
    # isolated -- which is the point of the test.
    #
    # Raised 30 -> 100 m/s^2 on 2026-08-03 when M2_LO went 800 -> 2500: at 30
    # the high arm produced only 995 m/s^3, which now sits BELOW the floor, so
    # the sanity half of the test would have passed vacuously.
    A_MAG = 100.0

    def gen_for(rate):
        def gen(k):
            a = np.tile([0.0, A_MAG, 0.0], (NS, 1))
            w = np.tile([rate, 0.0, 0.0], (NS, 1))
            return a, w
        return gen

    # |w x a| = w_rad * 100. At 200 deg/s that is 349 m/s^3, well under M2_LO
    # (2500), so m2 must read 0. Under a deg/s bug the term is 57.3x bigger
    # (20,000 m/s^3), which lands solidly inside the scale and m2 would read > 0.
    res, _ = _drive(gen_for(200.0), 150)
    assert res[-1].m2 == 0.0, (
        "m2 responded to a rotation term that should be below the noise floor "
        "— w is probably being fed in deg/s instead of rad/s"
    )

    # Sanity that the term exists at all: 1900 deg/s (just inside the +-2000
    # gyro full scale, so nothing saturates) gives 3316 m/s^3, above M2_LO.
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
    """A moving limb at roughly hard-running load.

    Amplitudes raised 4.0 -> 8.5 m/s^2 and 120 -> 300 deg/s on 2026-08-03 with
    the m1/m2 re-anchoring: `M1_LO_FLOOR` went 2.0 -> 8.0 m/s^2 (a walk used to
    read 40/100), so the old 4.0 sat BELOW the floor and every metric derived
    from it read 0 -- which would make the assertions vacuous rather than
    failing. The amplitude is what changed, not any claim.

    8.5 and not more: the dynamic term must stay UNDER gravity or 9.81 + A*sin
    goes negative and |a| FOLDS (SPEC §3.3), which makes a_dyn a non-linear
    function of the amplitude and silently compresses any left/right ratio built
    by scaling it. The gyro is likewise held at 300 deg/s rather than scaled to
    match: callers pass `scale` up to 6, and 480*6 = 2880 deg/s exceeds the
    +-2000 deg/s full scale, tripping saturation suppression to None.
    """
    t = (k * NS + np.arange(NS)) / FS
    a = np.stack([np.zeros(NS), 9.81 + scale * 8.5 * np.sin(2 * np.pi * 9 * t),
                  np.zeros(NS)], 1)
    w = np.tile([scale * 300.0, 0.0, 0.0], (NS, 1))
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
    for k in range(60 * 160):        # m4 needs 60 s settle + 60 s in-band
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


def test_noise_weight_never_subtracts_from_the_accumulators():
    """wUSI's W must clamp at 0, not go negative (SPEC §5.5).

    W = 1 - 2s^2/(s^2 + L^2 + R^2) tends to -1 as the per-tick loads approach
    the noise floor. A negative W does not merely stop accumulating: it eats
    load already measured, and on a sensor whose measured sigma is large it
    drives accL/accR negative outright. The floor means "this tick carries no
    trustworthy asymmetry information".
    """
    state: dict = {}
    # Movement just above MOVE_GATE, paired with a deliberately noisy sensor:
    # 2*sigma^2 then exceeds sigma^2 + L^2 + R^2 and the unclamped weight is
    # about -0.66 on every tick.
    def small(k):
        t = (k * NS + np.arange(NS)) / FS
        a = np.stack([np.zeros(NS), 9.81 + 0.25 * np.sin(2 * np.pi * 9 * t),
                      np.zeros(NS)], 1)
        return a, np.tile([20.0, 0.0, 0.0], (NS, 1))

    _drive(small, 5, state)                      # create the session
    noisy = biomech.Calibration(k=1.0, gyro_bias=np.zeros(3), sigma=0.5)
    biomech.set_calibration(state, {limb: noisy for limb in LIMBS})
    _drive(small, 60 * 60, state)                # 60 s

    sess = state["_biomech"]
    assert sess.accL >= 0.0 and sess.accR >= 0.0, (
        f"noise weight went negative and drained the accumulators "
        f"(accL={sess.accL:.4f}, accR={sess.accR:.4f})"
    )


def test_no_thigh_mapped_is_degraded_not_warming_up():
    """R_base can never lock without a thigh, so it is not a warm-up (SPEC §10).

    `warming_up` tells the UI a value is coming. With no thigh sensor in
    LIMB_MAP, m4 is permanently unavailable — a degraded sensor set. Flagging
    it `warming_up` leaves the UI waiting forever for m4.
    """
    limbs = ("left_shin", "right_shin")
    state: dict = {}
    last = None
    for k in range(60 * 70):                     # past both warm-ups
        a, w = _moving(k)
        frames, times = make_tick(a, w, limbs=limbs, t0=k * NS / FS)
        last = compute(frames, state, times)

    assert last.m4 is None, "m4 cannot exist without a thigh sensor"
    assert last.m5 is not None, "m5 should still emit from the two shanks"
    assert "degraded_sensors" in last.flags
    assert "warming_up" not in last.flags, (
        "a metric that can never emit is degraded, not warming up"
    )


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

# Amplitudes here are a HARD-RUN equivalent (16 m/s^2 / 480 deg/s), not the
# gentler `_moving` default. Since 2026-08-03 the dose power law acts on the
# physical load ratio against A_DOSE_REF/W_DOSE_REF and M3_LO is 0.5 dose-minutes
# (= 30 s of hard-training equivalent), so a light synthetic load accumulates a
# real but sub-floor dose and reads m3 = 0 -- which would make these assertions
# vacuous rather than failing. The amplitude is what changed, not the claim.
_HARD_A_MS2 = 16.0
_HARD_W_DPS = 480.0


@pytest.mark.parametrize("fs,n", [(600.0, 10), (300.0, 5), (150.0, 3)])
def test_dose_is_sample_rate_independent(fs, n):
    """m3 is a MEAN, not a sum, so halving the rate must not halve the dose."""
    state = {}
    for k in range(int(60 * 30)):
        t = (k * n + np.arange(n)) / fs
        a = np.stack([np.zeros(n), 9.81 + _HARD_A_MS2 * np.sin(2 * np.pi * 9 * t),
                      np.zeros(n)], 1)
        w = np.tile([_HARD_W_DPS, 0.0, 0.0], (n, 1))
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


def test_saturated_window_reports_a_marked_floor_value():
    """A clipped peak is a LOWER BOUND, reported and marked — not suppressed.

    Changed 2026-08-03 (user decision). The +-16 g part cannot be changed and it
    clips inside real athletic movement: measured through this pipeline, dynamic
    accel tops out near 147 m/s^2, so 35 g, 42 g, 60 g and 100 g landings all
    read m1 = 75.2. Suppressing to None removed Impact and Loading Rate on ~2%
    of ticks at 27 g PTA and ~7% at 42 g — i.e. exactly when load was highest.

    So m1/m2 keep reporting and `saturated` marks them as floors, which the UI
    renders as ">= x". The values stay monotonic; they stop being exact.
    """
    state = {}
    _drive(lambda k: _moving(k), 120, state)
    sat = np.full((NS, 6), 32767.0, dtype=np.float32)
    t = np.arange(NS) / FS + 2.0
    m = compute({limb: sat.copy() for limb in LIMBS}, state,
                {limb: t.copy() for limb in LIMBS})
    assert m.m1 is not None and m.m2 is not None, "clipped peaks are still reported"
    assert m.m1 > 0.0
    assert "saturated" in m.flags, "and are marked as lower bounds"
    assert m.composite is not None            # composite is never None


def test_saturated_flag_means_lower_bound_not_any_clipping():
    """`saturated` fires at SAT_SUPPRESS_FRACTION, not on a single clipped
    sample. One clipped sample in a tick does not make the peak untrustworthy,
    and firing on that made the flag near-permanent during hard work. The raw
    fraction stays in `raw.sat_frac` for the fine-grained view."""
    state = _quiet_state()
    a, w = _moving(0)
    f, tt = make_tick(a, w, t0=2.0)
    one = next(iter(f))
    f[one] = f[one].copy()
    f[one][0, 0] = 32767.0                    # exactly one clipped sample
    m = compute(f, state, tt)
    assert 0.0 < m.raw["sat_frac"] <= 1.0 / NS
    assert "saturated" not in m.flags
    assert m.m1 is not None


def test_rotation_discriminates_once_the_accelerometer_clips():
    """The user's proposal, and the measurement behind it.

    Above the clipping point m1/m2 cannot tell a fast benign movement from a
    fast violently rotating one — measured at a fixed 35 g landing while
    sweeping shank rate 100 -> 1900 deg/s, m1 stayed at 81.5 and m2 at 61.5 at
    EVERY rate. Rotation is the only channel left, so it claims the demand
    headroom m1 can no longer reach — and ONLY when saturated, because rotation
    alone must never read as risk (a kick through the air is nearly pure
    rotation and must stay low).
    """
    def sat_tick(state, gyro_dps):
        sat = np.full((NS, 6), 32767.0, dtype=np.float32)
        sat[:, 3:6] = gyro_dps / GYRO_DPS_PER_COUNT
        t = np.arange(NS) / FS + 2.0
        return compute({limb: sat.copy() for limb in LIMBS}, state,
                       {limb: t.copy() for limb in LIMBS})

    slow = sat_tick(_drive(lambda k: _moving(k), 120, {})[1], 50.0)
    fast = sat_tick(_drive(lambda k: _moving(k), 120, {})[1], 1900.0)

    assert slow.m1 == pytest.approx(fast.m1), "impact is blind here — that is the premise"
    assert fast.raw["demand"] > slow.raw["demand"], (
        "rotation must discriminate where impact cannot"
    )


def test_rotation_alone_does_not_inflate_demand():
    """The guard on the rule above: high rotation with UNsaturated impact must
    not escalate. A firm kick through the air is almost pure rotation with
    modest impact and the wearer expects it to read low."""
    state = _quiet_state()
    spin = _rotating(1900.0)
    res, _ = _drive(spin, 200, state)
    last = res[-1]
    assert "saturated" not in last.flags
    assert last.raw["demand"] == pytest.approx(0.0, abs=2.0), (
        f"pure rotation inflated demand to {last.raw['demand']:.1f}"
    )


# =============================================================================
# 8. Session + calibration.
# =============================================================================

def test_session_reset_clears_dose_but_keeps_calibration():
    state = {}
    # hard-run amplitude so the dose clears M3_LO -- see _HARD_A_MS2 above
    res, _ = _drive(lambda k: _moving(k), 60 * 20, state)
    assert res[-1].m3 > 0.0
    sess = state["_biomech"]
    sess.cal["left_shin"] = biomech.Calibration(k=1.01, gyro_bias=np.zeros(3), sigma=0.04)
    biomech.reset_session(state)
    assert sess.dose == 0.0 and sess.move_t == 0.0
    assert all(b is None for b in sess.r_base)
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
    raw = np.fromfile(str(SQUATS_BIN), dtype=np.uint8)
    n = raw.size // 21
    recs = raw[: n * 21].reshape(n, 21)

    # Decoded here rather than through common.packet.decode: this test replays
    # a whole SD-log file, and decode() is the per-datagram wire path (sync +
    # CRC + bounds). Vectorising the field extraction over the entire file is
    # what keeps the replay to a few seconds.
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

    # Re-measured 2026-08-03 after the dose law and the acute curve were
    # corrected (SPEC §5.3/§6.1). m1 and m2 are UNCHANGED at 13.5 / 17.7 --
    # neither change touches their normalisation, so they are the control
    # showing the retune moved only what it was meant to.
    #
    # m3 is now 0 across the whole capture, and that is the point rather than a
    # regression. The dose power law acts on the physical load ratio against a
    # hard-running reference, and M3_LO is 0.5 dose-minutes (= 30 s of hard
    # training equivalent). This file holds 16 s of GENTLE squatting, whose
    # measured cube-mean load is ~14% of hard running; cubed and integrated that
    # is 0.0011 dose-minutes, three orders below the floor. SPEC §5.3 already
    # argued this is the physically correct answer ("16 seconds of moderate
    # squatting genuinely is a negligible cumulative load"); the old scale said
    # 13.7/100 because its floor was 0.6 s of hard-training equivalent, so any
    # movement at all cleared it within a second.
    #
    # Consequence: this capture can no longer validate m3 or the dose-floor
    # identity. test_sustained_load_builds_dose_and_decays_to_the_floor below
    # takes that over on a synthetic load long enough to matter.
    still, mean = phase(2, 11)
    assert mean(lambda m: m.m1) < 2.0
    assert mean(lambda m: m.m2) < 2.0
    assert mean(lambda m: m.m3) == 0.0
    assert mean(lambda m: m.composite) < 2.0

    squat, mean = phase(16, 31)
    # Re-measured 2026-08-03 after M1_LO_FLOOR 2.0 -> 8.0 and M2_LO 800 -> 2500.
    # This capture is 16 s of GENTLE bodyweight squatting whose peak dynamic
    # acceleration is ~3.6 m/s^2 -- below the new impact floor, and genuinely
    # below WALKING (~11 m/s^2), which is why SPEC §6.4 already noted "a squat
    # has less heel strike than a step". So it now reads ~0 throughout.
    #
    # ⚠️ That means this capture no longer validates ANY metric numerically --
    # it validates only that a low-load activity reads low. The synthetic
    # fixtures and test_sustained_load_builds_dose_and_decays_to_the_floor carry
    # the numeric validation until a real capture with running/jumping exists
    # (docs/biomech/SPEC.md open item 13).
    assert mean(lambda m: m.m1) < 1.0                     # measured 0.04
    assert mean(lambda m: m.m2) < 5.0                     # measured 1.79
    assert mean(lambda m: m.m3) == 0.0                    # measured 0.0
    assert mean(lambda m: m.composite) < 1.0              # measured 0.000

    after, mean = phase(34, 41)
    assert mean(lambda m: m.m1) < 4.0
    assert mean(lambda m: m.m3) == 0.0                    # measured 0.0
    # composite at rest is the dose floor and nothing else: 0.50 * m3. Holds as
    # an identity at any dose, including this capture's zero.
    assert abs(mean(lambda m: m.composite) - 0.5 * mean(lambda m: m.m3)) < 0.1

    assert all(m.m4 is None for _, m in rows), "m4 cannot emit on this capture"
    assert all(m.m5 is None for _, m in rows), "m5 cannot emit on this capture"


def test_rest_reads_zero_however_much_dose_has_accumulated():
    """The composite must return to ZERO at rest, whatever the accumulated load.

    Replaces test_sustained_load_builds_dose_and_decays_to_the_floor. That test
    asserted `composite == 0.50 * m3` at rest, which was true while dose entered
    the composite as an additive FLOOR -- and which was exactly the fault: on a
    13-minute worn protocol the composite spanned only 15.5 to 18.5, because the
    floor held it up and standing still was indistinguishable from squatting to
    failure.

    Dose now reduces CAPACITY instead, so accumulated load makes the same
    movement read RISKIER without putting a floor under rest.
    """
    state: dict = {}
    res, _ = _drive(lambda k: _moving(k, scale=4.0), 60 * 300, state)   # 5 min
    worked = res[-1]
    assert worked.m3 > 20.0, f"5 min of hard load should build dose, got {worked.m3:.1f}"
    # The synthetic fixture is a constant-amplitude sinusoid, so its jerk sits
    # below M2_LO and m2 reads 0 -- demand is therefore modest here. The claim
    # that matters is the CONTRAST with rest below, not the absolute level.
    assert worked.composite > 1.0, "should read as non-zero risk while working"

    n_work = 60 * 300
    a_still = np.stack([np.zeros(NS), np.full(NS, 9.81), np.zeros(NS)], 1)
    w_still = np.zeros((NS, 3))
    for k in range(n_work, n_work + 60 * 20):                           # 20 s rest
        frames, times = make_tick(a_still, w_still, t0=k * NS / FS)
        rested = compute(frames, state, times)

    assert rested.m3 > 10.0, "dose must persist through a short rest, not reset"
    assert rested.composite < 1.0, (
        f"standing still must read ~0 whatever the dose, got {rested.composite:.1f} "
        f"at m3={rested.m3:.1f}"
    )
    # ...and the accumulated dose must show up as REDUCED CAPACITY, i.e. the
    # same movement is riskier than it was when fresh.
    assert rested.raw["degradation"] > 0.0
    assert worked.composite > 5.0 * rested.composite, (
        f"working {worked.composite:.2f} must stand clear of rest "
        f"{rested.composite:.2f} -- that contrast is what the old floor destroyed"
    )

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
    # 1,590 us measured on an idle dev machine (SPEC §7.1). The guard is set at
    # 3 ms -- the same figure SPEC §11 test 15 states -- so it tolerates a
    # loaded machine while still catching the regression it exists for:
    # reverting to per-limb lfilter calls costs ~3.8x and lands at ~6 ms.
    assert best_us < 3000.0, (
        f"{best_us:.0f} us/tick for {n_dev} devices exceeds the 3 ms guard — "
        "check that filtering is still batched per device, not per limb"
    )


# =============================================================================
# 13. SPEC §11 tests 16, 17, 19, 25.
# =============================================================================

def test_trailing_windows_delay_release_not_onset():
    """SPEC §11 test 16 — a step impact moves m1 on the NEXT tick (§7.1).

    m1 is "peak over the last 1 s", which is a peak-HOLD: the window governs
    how long a peak persists, not how long it takes to appear. If this ever
    starts needing several ticks, the 22.6 ms latency budget is wrong and the
    onset claim in §7.1 no longer holds.
    """
    state = _quiet_state()
    frames, times = make_tick(np.tile([0.0, 9.81, 0.0], (NS, 1)),
                              np.zeros((NS, 3)), t0=2.0)
    quiet = compute(frames, state, times)

    step = np.tile([0.0, 9.81, 0.0], (NS, 1))
    step[:, 1] += 30.0
    frames, times = make_tick(step, np.zeros((NS, 3)), t0=2.0 + NS / FS)
    onset = compute(frames, state, times)

    assert onset.m1 > quiet.m1 + 20.0, (
        f"impact took more than one tick to reach m1 ({quiet.m1:.1f} -> "
        f"{onset.m1:.1f}); trailing windows must delay release, not onset"
    )

    # And the release side of the same claim: the peak is held for the 1 s
    # ring, then released. Without the hold a 5 ms impact would be invisible
    # on a 60 Hz chart.
    tail = []
    for i in range(biomech.PEAK_WINDOW_TICKS):
        frames, times = make_tick(np.tile([0.0, 9.81, 0.0], (NS, 1)),
                                  np.zeros((NS, 3)), t0=2.0 + (2 + i) * NS / FS)
        tail.append(compute(frames, state, times))
    assert tail[0].m1 == pytest.approx(onset.m1, abs=1e-9), "peak must hold"
    # The step leaves a decaying baseline transient behind it (tau = 0.35 s), so
    # this asserts the peak is released, not that it returns to the floor.
    assert tail[-1].m1 < onset.m1 - 5.0, "peak must be released after ~1 s"


def test_bench_per_device_time_stays_flat_with_device_count():
    """SPEC §11 test 17 — 1, 2, 3, 5 devices all work; per-DEVICE time is flat.

    Per-device batching (§7.2) predicts a flat per-device cost and a total that
    scales linearly — measured 344 us at 1 device and 332 us at 5. A flat TOTAL
    would indicate cross-device batching, which this build deliberately does
    not use, so the assertion is on the per-device figure.
    """
    per_device_us = {}
    for n_dev in (1, 2, 3, 5):
        states = [{} for _ in range(n_dev)]
        # scale=6: the timing loop deliberately replays ONE fixed chunk, so the
        # gravity baseline converges to it and the dynamic content is far below
        # the raw amplitude. At scale=1 that lands under the raised M1_LO and the
        # metrics read 0, which would make the smoke assertion below vacuous.
        payload = [make_tick(*_moving(0, scale=6.0)) for _ in range(n_dev)]
        last = []
        for k in range(120):                  # warm up filters and rings
            for d in range(n_dev):
                frames, times = payload[d]
                last.append(compute({l: f.copy() for l, f in frames.items()},
                                    states[d],
                                    {l: t + k * NS / FS for l, t in times.items()}))

        # every device must still produce correct metrics, not just be fast
        for m in last[-n_dev:]:
            assert m.m1 is not None and m.m1 > 0.0
            assert m.m2 is not None and m.m3 is not None
            assert 0.0 < m.composite <= 100.0

        reps, rounds, off = 60, 5, 200
        best = float("inf")
        for _ in range(rounds):
            t0 = time.perf_counter()
            for k in range(reps):
                for d in range(n_dev):
                    frames, times = payload[d]
                    compute({l: f.copy() for l, f in frames.items()}, states[d],
                            {l: t + (off + k) * NS / FS for l, t in times.items()})
            off += reps
            best = min(best, (time.perf_counter() - t0) / reps * 1e6)
        per_device_us[n_dev] = best / n_dev
        print(f"\n  {n_dev} device(s): {best:.0f} us/tick total, "
              f"{best / n_dev:.0f} us/device")

    # Generous: this runs alongside the rest of the suite. A per-limb-loop
    # regression or per-device state leaking across devices would show up as
    # per-device cost RISING with the device count, not as a small ratio.
    assert per_device_us[5] < 2.5 * per_device_us[1], (
        f"per-device cost grew with device count: {per_device_us} us/device"
    )


def test_mid_run_sensor_fault_is_isolated_and_recovers():
    """SPEC §11 test 19 — one device loses a sensor for 2 s.

    Three things have to hold at once: the affected device degrades per §8
    (m4/m5 freeze rather than pinning at 100), the other devices are
    bit-identically unaffected, and on reconnect m1 returns within noise of a
    slot that stayed live — hold-last fill, no filter re-seeding (§7.2.1).
    """
    n_dev = 3
    states = [{} for _ in range(n_dev)]
    control: dict = {}
    fault_from, fault_to = 60 * 160, 60 * 162        # 2 s, after both warm-ups
    out: list[list] = [[] for _ in range(n_dev)]
    ctl = []

    for k in range(60 * 170):        # m4 needs 60 s settle + 60 s in-band
        a, w = _moving(k)
        t0 = k * NS / FS
        for d in range(n_dev):
            frames, times = make_tick(a, w, t0=t0)
            if d == 0 and fault_from <= k < fault_to:
                frames["left_shin"] = np.empty((0, 6), dtype=np.float32)
                times["left_shin"] = np.empty(0, dtype=np.float64)
            out[d].append(compute(frames, states[d], times))
        frames, times = make_tick(a, w, t0=t0)
        ctl.append(compute(frames, control, times))

    # 1. the untouched devices are bit-identical to a run with no fault at all
    for d in (1, 2):
        for k in (fault_from - 1, fault_from, fault_to, len(ctl) - 1):
            got, want = out[d][k], ctl[k]
            assert got.as_list() == want.as_list(), f"device {d} disturbed at k={k}"
            assert got.composite == want.composite, f"device {d} disturbed at k={k}"

    # 2. the affected device freezes m4/m5 instead of pinning them
    before = out[0][fault_from - 1]
    assert before.m4 is not None and before.m5 is not None
    for k in range(fault_from, fault_to):
        m = out[0][k]
        if m.m4 is not None:
            assert m.m4 == pytest.approx(before.m4), "m4 moved on a dead sensor"
        if m.m5 is not None:
            assert m.m5 == pytest.approx(before.m5), "m5 moved on a dead sensor"
            assert m.m5 < 100.0

    # 3. on reconnect m1 comes back within noise of the slot that stayed live
    live = max(m.m1 for m in ctl[fault_to:fault_to + 30])
    back = max(m.m1 for m in out[0][fault_to:fault_to + 30])
    assert back <= 1.2 * live, (
        f"m1 after reconnect was {back:.2f} vs {live:.2f} on a live slot — "
        "hold-last fill should return it within noise (measured 1.10x)"
    )


def test_noise_weight_is_applied_per_tick_not_to_the_accumulator():
    """SPEC §11 test 25 — W < 0.7 at the noise floor, > 0.99 while moving.

    The weighting only does anything if it is evaluated PER TICK, where the
    units match: sigma is a per-sample figure in m/s^2 while the accumulators
    are in m/s^2*s, so evaluating W against the accumulators gives 0.99999 —
    an exact no-op, and "wUSI" would silently be plain USI. An implementation
    with that bug reports W == 1 here and fails the first assertion.
    """
    rng = np.random.default_rng(23)
    still, _ = _drive(lambda k: (np.tile([0.0, 9.81, 0.0], (NS, 1))
                                 + rng.normal(0, 0.02, (NS, 3)),
                                 np.zeros((NS, 3))), 120)
    w_floor = still[-1].raw["W"]
    assert w_floor < 0.7, (
        f"W = {w_floor:.3f} at the noise floor; a per-tick weighting must "
        "suppress it there, and W == 1 means it is being applied to the "
        "accumulators instead"
    )

    moving, _ = _drive(lambda k: _moving(k), 120)
    w_move = moving[-1].raw["W"]
    assert w_move > 0.99, f"W = {w_move:.4f} during movement; must be ~1"


# =============================================================================
# 14. MAX_DEVICES cap, displacement and eviction (SPEC §7.2, test 20).
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


# --- sensors dead from the first tick (live-hardware finding, 2026-08-02) -----
# A sensor that is mapped but never streams (flat battery, bad strap contact) is
# NOT the same as one that dies mid-session, and it used to be reported wrongly:
# the role indices are built once from LIMB_MAP names, so the "can this metric
# ever be computed?" test consulted configuration instead of reality.

def _feed(dead: tuple[str, ...], ticks: int = 60 * 90):
    state = {}
    m = None
    for k in range(ticks):
        a, w = _moving(k)
        frames, times = make_tick(a, w, t0=k * NS / FS)
        for limb in dead:
            frames[limb] = np.empty((0, 6), dtype=np.float32)
            times[limb] = np.empty(0, dtype=np.float64)
        m = compute(frames, state, times)
    return m


def test_thigh_dead_from_the_start_is_degraded_not_warming_up():
    """m4 can never lock without a thigh — say so instead of waiting forever."""
    m = _feed(("left_thigh", "right_thigh"))
    assert m.m4 is None
    assert "degraded_sensors" in m.flags
    assert "warming_up" not in m.flags, (
        "`warming_up` tells the UI a value is coming; with no thigh sensor "
        "R_base can never lock and none ever will"
    )


def test_one_side_dead_from_the_start_flags_m5():
    """m5 used to go null with NO flag at all — indistinguishable from 'no asymmetry'."""
    m = _feed(("left_shin", "left_thigh"))
    assert m.m5 is None
    assert "degraded_sensors" in m.flags


def test_all_sensors_live_still_warms_up_normally():
    """The mirror: a healthy rig must keep saying `warming_up`, not `degraded`."""
    state = {}
    for k in range(30):                       # well under the 60 s m4 lock
        a, w = _moving(k)
        m = compute(*_mk(a, w, k, state))
    assert "warming_up" in m.flags
    assert "degraded_sensors" not in m.flags


def _mk(a, w, k, state):
    frames, times = make_tick(a, w, t0=k * NS / FS)
    return frames, state, times
