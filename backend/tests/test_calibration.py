"""Automatic calibration — still-detection, running sums, carry-over.

Every session calibrates itself (docs/biomech/SPEC.md §3.8): no trainer action,
no API route. Per sensor, the first 3 s of streaming is discarded as power-on
transients, then 10 s of continuous stillness — gated on SPEC §3.8's own
rejection guards — produces gain, gyro bias and noise sigma from running sums.

The reason any of this matters is `m4` and `m5`: both are INTER-SENSOR ratios,
so a per-sensor gain mismatch lands on them directly as a bias no downstream
processing can remove. test_gain_mismatch_bias_disappears_after_calibration is
SPEC §11 test 12 and is the one that demonstrates it end to end.
"""

from __future__ import annotations

import numpy as np
import pytest

from ingest import biomech
from ingest.biomech import compute
from tests.conftest import LIMBS, make_tick

FS = 600.0
NS = 10
G = 9.81


def _still(k, gain=1.0, bias=(0.0, 0.0, 0.0), noise=0.0, rng=None):
    """A stationary sensor: gravity on one axis, optional gain/bias/noise."""
    a = np.zeros((NS, 3))
    a[:, 1] = G * gain
    w = np.tile(bias, (NS, 1)).astype(float)
    if noise and rng is not None:
        a = a + rng.normal(0.0, noise, (NS, 3))
    return a, w


def _moving(k, scale=1.0, gain=1.0):
    t = (k * NS + np.arange(NS)) / FS
    a = np.stack([np.zeros(NS),
                  (G + scale * 4.0 * np.sin(2 * np.pi * 9 * t)) * gain,
                  np.zeros(NS)], 1)
    w = np.tile([scale * 120.0, 0.0, 0.0], (NS, 1))
    return a, w


def _drive(gen, n_ticks, state=None, t0=0.0, limbs=LIMBS):
    state = {} if state is None else state
    out = []
    for k in range(n_ticks):
        a, w = gen(k)
        frames, times = make_tick(a, w, limbs=limbs, t0=t0 + k * NS / FS)
        out.append(compute(frames, state, times))
    return out, state


def _sess(state):
    return state["_biomech"]


# =============================================================================
# The stand-still countdown (`raw.cal_left` -> the tick's `cal` field).
# =============================================================================

def _cal_left(metrics):
    v = metrics.raw.get("cal_left", float("nan"))
    return None if v != v else v          # NaN -> None


def test_countdown_starts_at_the_full_wait_and_falls_to_zero():
    """The badge counts DOWN, so the value must start at ~13 s (3 s discard +
    10 s window), decrease monotonically while the athlete holds still, and
    become None the moment there is nothing left to calibrate."""
    res, state = _drive(lambda k: _still(k), 60 * 20)

    first = _cal_left(res[0])
    assert first is not None and 12.5 <= first <= 13.5, (
        f"countdown should open at the full 13 s wait, got {first}"
    )
    # monotone while still (allow the float dust of a 1/60 s step)
    seq = [_cal_left(m) for m in res[: 60 * 13]]
    seq = [v for v in seq if v is not None]
    assert all(b <= a + 1e-6 for a, b in zip(seq, seq[1:])), "must not go up while still"
    assert min(seq) < 0.2, "should reach ~0 as the window closes"

    assert all(src == "measured" for src in _sess(state).cal_src)
    assert _cal_left(res[-1]) is None, "nothing left to calibrate -> no countdown"


def test_countdown_rises_again_when_the_athlete_moves():
    """Moving really does cost the accumulated stillness, so the honest
    countdown goes back up rather than pretending progress was kept."""
    res, state = _drive(lambda k: _still(k), 60 * 8)          # part-way in
    part = _cal_left(res[-1])
    assert part is not None and part < 12.0

    res2, _ = _drive(lambda k: _moving(k), 30, state=state, t0=8.0)
    after = _cal_left(res2[-1])
    assert after is not None and after > part, (
        "a reset window must show MORE time remaining, not less"
    )


def test_countdown_ignores_a_limb_that_never_streams():
    """A sensor that is not worn cannot be calibrated by standing still. If it
    pinned the countdown the athlete would be told to keep waiting forever —
    the sensor dots and `degraded_sensors` report that case instead."""
    live = ("left_shin", "left_thigh", "right_thigh")
    res, state = _drive(lambda k: _still(k), 60 * 20, limbs=live)
    sess = _sess(state)
    # the unworn limb is still in the session's limb list but never streamed
    assert _cal_left(res[-1]) is None, (
        f"countdown pinned by a non-streaming limb: cal_src={sess.cal_src}"
    )


# =============================================================================
# Still-window detection: what it accepts, and what it must refuse.
# =============================================================================

def test_still_window_calibrates_automatically():
    """15 s of stillness with no trainer action calibrates every sensor."""
    rng = np.random.default_rng(5)
    res, state = _drive(lambda k: _still(k, noise=0.02, rng=rng), 60 * 15)

    sess = _sess(state)
    assert all(src == "measured" for src in sess.cal_src), (
        f"still window never landed: {sess.cal_src}"
    )
    assert "uncalibrated" not in res[-1].flags
    assert "cal_failed" not in res[-1].flags
    # The 3 s discard plus the 10 s window means nothing lands before ~13 s.
    calibrated_at = next(i for i, m in enumerate(res)
                         if "uncalibrated" not in m.flags)
    assert 12.5 <= calibrated_at / 60.0 <= 14.5, (
        f"calibration landed at {calibrated_at / 60.0:.1f} s; expected ~13 s "
        "(3 s power-on discard + 10 s window)"
    )


def test_first_three_seconds_are_discarded():
    """Power-on transients must not reach the sums (SPEC §3.8).

    12 s of stillness is more than the 10 s window but less than discard +
    window, so nothing may calibrate yet.
    """
    res, state = _drive(lambda k: _still(k), 60 * 12)
    assert all(src == "default" for src in _sess(state).cal_src)
    assert "uncalibrated" in res[-1].flags


def test_movement_inside_the_window_rejects_it():
    """A window containing movement must not calibrate (SPEC §3.8).

    Stillness is broken at 10 s — after 7 s of accepted quiet — so the window
    restarts and the total 18 s never contains 10 continuous still seconds.
    """
    def gen(k):
        t_s = k * NS / FS
        if 10.0 <= t_s < 12.0:
            return _moving(k)
        return _still(k)

    res, state = _drive(gen, 60 * 18)
    assert all(src == "default" for src in _sess(state).cal_src), (
        "a window containing movement was accepted"
    )
    assert "uncalibrated" in res[-1].flags


def test_rotation_inside_the_window_rejects_it():
    """mean|w| above CAL_MAX_ROTATION_DPS is the athlete moving (SPEC §3.8)."""
    res, state = _drive(lambda k: _still(k, bias=(30.0, 0.0, 0.0)), 60 * 20)
    assert all(src == "default" for src in _sess(state).cal_src)


def test_sensor_reading_a_bad_magnitude_never_calibrates():
    """A sensor sitting at |a| = 8.0 is faulty, not still (SPEC §3.8).

    18% off gravity is far outside the 6% guard, so the window is refused for
    as long as the fault lasts — defaults stay in place rather than a 1.23x
    gain being baked in.
    """
    res, state = _drive(lambda k: _still(k, gain=8.0 / G), 60 * 20)
    sess = _sess(state)
    assert all(src == "default" for src in sess.cal_src)
    assert sess.cal[LIMBS[0]].k == 1.0, "a bad magnitude was baked in as gain"
    assert "uncalibrated" in res[-1].flags


def test_gain_error_within_correctable_range_calibrates():
    """A motionless sensor 4% off gravity MUST calibrate (2026-08-04 fix).

    The k guard declares gains up to 5% correctable, so the stillness guard
    has to accept the very sensors calibration exists to fix. At the original
    2% guard this sensor failed the acceptance test every tick, never
    accumulated a window, was branded cal_failed at 20 s — and pinned the
    badge countdown forever. This is the failure observed on real hardware:
    held still for 30-40 s, never calibrated, ran on carried values.
    """
    res, state = _drive(lambda k: _still(k, gain=1.04), 60 * 20)
    sess = _sess(state)
    assert all(src == "measured" for src in sess.cal_src), (
        f"a correctable gain error was refused: {sess.cal_src}"
    )
    assert sess.cal[LIMBS[0]].k == pytest.approx(1.0 / 1.04, rel=1e-3), (
        "the measured k must correct the 4% gain error"
    )
    assert "uncalibrated" not in res[-1].flags
    assert "cal_failed" not in res[-1].flags
    assert _cal_left(res[-1]) is None


def test_faulted_sensor_does_not_pin_the_countdown():
    """One faulty sensor must not keep the countdown alive forever.

    Same argument as a never-streaming limb: more stillness cannot fix a
    faulty sensor, so once it is flagged cal_failed it leaves the countdown —
    which goes to None the moment every healthy sensor is measured. The
    cal_failed chip is what reports the fault from there.
    """
    healthy = ("left_shin", "left_thigh", "right_thigh")

    def gen_pair(k):
        t0 = k * NS / FS
        fh, th = make_tick(*_still(k), limbs=healthy, t0=t0)
        fb, tb = make_tick(*_still(k, gain=1.10), limbs=("right_shin",), t0=t0)
        return {**fh, **fb}, {**th, **tb}

    state: dict = {}
    res = []
    for k in range(60 * 30):
        f, t = gen_pair(k)
        res.append(compute(f, state, t))

    sess = _sess(state)
    for limb in healthy:
        assert sess.cal_src[sess.idx[limb]] == "measured", (
            f"{limb} should have calibrated normally: {sess.cal_src}"
        )
    i_bad = sess.idx["right_shin"]
    assert sess.cal_src[i_bad] == "default", "the bad correction must not land"
    assert sess.cal_failed[i_bad], "10% off gravity while motionless is a fault"
    assert "cal_failed" in res[-1].flags
    assert _cal_left(res[-1]) is None, (
        "a faulted sensor pinned the countdown — the badge would flash forever"
    )


def test_stillness_must_be_continuous():
    """Ten separate seconds of quiet are not a 10 s window."""
    def gen(k):
        # 1.5 s still, 0.5 s moving, repeating: never 10 s continuous
        phase = (k * NS / FS) % 2.0
        return _still(k) if phase < 1.5 else _moving(k)

    _, state = _drive(gen, 60 * 60)
    assert all(src == "default" for src in _sess(state).cal_src)


# =============================================================================
# Running sums: the three SPEC §3.8 outputs, to 0.1%.
# =============================================================================

def test_running_sums_recover_known_gain_bias_and_sigma():
    """Recover synthetic ground truth within 0.1% — without buffering samples.

    Gain, gyro bias and noise sigma are exact functions of sum|a|, sum(w) and
    sum|a|^2, which is why 10 s x 600 Hz x 4 sensors of raw samples never has
    to be held.
    """
    rng = np.random.default_rng(17)
    true_gain, true_bias, true_sigma = 1.012, np.array([0.4, -0.2, 0.1]), 0.030

    def gen(k):
        a = np.zeros((NS, 3))
        a[:, 1] = G * true_gain
        a = a + rng.normal(0.0, true_sigma, (NS, 3))
        return a, np.tile(true_bias, (NS, 1))

    # A long window averages the noise down so the assertion is about the
    # estimator, not about the sample count.
    _, state = _drive(gen, 60 * 60)
    got = _sess(state).cal[LIMBS[0]]

    assert got.k == pytest.approx(1.0 / true_gain, rel=1e-3)
    assert got.gyro_bias == pytest.approx(true_bias, abs=1e-3)
    # sigma is the SD of |a|, and |a| only sees the noise component along
    # gravity, so it recovers the per-axis sigma.
    assert got.sigma == pytest.approx(true_sigma, rel=0.05)


def test_calibration_composes_with_carried_values():
    """Refinement, not replacement.

    The sums are taken on the corrected signal, so a sensor already running a
    carried-over k measures the RESIDUAL error. Multiplying it into the applied
    k is what makes the total converge on truth instead of on 1.0.
    """
    state: dict = {}
    _drive(lambda k: _still(k), 5, state)         # create the session
    biomech.apply_carried_calibration(
        state, {limb: {"k": 0.99, "gyro_bias": [0.0, 0.0, 0.0], "sigma": 0.035}
                for limb in LIMBS}, LIMBS)
    assert _sess(state).cal_src[0] == "carried"

    # The sensor reads 2% high; the carried k of 0.99 already removes half of
    # that, leaving a ~1% residual — inside the stillness guard, which is what
    # lets a corrected sensor keep measuring.
    _drive(lambda k: _still(k, gain=1.02), 60 * 20, state, t0=5 * NS / FS)
    got = _sess(state).cal[LIMBS[0]]
    assert _sess(state).cal_src[0] == "measured"
    assert got.k == pytest.approx(1.0 / 1.02, rel=1e-3), (
        "measured k replaced the carried value instead of composing with it"
    )


def test_rejected_calibration_sets_cal_failed_and_keeps_seeking():
    """The k guard rejects rather than baking in a bad correction (SPEC §3.8).

    A carried-over k of 1.05 that is itself wrong compounds with this session's
    residual and lands outside [0.95, 1.05]. The correction is refused, the
    previous values stand, cal_failed is raised, and the search continues.
    """
    state: dict = {}
    _drive(lambda k: _still(k), 5, state)
    biomech.apply_carried_calibration(
        state, {limb: {"k": 1.05, "gyro_bias": [0.0, 0.0, 0.0], "sigma": 0.035}
                for limb in LIMBS}, LIMBS)

    # |a| reads 1.5% low after the carried k, so k_total = 1.05 * 1.015 > 1.05.
    res, _ = _drive(lambda k: _still(k, gain=1.0 / 1.05 * 0.985), 60 * 20,
                    state, t0=5 * NS / FS)
    sess = _sess(state)
    assert "cal_failed" in res[-1].flags
    assert all(src == "carried" for src in sess.cal_src), (
        "a rejected calibration must leave last-known-good in place"
    )
    assert sess.cal[LIMBS[0]].k == pytest.approx(1.05)
    # Rejection restarts the window rather than latching, so by the end of the
    # run the next attempt is already part-way accumulated.
    assert 0.0 < sess.cal_dur[0] < biomech.CAL_WINDOW_S, (
        "a rejected window must clear and keep seeking"
    )


# =============================================================================
# Carry-over between sessions.
# =============================================================================

def test_carry_over_applies_with_no_still_window():
    """A device with history never runs on defaults (SPEC §3.8).

    This session is movement from the first tick, so no still window can ever
    land — which is exactly when carry-over is the only thing standing between
    m4/m5 and an uncorrected gain mismatch.
    """
    carried = {limb: {"k": 1.004, "gyro_bias": [0.3, 0.0, -0.1], "sigma": 0.031}
               for limb in LIMBS}
    state: dict = {}
    biomech.apply_carried_calibration(state, carried, LIMBS)
    res, state = _drive(lambda k: _moving(k), 60 * 40, state)

    sess = _sess(state)
    assert all(src == "carried" for src in sess.cal_src)
    assert sess.cal[LIMBS[0]].k == pytest.approx(1.004)
    assert "carried_over" in res[-1].flags
    assert "uncalibrated" not in res[-1].flags, (
        "carried-over values are not defaults — the UI has to tell them apart"
    )


def test_carried_over_is_replaced_by_a_measured_window():
    """Start calibrated, then refine: the flag marks which one is live."""
    carried = {limb: {"k": 1.004, "gyro_bias": [0.0, 0.0, 0.0], "sigma": 0.031}
               for limb in LIMBS}
    state: dict = {}
    biomech.apply_carried_calibration(state, carried, LIMBS)
    res, state = _drive(lambda k: _still(k), 60 * 20, state)

    assert all(src == "measured" for src in _sess(state).cal_src)
    assert "carried_over" not in res[-1].flags
    assert "carried_over" in res[60 * 5].flags, "carried values must apply from tick 1"


def test_session_reset_demotes_measured_to_carried():
    """A new session re-measures but is never left on defaults (SPEC §3.8)."""
    _, state = _drive(lambda k: _still(k), 60 * 15)
    sess = _sess(state)
    assert all(src == "measured" for src in sess.cal_src)
    k_measured = sess.cal[LIMBS[0]].k

    biomech.reset_session(state)
    assert all(src == "carried" for src in sess.cal_src)
    assert sess.cal[LIMBS[0]].k == k_measured, "calibration must survive the reset"
    assert sess.cal_age[0] == 0.0, "the 3 s power-on discard restarts too"


def test_export_is_none_until_something_beats_defaults():
    _, state = _drive(lambda k: _moving(k), 60)
    assert biomech.calibration_export(state) is None
    _, state = _drive(lambda k: _still(k), 60 * 15)
    exported = biomech.calibration_export(state)
    assert exported is not None and set(exported) == set(LIMBS)
    assert set(exported[LIMBS[0]]) == {"k", "gyro_bias", "sigma"}


# =============================================================================
# SPEC §11 test 12 — the reason calibration is worth taking at all.
# =============================================================================

def test_gain_mismatch_bias_disappears_after_calibration():
    """A 3% L/R gain mismatch biases m5, and calibration removes it (test 12).

    m5 is an inter-sensor ratio, so a gain mismatch lands on it directly as a
    bias nothing downstream can remove. The mismatch is applied as +-1.5% per
    side; the stillness guard now covers the whole correctable k range (6%,
    see test_gain_error_within_correctable_range_calibrates), so either split
    would calibrate — the symmetric one keeps the fixture's m5 math simple.
    """
    half = 1.015                       # +-1.5% => 3% between the sides

    def run(with_still_window: bool):
        state: dict = {}
        t0 = 0.0
        if with_still_window:
            for k in range(60 * 15):   # 15 s standing still => calibration lands
                fl, tl = make_tick(*_still(k, gain=half),
                                   limbs=("left_shin", "left_thigh"),
                                   t0=k * NS / FS)
                fr, tr = make_tick(*_still(k, gain=1.0 / half),
                                   limbs=("right_shin", "right_thigh"),
                                   t0=k * NS / FS)
                compute({**fl, **fr}, state, {**tl, **tr})
            t0 = 60 * 15 * NS / FS
        last = None
        for k in range(60 * 120):      # 120 s of symmetric movement
            fl, tl = make_tick(*_moving(k, gain=half),
                               limbs=("left_shin", "left_thigh"),
                               t0=t0 + k * NS / FS)
            fr, tr = make_tick(*_moving(k, gain=1.0 / half),
                               limbs=("right_shin", "right_thigh"),
                               t0=t0 + k * NS / FS)
            last = compute({**fl, **fr}, state, {**tl, **tr})
        return last, state

    biased, biased_state = run(with_still_window=False)
    assert all(src == "default" for src in _sess(biased_state).cal_src)
    assert biased.m5 is not None
    assert biased.m5 > 5.0, (
        f"a 3% gain mismatch should bias m5 (got {biased.m5:.2f}); the fixture "
        "is not exercising what calibration is for"
    )

    corrected, corrected_state = run(with_still_window=True)
    assert all(src == "measured" for src in _sess(corrected_state).cal_src)
    assert corrected.m5 is not None
    assert corrected.m5 < 0.5, (
        f"m5 still carries {corrected.m5:.2f} points of gain bias after "
        f"calibration (uncalibrated: {biased.m5:.2f})"
    )


# =============================================================================
# Live-hardware findings (2026-08-02 capture session)
# =============================================================================

def test_whole_device_dropout_breaks_the_still_window() -> None:
    """A held tick must AGE the window, not freeze it.

    compute() returns early when no limb produced samples, which used to skip
    the still-window update entirely: a half-built window survived a dropout of
    any length and resumed as though the stillness had been continuous. The real
    link drops packets in bursts, so this is the case that actually occurs.
    """
    state: dict = {}
    t = 0.0
    for _ in range(300):                       # 5 s of stillness, well past the discard
        f, ts = make_tick(np.tile([0.0, G, 0.0], (NS, 1)),
                          np.zeros((NS, 3)), t0=t, fs=FS)
        compute(f, state, ts)
        t += NS / FS
    sess = _sess(state)
    assert sess.cal_dur.max() > 1.0, "no window accumulated; fixture is wrong"

    # Whole device silent: frames present but empty -> held ticks.
    empty = {limb: np.zeros((0, 6), dtype=np.float32) for limb in LIMBS}
    for _ in range(60):                        # 1 s >> CAL_MAX_GAP_S
        compute(empty, state, {limb: np.zeros(0) for limb in LIMBS})

    assert sess.cal_dur.max() == 0.0, (
        "the still window survived a 1 s whole-device dropout — it must be "
        "cleared, because nothing verified the athlete stayed still"
    )


def test_motionless_but_wrong_gravity_is_reported_as_a_sensor_fault() -> None:
    """Gyro says still + |a| disagrees with gravity => cal_failed, not silence.

    Previously the two guards were one test, so a mis-scaled sensor simply never
    accumulated a window and read `uncalibrated` for the whole session — exactly
    what an athlete who never stood still looks like. The gyro disambiguates it.
    """
    state: dict = {}
    t = 0.0
    ticks = int((biomech.CAL_FAULT_S + biomech.CAL_DISCARD_S + 2.0) * 60)
    for _ in range(ticks):
        # 10% high on every axis-magnitude, perfectly motionless.
        f, ts = make_tick(np.tile([0.0, G * 1.10, 0.0], (NS, 1)),
                          np.zeros((NS, 3)), t0=t, fs=FS)
        m = compute(f, state, ts)
        t += NS / FS

    sess = _sess(state)
    assert sess.cal_failed.all(), "a motionless, mis-scaled sensor must be flagged"
    assert "cal_failed" in m.flags
    assert all(src == "default" for src in sess.cal_src), (
        "a bad correction must never be baked in — defaults are kept"
    )


def test_moving_athlete_is_not_blamed_on_the_sensor() -> None:
    """The mirror of the above: rotation present => no fault flag, ever."""
    state: dict = {}
    t = 0.0
    ticks = int((biomech.CAL_FAULT_S + biomech.CAL_DISCARD_S + 2.0) * 60)
    for _ in range(ticks):
        f, ts = make_tick(np.tile([0.0, G * 1.10, 0.0], (NS, 1)),
                          np.tile([0.0, 0.0, 60.0], (NS, 1)), t0=t, fs=FS)
        m = compute(f, state, ts)
        t += NS / FS

    assert not _sess(state).cal_failed.any()
    assert "cal_failed" not in m.flags
