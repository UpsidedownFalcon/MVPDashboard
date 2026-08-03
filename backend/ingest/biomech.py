"""Biomech model — 5 orientation-free primitives + load-vs-capacity composite.

Implements docs/biomech/SPEC.md. Section references below are to that document;
read it before changing anything here, because most of the non-obvious choices
exist to avoid a specific, measured failure.

Orientation-free by mandate (SPEC Section 1): every quantity is a
rotation-invariant magnitude (|a|, |w|) or a derivative. No absolute angles, no
complementary/Kalman filter, no bone-aligned axis assumption.

    m1  Impact           peak dynamic accel at the shank
    m2  Loading Rate     exact linear jerk  ||da/dt + w x a||
    m3  Accumulated Load power-law-weighted decaying dose
    m4  Movement Control |drift| of shank->thigh transmission vs session baseline
    m5  L/R Balance      weighted Universal Symmetry Index of accumulated load
    composite            load vs capacity

All six outputs are 0..100. m1..m5 may be None (warm-up, missing sensors,
saturation); composite never is.

!! m4 AND m5 ARE VALIDATED ON SYNTHETIC FIXTURES ONLY (SPEC Section 11.1) !!
The only real capture (example/squats.bin) holds ~19.9 s of movement, below
m4's 60 s baseline lock and m5's 30 s warm-up, so neither metric emits a single
value on it. Both carry the `unvalidated` flag for the whole of stage 1. Do not
present them to a trainer as a finding until a >=10 min session with a
deliberate fatigue block has been recorded and SPEC Section 6.4 gains real rows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import lfilter

from common.scaling import (
    ACCEL_MS2_PER_COUNT,
    CAL_K_MAX,
    CAL_K_MIN,
    CAL_MAX_G_ERROR,
    CAL_MAX_ROTATION_DPS,
    DEFAULT_CALIBRATION,
    GRAVITY_MS2,
    GYRO_DPS_PER_COUNT,
    SAT_SUPPRESS_FRACTION,
    SAT_THRESHOLD_COUNTS,
    Calibration,
)

# =============================================================================
# Tunables. All provisional starting points requiring calibration against trial
# data (SPEC Section 4) -- NOT validated clinical cut-offs. These are model
# parameters and deliberately live here, not in .env: if they drifted per
# deployment the numbers would stop being comparable between sessions.
# =============================================================================

# --- signal conditioning (SPEC Sections 3.5, 3.6) ---
FILTER_CUTOFF_HZ = 75.0      # not 50: 50 Hz keeps 97% of peak accel but only
                             # 36-75% of peak JERK, and m2 is a derivative
GRAVITY_TAU_S = 0.35         # baseline tracks the ~constant |g|
NOMINAL_INPUT_HZ = 600.0     # fallback dt only; real dt comes from `times`
MAX_DT_S = 0.05              # a gap larger than this is a discontinuity, not a
                             # sample interval -> fall back to nominal

# --- windows ---
PEAK_WINDOW_TICKS = 60       # 1.0 s of per-tick summaries at 60 Hz
CONTROL_TAU_S = 20.0         # m4 transmission-ratio averaging
MOVE_GATE_MS2 = 0.10         # tick-mean adyn: measured still 0.025-0.032,
                             # squatting 0.65-0.94 -- ~3x above the noise floor

# --- reference ranges (SPEC Section 4) ---
# Floors raised from a live 11-minute wearing session (2026-08-02). The old
# M1_LO_FLOOR of 0.15 m/s^2 sat barely above the rest noise floor, so ordinary
# walking already scored 57/100 and everything from an easy walk to near-maximal
# effort was squeezed into ten points. Measured shank p90 |a_dyn| that session:
# still 0.2, squats 4.2, walking 8.4, jumps 11.3, hard interval work 16.7 m/s^2.
# The CEILINGS are deliberately NOT anchored to that session -- its hardest
# impact was 11 m/s^2 against 30-150 in the landing literature, so anchoring the
# top to it would peg a real athlete at 100 permanently. Raise the floor, keep
# the headroom.
# Re-anchored 2026-08-03 against a worn session covering walk / jog / run / jump
# / single-leg landing / deceleration / change-of-direction / kick / squat. Both
# ends were wrong:
#
#   floor 2.0 m/s^2 put an ordinary WALK at 40/100, so 40 points of the scale
#   covered 2 -> 11 m/s^2 (nothing happening) and the whole athletic range was
#   squeezed into the remaining 60.
#
#   ceiling 150 m/s^2 is 15.3 g, which real movement EXCEEDS. Published
#   resultant peak tibial acceleration: walking 2.7-3.7 g, running 5-12 g,
#   max sprint 20.1 +- 9.0 g, single-leg hop landing 27.2 +- 7.9 g, and
#   vertical jumping up to 42 g. So a jump read exactly 100 because the scale
#   ENDED there -- censoring, not measurement. Jump, landing, kick and
#   deceleration all reported 100 and were therefore indistinguishable.
#
# Back-solved from that session at the old bounds: walk/squat ~11 m/s^2,
# jog ~14, run ~17, kick ~27, single-leg landing ~63, jump >=150 (censored).
M1_LO_FLOOR = 6.0            # m1 lo = max(M1_LO_FLOOR, M1_LO_SIGMAS * sigma)
M1_LO_SIGMAS = 5.0           # noise-adaptive: a noisier sensor must not read >0 at rest
M1_HI = 400.0                # ~41 g: vertical jumping reaches 42 g
# 🚩 The accelerometer is +-16 g per axis, so a resultant of 27.7 g clips every
# axis and jumping reaches 42 g -- some landing peaks are already truncated
# (SPEC open item 2 recommends >= +-32 g). Raising the ceiling does not create
# that problem, it makes it VISIBLE instead of hiding it at a pinned 100.
# m2 lo moves to the osteogenic jerk threshold (981 m/s^3, Jamsa 2011) rather
# than the rest floor: below that the loading rate is not doing anything worth
# scoring. Ceiling raised because 12,000 was being clipped by ordinary interval
# work (session p90 hit 100 in three separate phases).
# Same re-anchoring as m1. 800 m/s^3 sat BELOW walking (which back-solves to
# ~3,400 m/s^3, already inside the published running-impact jerk band of
# 2,500-7,800), so a walk read 40/100; and 30,000 was exceeded by a small jump.
M2_LO, M2_HI = 2_500.0, 80_000.0
# dose-minutes, where 1 dose-minute == 1 minute of hard-training equivalent
# (see A_DOSE_REF/W_DOSE_REF). Floor = 30 s of that, ceiling = a full hard hour.
# Re-anchored 2026-08-03 with the dose law: the old 0.01 floor was 0.6 s of
# hard-training equivalent, so ANY movement cleared it within a second and the
# bottom of the scale was unreachable. Measured on the ladder at these bounds:
# 45 min continuous slow walk -> 0, light jog -> 56, hard run -> 87; 10 min hard
# run -> 61, 1 min -> 14.
# Floor cut 0.5 -> 0.03 on 2026-08-03. 0.5 dose-minutes is 30 s of
# hard-training equivalent, which was ABOVE every bout in a real worn session:
# 60 s of running accumulates ~0.31 and a set of bodyweight squats ~0.005, so
# m3 read exactly 0 for everything tested. 0.03 is ~2 s of hard-training
# equivalent, low enough that a 30-60 s bout registers.
#
# ⚠️ A_DOSE_REF / W_DOSE_REF below are still measured on a SYNTHETIC generator,
# so the absolute dose rate is an estimate. scripts/calibrate_capture.py closes
# that against a real capture.
M3_LO, M3_HI = 0.03, 60.0
W_LO, W_HI = 5.0, 1500.0           # deg/s; still mean 1.6, squat mean 82, sprint >1200

# --- m3 dose (SPEC Section 5.3) ---
DOSE_EXPONENT = 3.0          # load accumulates as a POWER LAW, not linearly.
                             # Bone daily-stress-stimulus uses m=4 (Whalen 1988);
                             # fatigue damage ~2.1 sub-threshold (Pattin 1996);
                             # 3 is a deliberate conservative middle.
DOSE_HALFLIFE_S = 45 * 60.0
# Physical reference intensities for the dose power law -- the sustained level
# of HARD RUNNING on each arm, so one dose-minute == one minute of hard-training
# equivalent and M3_HI = 60 reads as "a full hard hour". These set the SCALE of
# the dose; DOSE_EXPONENT above sets its shape.
#
# Measured 2026-08-03 through this pipeline on an activity ladder built from
# literature resultant PTA (walk 2.7-3.7 g, jog ~5 g, run 8-12 g, sprint ~20 g,
# drop landing ~27 g). What matters is the CUBE-mean E[x^3]^(1/3), not the
# median -- the dose integrates every tick and cubing lets impact ticks dominate,
# so a median understates it several-fold. Cube-means at a 12 g / 180 spm hard
# run: |a_dyn| 7.38 m/s^2, |w| 539 deg/s.
A_DOSE_REF = 7.4             # m/s^2, cube-mean tick dynamic accel
W_DOSE_REF = 540.0           # deg/s, cube-mean tick rotational rate

# --- m4 control (SPEC Section 5.4) ---
M4_BASELINE_LOCK_S = 60.0    # movement time IN A BAND before its baseline locks
M4_FULL_SCALE = 0.50         # |R/R_base - 1| of 0.5 reads 100
# Shank-EMA |a_dyn| edges (m/s^2) splitting movement into intensity bands.
# Rebuilt 2026-08-03: a SINGLE baseline made m4 an activity-change detector.
# It locked from ONE tick's value at the moment cumulative movement first hit
# 60 s -- whatever the athlete happened to be doing -- and was never re-learned.
# Shock transmission genuinely differs between walking and running as
# PHYSIOLOGY, not fatigue, so switching activity moved R/R_base past the 50%
# full scale and pinned m4 at 100. Measured on a worn session: m4 sat at 90-100
# (saturated) while jogging, 75-85 running, and unchanged during squats -- the
# activity the baseline had locked on.
#
# Banding compares like with like, so m4 answers a question that is actually
# answerable: "at the intensity being worked at RIGHT NOW, is shock being
# transmitted differently than when fresh at this same intensity?"
M4_BAND_EDGES = (0.3, 1.0, 3.0)
M4_BANDS = len(M4_BAND_EDGES) + 1
# Movement time before ANY band starts learning. THREE transmission time
# constants, not one: at 1 tau the EMA is only 63% converged, so a baseline
# learned then is still partly the warm-up transient and the band reads high
# forever after. At 3 tau it is ~95% there.
M4_SETTLE_S = 3.0 * CONTROL_TAU_S

# --- m5 balance (SPEC Section 5.5) ---
ASYM_HALFLIFE_S = 5 * 60.0
# Fast asymmetry channel, DIAGNOSTIC ONLY (published in `raw.usi_fast_pct`).
# The 5-minute accumulator is a chronic measure by design, so one second of
# one-sided load displaces ~0.2% of it once the session is established -- a
# single-leg landing barely moved m5 on a real worn session. Note the reported
# m5's sensitivity is also NON-STATIONARY and never declared: the accumulators
# start empty, so the effective averaging length is min(elapsed, 433 s) and m5
# is roughly 14x more event-sensitive just after its 30 s warm-up gate than it
# is seven minutes later. This channel is here to size a fix, not to ship one.
ASYM_FAST_HALFLIFE_S = 10.0
M5_WARMUP_S = 30.0           # the accumulator is rep-dominated before this
PARTIAL_DEBOUNCE_S = 0.75    # `partial` must persist this long before it shows.
                             # A lossy link makes a limb's `active` flag flicker
                             # tick to tick; undebounced, the flag toggled
                             # hundreds of times per second in a live session and
                             # would strobe the UI. Integrator, so clearing it
                             # needs the same dwell -- hysteresis, not a timer.
M5_FULL_SCALE_USI = 0.18     # from Delgado-Garcia 2025 (bilateral tibial
                             # accelerometry): asymmetry 9% -> 25% classic SI
                             # over a fatiguing run; USI ~ SI/sqrt(2) near
                             # symmetry -> fresh ~6.4%, fatigued ~17.7%

# How long a frozen m4/m5 stays held before going None + `partial`.
STALE_TIMEOUT_S = 20.0

# --- automatic calibration (SPEC Section 3.8) ---
# There is no trainer-triggered routine: every session calibrates itself the
# moment the athlete happens to stand still for long enough, per sensor and
# independently, so sensors settle at whatever moment each one goes quiet.
CAL_DISCARD_S = 3.0          # power-on transients, discarded per SPEC 3.8
CAL_WINDOW_S = 10.0          # continuous stillness required, per sensor
CAL_MAX_GAP_S = 0.25         # empty ticks tolerated inside a still window: a
                             # dropped packet is not evidence the athlete moved,
                             # but a real dropout means the gap is unverified
CAL_FAULT_S = 20.0           # motionless (per gyro) but |a| off gravity for this
                             # long => the SENSOR is wrong, not the athlete, so
                             # say cal_failed instead of `uncalibrated` forever
# Last-known-good carry-over lives for weeks: accel gain is stable across
# sessions (SPEC 3.8), so a device that has ever calibrated should never run on
# defaults again. Gyro bias drifts with temperature and is re-measured anyway.
CAL_CARRY_TTL_S = 30 * 24 * 3600

# --- composite (SPEC Section 6.1) ---
DEMAND_MAX_W, DEMAND_MIN_W = 0.60, 0.40
DEGRADE_W_M4, DEGRADE_W_M5 = 0.45, 0.30    # m3 is NOT here: it drives `floor`
# Capacity reduction per point of degradation. CUT 0.70 -> 0.15 on 2026-08-03,
# so capacity floors at 85 rather than 30.
#
# m4 and m5 carry the `unvalidated` flag: synthetic fixtures only, no real-data
# validation (SPEC Section 11.1). At 0.70 they could remove 42 capacity points,
# and because `acute` raises demand/capacity to the FOURTH power that is far more
# leveraged than the linear form suggests. Measured on a real worn session:
#
#   demand 60, m3 0    capacity   ratio   composite
#   m4=0  m5=0            100.0   0.600      23
#   m4=75 m5=14            64.6   0.929      85   <- crosses WARN
#   m4=90 m5=0             62.2   0.965      93   <- crosses ALERT
#   m4=90 m5=None          37.0   1.622     100   <- pinned
#
# The same physical session read 23 or 93 depending only on an unvalidated
# number. The label said `unvalidated` on the metric's own card while the value
# silently set the alert-severity headline; SPEC Section 2 forbids exactly that.
#
# The last row is the sharpest case: `degradation` renormalises over AVAILABLE
# terms (SPEC Section 8, so fewer sensors are not scored as lower risk), which
# means that whenever m5 is None -- common, it freezes on any missing side --
# m4 ALONE sets degradation at full value and its 0.45 weight is cosmetic.
# Renormalisation is kept; the cap is what makes it safe.
#
# Restore this toward 0.70 when m4/m5 gain real-data validation (open item 10).
CAPACITY_FACTOR = 0.15
FLOOR_FACTOR = 0.50
# Demand is EXPOSURE, not peak. m1/m2 are 1 s peak-holds -- correct for metrics
# named Impact and Loading Rate -- but feeding them straight into a risk index
# made one landing outrank a minute of running. Measured on a worn session: a
# small forward jump pinned the composite at 100 while sustained running, which
# the wearer rated as the higher risk, read the same. Kalkhoven's model is about
# damage accumulating toward a threshold, so the risk term should follow
# exposure. m1/m2 are unchanged; only the composite sees the smoothed value.
DEMAND_TAU_S = 25.0
# Hill exponent on the load/capacity ratio. Raised 2 -> 4 on 2026-08-03: at n=2
# the curve was far too eager at the bottom, and the wearer reported ordinary
# activity reading as substantial injury risk -- a slow walk 20-30 and a light
# jog 50-70. Measured on an activity ladder driven through this pipeline
# (literature resultant PTA: walk 2.7-3.7 g, jog ~5 g, run 8-12 g, sprint ~20 g,
# drop landing ~27 g), n=2 gave slow walk 26.0 and light jog 55.3 -- reproducing
# the complaint almost exactly. See SPEC Section 6.1 for the full ladder.
#
# n=4 is not a fitted constant. Injury is a THRESHOLD event, not an average:
# damage accumulates until it exceeds a critical threshold, or until load
# exceeds a declining tissue strength (Kalkhoven 2021/2026 first-principles
# model), and mechanical damage is driven far more by load MAGNITUDE than by
# load frequency. The bone daily-stress-stimulus law uses exponent m = 4
# (Whalen 1988) and fatigue-damage exponents span 2.1-5.8 (Pattin 1996), so a
# 4th-power ratio is the same physics the dose term already uses. The practical
# effect is that risk stays near zero through ordinary ambulation and only
# climbs as demand approaches capacity, which is what the model claims to mean.
ACUTE_EXPONENT = 4.0

SESSION_GAP_S_DEFAULT = 300.0

# Limb-role parsing. LIMB_MAP names are configurable, so roles are derived from
# the name rather than hardcoded; this also makes fewer-sensor configs work.
_SHANK_WORDS = ("shin", "shank", "tibia", "calf")
_THIGH_WORDS = ("thigh", "femur", "quad")


def limb_role(limb: str) -> tuple[str | None, str | None]:
    """('left'|'right'|None, 'shank'|'thigh'|None) from a limb name."""
    low = limb.lower()
    side = "left" if "left" in low else "right" if "right" in low else None
    if any(w in low for w in _SHANK_WORDS):
        seg = "shank"
    elif any(w in low for w in _THIGH_WORDS):
        seg = "thigh"
    else:
        seg = None
    return side, seg


@dataclass
class Metrics:
    m1: float | None
    m2: float | None
    m3: float | None
    m4: float | None
    m5: float | None
    composite: float
    flags: frozenset[str] = frozenset()
    raw: dict = field(default_factory=dict)

    def as_list(self) -> list[float | None]:
        return [self.m1, self.m2, self.m3, self.m4, self.m5]


HELD_ZERO = Metrics(0.0, 0.0, 0.0, None, None, 0.0, frozenset({"warming_up"}), {})


def _intensity_band(sh_mean: float) -> int:
    """Which m4 intensity band a shank-EMA |a_dyn| falls in (SPEC Section 5.4).

    Bands exist so m4 compares shock transmission at like intensities. Walking
    and running transmit differently as physiology; without banding that read as
    maximum 'movement control' degradation the moment the athlete changed pace.
    """
    band = 0
    for edge in M4_BAND_EDGES:
        if sh_mean < edge:
            return band
        band += 1
    return band


def log_score(x: float, lo: float, hi: float) -> float:
    """0..100 on a log scale (SPEC Section 4).

    Log, not linear: the measured dynamic range between lifting and running is
    ~50x, so a linear scale calibrated for running renders every gym session
    as ~2/100.
    """
    if not math.isfinite(x) or x <= 0.0:
        return 0.0
    frac = math.log(max(x, 1e-12) / lo) / math.log(hi / lo)
    return 100.0 * min(max(frac, 0.0), 1.0)


def _onepole(alpha: float) -> tuple[list[float], list[float]]:
    """b, a for y[n] = alpha*x[n] + (1-alpha)*y[n-1]."""
    return [alpha], [1.0, -(1.0 - alpha)]


def _p90_axis0(x: np.ndarray) -> np.ndarray:
    """90th percentile down axis 0, linear interpolation. Same result as
    np.percentile(x, 90, axis=0), ~10x cheaper for the ~10-sample blocks we get:
    np.percentile's cost here is almost entirely dispatch/validation overhead,
    and it profiled as the single most expensive call in compute().
    """
    n = x.shape[0]
    if n == 1:
        return x[0].copy()
    pos = 0.9 * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    part = np.partition(x, (lo, hi), axis=0)
    a, b = part[lo], part[hi]
    return a + (pos - lo) * (b - a)


def _zi_for(alpha: float, steady: np.ndarray) -> np.ndarray:
    """Steady-state lfilter initial condition (transposed direct form II).

    For this one-pole it is (1-alpha)*v, NOT v. Getting it wrong injects
    exactly the transient it is meant to remove (SPEC Section 7.2.1).
    """
    return ((1.0 - alpha) * steady)[None, :]


class _Sess:
    """Per-device biomech state, stored in the caller's `state` dict under _KEY."""

    def __init__(self, limbs: tuple[str, ...]) -> None:
        self.limbs = limbs
        n = len(limbs)
        self.idx = {limb: i for i, limb in enumerate(limbs)}
        self.roles = [limb_role(limb) for limb in limbs]
        self.cal: dict[str, Calibration] = {limb: DEFAULT_CALIBRATION for limb in limbs}
        # Where each limb's calibration came from: 'default' (nothing known),
        # 'carried' (last-known-good from a previous session) or 'measured'
        # (a still window landed this session). Drives the uncalibrated /
        # carried_over flags, which is how the UI tells a step change in the
        # numbers apart from a change in the athlete (SPEC Section 3.8).
        self.cal_src: list[str] = ["default"] * n
        self.cal_failed = np.zeros(n, dtype=bool)
        # Still-window detection: running sums only. 10 s x 600 Hz x 4 sensors
        # of raw samples is ~2 MB per device to produce three scalars that are
        # exact functions of these sums (SPEC Section 3.8).
        self.cal_age = np.zeros(n)       # streaming time since the session began
        self.cal_dur = np.zeros(n)       # continuous stillness accumulated
        self.cal_gap = np.zeros(n)       # time with no samples inside a window
        self.cal_bad_g = np.zeros(n)     # time motionless-but-wrong-|a| (fault)
        self.cal_n = np.zeros(n)         # samples in the window
        self.cal_s1 = np.zeros(n)        # sum |a|
        self.cal_s2 = np.zeros(n)        # sum |a|^2
        self.cal_sw = np.zeros((n, 3))   # sum w (3-vector)
        # Role index sets and the calibration-derived scalars are fixed for the
        # life of the session, so they are resolved once here rather than being
        # rebuilt 60 times a second.
        self.shank_i = np.array([i for i, (_, s) in enumerate(self.roles)
                                 if s == "shank"], dtype=int)
        self.thigh_i = np.array([i for i, (_, s) in enumerate(self.roles)
                                 if s == "thigh"], dtype=int)
        self.left_i = np.array([i for i, (s, _) in enumerate(self.roles)
                                if s == "left"], dtype=int)
        self.right_i = np.array([i for i, (s, _) in enumerate(self.roles)
                                 if s == "right"], dtype=int)
        self.impact_i = (self.shank_i if len(self.shank_i)
                         else np.arange(n, dtype=int))
        self.refresh_cal()
        # filter state
        self.z1: np.ndarray | None = None
        self.z2: np.ndarray | None = None
        self.zb: np.ndarray | None = None
        self.alpha_lp: float | None = None
        self.alpha_b: float | None = None
        self.last_a = np.zeros((n, 3))
        self.last_raw = np.zeros((n, 6))
        self.has_raw = np.zeros(n, dtype=bool)
        self.last_t = np.full(n, np.nan)
        self.seen_a = False
        # 1 s summary rings (SPEC Section 7)
        self.pk_a = np.zeros((n, PEAK_WINDOW_TICKS))
        self.pk_j = np.zeros((n, PEAK_WINDOW_TICKS))
        self.ring_valid = np.zeros((n, PEAK_WINDOW_TICKS), dtype=bool)
        self.ptr = 0
        # long-horizon EMAs (O(1) equivalents of SPEC 5.4's trailing means)
        self.ema_adyn = np.zeros(n)
        self.ema_seen = np.zeros(n, dtype=bool)
        self.last_step = 1.0 / 60.0      # for held ticks, which carry no times
        # session accumulators
        self.dose = 0.0
        self.accL = 0.0
        self.accR = 0.0
        self.fastL = 0.0        # m5 fast channel (diagnostic)
        self.fastR = 0.0
        self.move_t = 0.0
        self.asym_t = 0.0      # time accumulated INTO accL/accR
        self.legs_bad = 0.0    # debounce integrators for `partial`
        self.sides_bad = 0.0
        # m4's transmission baseline, learned PER INTENSITY BAND. A single
        # scalar baseline made m4 an activity-change detector -- see the band
        # helpers and the m4 block for why.
        self.r_sum = [0.0] * M4_BANDS      # time-weighted sum of R, per band
        self.r_time = [0.0] * M4_BANDS     # movement seconds accrued, per band
        self.r_base: list[float | None] = [None] * M4_BANDS
        self.m4_hold: float | None = None
        self.m5_hold: float | None = None
        self.m4_stale = 0.0
        self.m5_stale = 0.0
        self.demand_ema: float | None = None   # exposure-smoothed demand
        self.prev: Metrics | None = None
        self.last_tick_t: float | None = None
        self.session_start_t: float | None = None

    def refresh_cal(self) -> None:
        """Recache the per-tick-invariant calibration derivatives."""
        self.k_vec = np.array([self.cal[limb].k for limb in self.limbs])[None, :, None]
        self.bias_vec = np.array([self.cal[limb].gyro_bias
                                  for limb in self.limbs])[None, :, :]
        self.sigma_mean = float(np.mean([self.cal[limb].sigma for limb in self.limbs]))

    # --- automatic calibration (SPEC Section 3.8) ---------------------------

    def age_still_gap(self, step: float) -> None:
        """Age the no-samples gap for every still-unmeasured sensor.

        Called on held ticks, where no limb produced samples at all: the window
        is not broken by a dropout, but it is unverified, so the same
        CAL_MAX_GAP_S tolerance applies as inside _still_window_update.
        """
        for i in range(len(self.cal_src)):
            if self.cal_src[i] == "measured":
                continue
            self.cal_gap[i] += step
            if self.cal_gap[i] > CAL_MAX_GAP_S:
                self.cal_clear(i)

    def debounce(self, field: str, bad: bool, step: float) -> bool:
        """Leaky integrator: True only after `bad` has held for the dwell time."""
        v = getattr(self, field) + (step if bad else -step)
        v = min(max(v, 0.0), 2.0 * PARTIAL_DEBOUNCE_S)
        setattr(self, field, v)
        return v >= PARTIAL_DEBOUNCE_S

    def cal_clear(self, i: int) -> None:
        """Stillness broke (or a window closed): start the next one from zero."""
        self.cal_dur[i] = 0.0
        self.cal_gap[i] = 0.0
        self.cal_n[i] = 0.0
        self.cal_s1[i] = 0.0
        self.cal_s2[i] = 0.0
        self.cal_sw[i] = 0.0

    def finish_calibration(self, i: int, limb: str) -> None:
        """Turn one sensor's running sums into k / gyro_bias / sigma.

        All three SPEC Section 3.8 outputs are exact functions of the sums, so
        nothing is lost by never holding the samples:
            k     = 9.81 / mean|a|      gain
            bias  = mean(w)             gyro zero offset
            sigma = sqrt(E|a|^2 - E|a|^2)   accel noise SD

        The sums were taken on the CORRECTED signal, so the results compose
        with whatever was already applied rather than replacing it -- that is
        what lets a sensor running carried-over values refine them instead of
        measuring its own correction as an error.
        """
        n = self.cal_n[i]
        prev = self.cal[limb]
        mean_a = self.cal_s1[i] / n
        var = max(self.cal_s2[i] / n - mean_a * mean_a, 0.0)
        bias = prev.gyro_bias + self.cal_sw[i] / n
        k = prev.k * GRAVITY_MS2 / mean_a
        self.cal_clear(i)
        if not (CAL_K_MIN <= k <= CAL_K_MAX):
            # Reject rather than bake in a bad correction: keep last-known-good
            # (or defaults), flag it, and keep looking for a better window.
            self.cal_failed[i] = True
            return
        self.cal[limb] = Calibration(
            k=k,
            gyro_bias=np.asarray(bias, dtype=np.float64),
            sigma=math.sqrt(var) or prev.sigma,
        )
        self.cal_src[i] = "measured"
        self.cal_failed[i] = False
        self.refresh_cal()

    def apply_carried(self, cal_map: dict) -> int:
        """Seed the session from last-known-good values (SPEC Section 3.8).

        Only a device with no history should ever run on defaults, so this is
        applied the moment a device appears and before the first still window
        lands. Marked `carried`, never `measured`: the UI has to be able to
        tell "these numbers were measured on this athlete just now" from
        "these are last week's numbers, still the best we have".
        """
        applied = 0
        for limb, d in (cal_map or {}).items():
            i = self.idx.get(limb)
            if i is None or self.cal_src[i] == "measured":
                continue
            self.cal[limb] = Calibration.from_dict(d)
            self.cal_src[i] = "carried"
            applied += 1
        if applied:
            self.refresh_cal()
        return applied

    def cal_export(self) -> dict | None:
        """Everything worth carrying into the next session, or None."""
        out = {limb: self.cal[limb].as_dict()
               for i, limb in enumerate(self.limbs)
               if self.cal_src[i] != "default"}
        return out or None

    # --- SPEC Section 7.4 snapshot ------------------------------------------
    def snapshot(self) -> dict:
        return {
            # v2: m4's single `R_base` scalar became a per-intensity-band table.
            # A v1 snapshot is REJECTED rather than partly applied -- restoring a
            # baseline learned under the old single-band rule would reintroduce
            # exactly the activity-change confound the bands exist to remove.
            "v": 2,
            "dose": self.dose,
            "accL": self.accL,
            "accR": self.accR,
            "move_t": self.move_t,
            "asym_t": self.asym_t,
            "r_sum": list(self.r_sum),
            "r_time": list(self.r_time),
            "r_base": list(self.r_base),
            "last_tick_t": self.last_tick_t,
            "session_start_t": self.session_start_t,
            # Keyed by LIMB NAME, never by slot index: slots are assigned
            # dynamically, so slot-keyed calibration would silently apply the
            # wrong sensor's gain after devices reconnect in a different order.
            "cal": {limb: c.as_dict() for limb, c in self.cal.items()},
            "cal_src": {limb: self.cal_src[i] for i, limb in enumerate(self.limbs)},
        }

    def restore(self, snap: dict, now: float, session_gap_s: float) -> bool:
        if snap.get("v") != 2:
            return False
        last = snap.get("last_tick_t")
        if last is None or now - last > session_gap_s:
            return False                       # too old: start a fresh session
        elapsed = max(now - last, 0.0)
        self.dose = snap["dose"] * 0.5 ** (elapsed / DOSE_HALFLIFE_S)
        decay = 0.5 ** (elapsed / ASYM_HALFLIFE_S)
        self.accL = snap["accL"] * decay
        self.accR = snap["accR"] * decay
        self.move_t = snap["move_t"]
        self.asym_t = snap.get("asym_t", 0.0)
        n = M4_BANDS
        self.r_sum = (list(snap.get("r_sum") or []) + [0.0] * n)[:n]
        self.r_time = (list(snap.get("r_time") or []) + [0.0] * n)[:n]
        self.r_base = (list(snap.get("r_base") or []) + [None] * n)[:n]
        self.last_tick_t = last
        self.session_start_t = snap.get("session_start_t")
        src = snap.get("cal_src") or {}
        for limb, d in (snap.get("cal") or {}).items():
            i = self.idx.get(limb)
            if i is None:
                continue
            self.cal[limb] = Calibration.from_dict(d)
            # Snapshots written before cal_src existed carry no provenance;
            # `carried` is the honest default — it never overclaims that the
            # values were measured on this athlete during this session.
            self.cal_src[i] = src.get(limb, "carried")
        self.refresh_cal()
        return True

    def reset_session(self) -> None:
        """New session: drop accumulated load and learned baselines, keep cal.

        Calibration survives as the new session's carry-over (SPEC Section 3.8
        recomputes per session and keeps last-known-good as fallback), so
        `measured` is demoted to `carried` and the still-window search restarts
        from scratch — including the 3 s power-on discard.
        """
        self.dose = 0.0
        self.accL = self.accR = 0.0
        self.fastL = self.fastR = 0.0
        self.move_t = 0.0
        self.asym_t = 0.0      # time accumulated INTO accL/accR
        self.legs_bad = 0.0    # debounce integrators for `partial`
        self.sides_bad = 0.0
        self.r_sum = [0.0] * M4_BANDS
        self.r_time = [0.0] * M4_BANDS
        self.r_base = [None] * M4_BANDS
        self.m4_hold = self.m5_hold = None
        self.m4_stale = self.m5_stale = 0.0
        self.demand_ema = None
        self.session_start_t = None
        for i in range(len(self.limbs)):
            if self.cal_src[i] == "measured":
                self.cal_src[i] = "carried"
            self.cal_clear(i)
        self.cal_age[:] = 0.0
        self.cal_failed[:] = False


_KEY = "_biomech"


def get_session(state: dict, limbs: tuple[str, ...]) -> _Sess:
    sess = state.get(_KEY)
    if sess is None or sess.limbs != limbs:
        sess = state[_KEY] = _Sess(limbs)
    return sess


def reset_session(state: dict) -> None:
    sess = state.get(_KEY)
    if sess is not None:
        sess.reset_session()


def snapshot(state: dict) -> dict | None:
    sess = state.get(_KEY)
    return sess.snapshot() if sess is not None else None


def restore(state: dict, snap: dict, limbs: tuple[str, ...], now: float,
            session_gap_s: float = SESSION_GAP_S_DEFAULT) -> bool:
    return get_session(state, tuple(sorted(limbs))).restore(snap, now, session_gap_s)


def set_calibration(state: dict, cal: dict[str, Calibration]) -> None:
    sess = state.get(_KEY)
    if sess is None:
        return
    for limb, c in cal.items():
        i = sess.idx.get(limb)
        if i is not None:
            sess.cal[limb] = c
            sess.cal_src[i] = "measured"
    sess.refresh_cal()


def apply_carried_calibration(state: dict, cal_map: dict,
                              limbs: tuple[str, ...]) -> int:
    """Apply a device's last-known-good calibration from biomech:cal:{dev}.

    Kept separate from the SPEC Section 7.4 session snapshot on purpose: that
    snapshot is deliberately discarded after SESSION_GAP_S, which is exactly
    the case where carry-over matters most — the athlete came back tomorrow.
    """
    return get_session(state, tuple(sorted(limbs))).apply_carried(cal_map)


def calibration_export(state: dict) -> dict | None:
    """{limb: cal} for the carry-over key, or None if nothing beats defaults."""
    sess = state.get(_KEY)
    return sess.cal_export() if sess is not None else None


# =============================================================================
# Calibration (SPEC Section 3.8) -- AUTOMATIC, so every session runs calibrated.
# Nothing here is load-bearing for correctness: the defaults are a fully working
# system, and a device that never stands still keeps running on them.
# =============================================================================

def _still_window_update(sess: _Sess, limbs: tuple[str, ...], counts: list[int],
                         a_cal: np.ndarray, w_cal: np.ndarray,
                         wmag: np.ndarray, step: float) -> None:
    """Automatic still-detection + running-sum calibration (SPEC Section 3.8).

    No trainer action and no API route: every session calibrates itself the
    moment the athlete happens to stand still. Per sensor, the first
    CAL_DISCARD_S of streaming is thrown away (power-on transients), then each
    tick is accepted into the window only if it passes SPEC Section 3.8's own
    rejection guards -- mean|w| below CAL_MAX_ROTATION_DPS and mean|a| within
    CAL_MAX_G_ERROR of gravity. Anything else breaks stillness and the window
    restarts. Sensors are independent: they settle at different moments.

    The guards run on the CALIBRATED magnitudes, i.e. what the pipeline
    actually carries. For a sensor on defaults (k = 1) that is exactly SPEC
    Section 3.8's raw test; for one already running carried-over values it
    tests the residual error, which is what lets carry-over be refined instead
    of locking a corrected sensor out of ever measuring again.
    """
    amag = np.sqrt(np.einsum("ijk,ijk->ij", a_cal, a_cal))
    g_tol = GRAVITY_MS2 * CAL_MAX_G_ERROR
    for i, limb in enumerate(limbs):
        if sess.cal_src[i] == "measured":
            continue                      # done for this session
        n_i = counts[i]
        if not n_i:
            # A missing tick is not evidence the athlete moved, but a real
            # dropout leaves the window unverified -- tolerate a little, then
            # start over.
            sess.cal_gap[i] += step
            if sess.cal_gap[i] > CAL_MAX_GAP_S:
                sess.cal_clear(i)
            continue
        sess.cal_gap[i] = 0.0
        sess.cal_age[i] += step
        if sess.cal_age[i] < CAL_DISCARD_S:
            continue
        seg = amag[:n_i, i]
        mean_a = float(seg.mean())
        if float(wmag[:n_i, i].mean()) > CAL_MAX_ROTATION_DPS:
            # The athlete moved. Says nothing about the sensor.
            sess.cal_bad_g[i] = 0.0
            sess.cal_clear(i)
            continue
        if abs(mean_a - GRAVITY_MS2) > g_tol:
            # The gyro says this sensor is motionless, yet |a| disagrees with
            # gravity. Motion cannot explain that -- the sensor can. Without
            # this split the two causes were indistinguishable and a genuinely
            # mis-scaled sensor read `uncalibrated` forever, which looks exactly
            # like an athlete who never stood still.
            sess.cal_bad_g[i] += step
            if sess.cal_bad_g[i] >= CAL_FAULT_S:
                sess.cal_failed[i] = True
            sess.cal_clear(i)
            continue
        sess.cal_bad_g[i] = 0.0
        sess.cal_n[i] += n_i
        sess.cal_s1[i] += float(seg.sum())
        sess.cal_s2[i] += float(seg @ seg)
        sess.cal_sw[i] += w_cal[:n_i, i].sum(axis=0)
        sess.cal_dur[i] += step
        if sess.cal_dur[i] >= CAL_WINDOW_S:
            sess.finish_calibration(i, limb)


def calibrate(frames: dict[str, np.ndarray]) -> dict[str, Calibration] | None:
    """Per-sensor gain/bias/noise from an explicit still-stand window.

    The batch form of the same measurement compute() now makes automatically
    (_still_window_update): kept for callers holding a complete window of
    frames, and as the executable statement of the validity guards.

    Worth doing because m4 and m5 are inter-sensor RATIOS, so gain mismatch
    biases them directly. Returns None if any sensor fails a validity guard --
    rejecting is strictly better than baking in a bad correction.
    """
    out: dict[str, Calibration] = {}
    for limb, f in frames.items():
        if f is None or len(f) < 2:
            return None
        a = np.asarray(f[:, 0:3], dtype=np.float64) * ACCEL_MS2_PER_COUNT
        w = np.asarray(f[:, 3:6], dtype=np.float64) * GYRO_DPS_PER_COUNT
        amag = np.linalg.norm(a, axis=1)
        mean_a = float(amag.mean())
        if mean_a <= 0.0:
            return None
        if abs(mean_a - GRAVITY_MS2) / GRAVITY_MS2 > CAL_MAX_G_ERROR:
            return None                                   # sensor was not still
        if float(np.linalg.norm(w, axis=1).mean()) > CAL_MAX_ROTATION_DPS:
            return None                                   # athlete moved
        k = GRAVITY_MS2 / mean_a
        if not (CAL_K_MIN <= k <= CAL_K_MAX):
            return None
        out[limb] = Calibration(
            k=k,
            gyro_bias=w.mean(axis=0),
            sigma=float(np.std(amag - mean_a)) or DEFAULT_CALIBRATION.sigma,
        )
    return out or None


# =============================================================================
# compute()
# =============================================================================

def compute(
    frames: dict[str, np.ndarray],
    state: dict,
    times: dict[str, np.ndarray] | None = None,
) -> Metrics:
    """frames: limb -> float32[n, 6] (ax..gz, raw counts) since the last tick.

    times:  limb -> float64[n] server-mapped sample times in seconds. Optional;
            without it dt falls back to 1/NOMINAL_INPUT_HZ. m2 is a derivative,
            so real timestamps matter (SPEC Section 3.5).
    state:  persists across calls per device; holds filter state, the 1 s
            summary rings and the session accumulators.

    Called at OUTPUT_HZ per device. Returns Metrics(m1..m5, composite), 0..100.
    """
    limbs = tuple(sorted(frames.keys()))
    if not limbs:
        return HELD_ZERO
    sess = get_session(state, limbs)
    n_limbs = len(limbs)

    counts = [0 if frames[limb] is None else len(frames[limb]) for limb in limbs]
    n_max = max(counts)
    if n_max == 0:
        # Held tick: repeat the previous values, accumulate no dose. The still
        # window must still AGE here -- returning without touching it froze a
        # half-built window across a whole-device dropout and then resumed as
        # though the stillness had been continuous. Ticks are fixed-rate, so
        # last_step is the right increment; it defaults to one 60Hz tick.
        sess.age_still_gap(sess.last_step)
        return sess.prev if sess.prev is not None else HELD_ZERO

    # --- 1. assemble [n_max, n_limbs, 6]; HOLD-LAST fill for short/absent limbs.
    # Never zero-fill: it decays the gravity baseline, so a sensor reconnecting
    # while the athlete stands still emits a ~9.75 m/s^2 phantom impact and m1
    # reads ~85/100 out of nothing (SPEC Section 7.2.1).
    block = np.empty((n_max, n_limbs, 6), dtype=np.float64)
    valid = np.zeros((n_max, n_limbs), dtype=bool)
    active = np.zeros(n_limbs, dtype=bool)
    for i, limb in enumerate(limbs):
        n_i = counts[i]
        if n_i:
            f = np.asarray(frames[limb], dtype=np.float64)
            block[:n_i, i, :] = f
            valid[:n_i, i] = True
            active[i] = True
            sess.last_raw[i] = f[-1]
            sess.has_raw[i] = True
        if n_i < n_max:
            fill = sess.last_raw[i] if sess.has_raw[i] else np.zeros(6)
            block[n_i:, i, :] = fill

    # --- 2. scale + apply calibration
    a_raw = block[:, :, 0:3] * (ACCEL_MS2_PER_COUNT * sess.k_vec)
    w_dps = block[:, :, 3:6] * GYRO_DPS_PER_COUNT - sess.bias_vec

    sat_any = (np.abs(block) >= SAT_THRESHOLD_COUNTS).any(axis=2)
    n_valid = int(valid.sum())
    sat_frac = float((sat_any & valid).sum()) / max(n_valid, 1)

    # --- 3. dt per sample, from real timestamps where available.
    # One np.diff over a [n_max+1, n_limbs] time matrix rather than a diff plus
    # a median per limb: np.median was measured as the single most expensive
    # call in compute().
    nominal_dt = 1.0 / NOMINAL_INPUT_HZ
    tmat = np.empty((n_max + 1, n_limbs))
    for i, limb in enumerate(limbs):
        n_i = counts[i]
        t_i = None
        if n_i and times is not None and times.get(limb) is not None:
            cand = np.asarray(times[limb], dtype=np.float64)
            if len(cand) == n_i:
                t_i = cand
        if t_i is None:
            # no usable timestamps: synthesise a nominal ramp for this limb
            anchor = sess.last_t[i] if np.isfinite(sess.last_t[i]) else 0.0
            tmat[1:, i] = anchor + (np.arange(n_max) + 1) * nominal_dt
            tmat[0, i] = anchor
        else:
            tmat[1:n_i + 1, i] = t_i
            if n_i < n_max:            # hold-last padded rows advance nominally
                tmat[n_i + 1:, i] = t_i[-1] + (np.arange(n_max - n_i) + 1) * nominal_dt
            prev_t = sess.last_t[i]
            tmat[0, i] = prev_t if np.isfinite(prev_t) else t_i[0] - nominal_dt
            sess.last_t[i] = t_i[-1]
    dt_arr = np.diff(tmat, axis=0)
    bad = ~((dt_arr > 0.0) & (dt_arr < MAX_DT_S))
    if bad.any():
        dt_arr[bad] = nominal_dt

    # --- 4. filter (SPEC Section 3.6). Alphas come from MEASURED dt, so the
    # model stays rate-flexible as TRD Section 4 requires.
    #
    # dt is QUANTISED to 1 us before deriving the alphas. Without this, ordinary
    # timestamp jitter changes alpha on nearly every tick, which re-seeds the
    # filters every tick -- and a filter re-seeded from the current sample has
    # base == amag, so adyn collapses to 0 and every metric silently reads zero.
    dt_nom = max(float(dt_arr.mean()), 1e-6)
    dt_q = round(dt_nom, 6)
    alpha_lp = 1.0 - math.exp(-dt_q / (1.0 / (2.0 * math.pi * FILTER_CUTOFF_HZ)))
    alpha_b = 1.0 - math.exp(-dt_q / GRAVITY_TAU_S)
    b_lp, a_lp = _onepole(alpha_lp)
    b_b, a_b = _onepole(alpha_b)

    flat = np.ascontiguousarray(a_raw.reshape(n_max, n_limbs * 3))
    if sess.z1 is None or sess.alpha_lp != alpha_lp:
        # First tick, or the input rate moved enough to change the coefficients:
        # (re)seed from this block's first sample so no step transient is injected.
        seed = flat[0]
        sess.z1 = _zi_for(alpha_lp, seed)
        sess.z2 = _zi_for(alpha_lp, seed)
        sess.alpha_lp = alpha_lp
    y, sess.z1 = lfilter(b_lp, a_lp, flat, axis=0, zi=sess.z1)
    y, sess.z2 = lfilter(b_lp, a_lp, y, axis=0, zi=sess.z2)
    a_f = y.reshape(n_max, n_limbs, 3)

    amag = np.sqrt(np.einsum("ijk,ijk->ij", a_f, a_f))
    if sess.zb is None or sess.alpha_b != alpha_b:
        sess.zb = _zi_for(alpha_b, amag[0])
        sess.alpha_b = alpha_b
    base, sess.zb = lfilter(b_b, a_b, amag, axis=0, zi=sess.zb)
    adyn = np.abs(amag - base)

    # --- 5. exact gravity-free jerk (SPEC Section 3.4):
    #     ||df/dt + w x f|| == ||da_world/dt||, from measured signals only.
    # w MUST be in radians/s here. Feeding deg/s makes m2 wrong by 57.3x and it
    # looks plausible rather than obviously broken.
    prev_a = np.empty_like(a_f)
    prev_a[0] = sess.last_a if sess.seen_a else a_f[0]
    prev_a[1:] = a_f[:-1]
    w_rad = w_dps * (math.pi / 180.0)
    da = (a_f - prev_a) / dt_arr[:, :, None]
    # cross product written out: np.cross is generic and profiled ~3x the cost
    ax, ay, az = a_f[:, :, 0], a_f[:, :, 1], a_f[:, :, 2]
    wx, wy, wz = w_rad[:, :, 0], w_rad[:, :, 1], w_rad[:, :, 2]
    jx = da[:, :, 0] + (wy * az - wz * ay)
    jy = da[:, :, 1] + (wz * ax - wx * az)
    jz = da[:, :, 2] + (wx * ay - wy * ax)
    jerk = np.sqrt(jx * jx + jy * jy + jz * jz)
    sess.last_a = a_f[-1].copy()
    sess.seen_a = True

    # --- 6. per-tick summaries -> 1 s ring.
    # p90 WITHIN the tick rejects single-sample spikes; the peak-hold is a MAX
    # ACROSS the ring (step 8). A percentile across the ring is silently broken:
    # an isolated 50 m/s^2 impact moves it by 0.000 (SPEC Section 5.1).
    p = sess.ptr
    tick_mean_adyn = np.zeros(n_limbs)
    tick_mean_w = np.zeros(n_limbs)
    wmag = np.sqrt(w_dps[:, :, 0] ** 2 + w_dps[:, :, 1] ** 2 + w_dps[:, :, 2] ** 2)
    active_counts = {c for c in counts if c}
    if len(active_counts) == 1:
        # Fast path: every streaming limb released the same number of samples,
        # which is the overwhelmingly common case. One percentile call for all
        # limbs instead of two per limb -- np.percentile is ~15 us of overhead
        # each, so this is most of compute()'s cost.
        n_i = active_counts.pop()
        pk_a_all = _p90_axis0(adyn[:n_i])
        pk_j_all = _p90_axis0(jerk[:n_i])
        mean_a_all = adyn[:n_i].mean(axis=0)
        mean_w_all = wmag[:n_i].mean(axis=0)
        sess.pk_a[:, p] = np.where(active, pk_a_all, 0.0)
        sess.pk_j[:, p] = np.where(active, pk_j_all, 0.0)
        tick_mean_adyn = np.where(active, mean_a_all, 0.0)
        tick_mean_w = np.where(active, mean_w_all, 0.0)
        sess.ring_valid[:, p] = active
    else:
        for i in range(n_limbs):
            n_i = counts[i]
            if n_i:
                sess.pk_a[i, p] = float(_p90_axis0(adyn[:n_i, i:i + 1])[0])
                sess.pk_j[i, p] = float(_p90_axis0(jerk[:n_i, i:i + 1])[0])
                tick_mean_adyn[i] = float(adyn[:n_i, i].mean())
                tick_mean_w[i] = float(wmag[:n_i, i].mean())
                sess.ring_valid[i, p] = True
            else:
                sess.pk_a[i, p] = 0.0
                sess.pk_j[i, p] = 0.0
                sess.ring_valid[i, p] = False
    sess.ptr = (p + 1) % PEAK_WINDOW_TICKS

    # --- 7. elapsed time for the session accumulators
    now_t = None
    if times:
        ends = [float(np.asarray(times[limb])[-1])
                for i, limb in enumerate(limbs)
                if counts[i] and times.get(limb) is not None]
        if ends:
            now_t = max(ends)
    step = 1.0 / 60.0
    if now_t is not None and sess.last_tick_t is not None:
        step = min(max(now_t - sess.last_tick_t, 0.0), 1.0)
    sess.last_step = step        # held ticks carry no time; they reuse this
    if now_t is not None:
        sess.last_tick_t = now_t
        if sess.session_start_t is None:
            sess.session_start_t = now_t

    # Movement intensity for this tick. Hoisted above the m4 EMA because that
    # EMA is now movement-gated; the dose block below reuses these.
    a_int = float(tick_mean_adyn[active].mean()) if active.any() else 0.0
    w_int = float(tick_mean_w[active].mean()) if active.any() else 0.0
    moving = a_int > MOVE_GATE_MS2

    # m4's trailing transmission mean, as an O(1) EMA.
    # Advanced only while MOVING: rest ticks used to fold in too, dragging both
    # the thigh and shank means toward the noise floor, so a baseline that
    # locked shortly after a rest was measured against a partly-rest-loaded EMA.
    # `ema_seen` still latches on any active tick -- that is a has-ever-streamed
    # record used to tell "warming up" from "this sensor is dead", and gating it
    # on movement would make a still athlete look like a hardware fault.
    alpha_ctl = (1.0 - math.exp(-step / CONTROL_TAU_S)) if moving else 0.0
    upd = active & sess.ema_seen
    new = active & ~sess.ema_seen
    sess.ema_adyn = np.where(
        upd, sess.ema_adyn + alpha_ctl * (tick_mean_adyn - sess.ema_adyn),
        np.where(new, tick_mean_adyn, sess.ema_adyn),
    )
    sess.ema_seen |= active

    # Automatic calibration (SPEC Section 3.8). Runs only while some sensor is
    # still unmeasured, so a fully calibrated device pays nothing per tick. The
    # ticker keeps emitting throughout: the transition shows up in `flags`, so
    # the UI can mark the step change instead of presenting it as a change in
    # the athlete.
    if not all(src == "measured" for src in sess.cal_src):
        _still_window_update(sess, limbs, counts, a_raw, w_dps, wmag, step)

    flags: set[str] = set()
    if sat_frac > 0.0:
        flags.add("saturated")
    if "default" in sess.cal_src:
        flags.add("uncalibrated")
    if "carried" in sess.cal_src:
        flags.add("carried_over")
    if sess.cal_failed.any():
        flags.add("cal_failed")
    if n_limbs < 4:
        flags.add("degraded_sensors")

    # --- 8. primitives ------------------------------------------------------
    sigma = sess.sigma_mean          # cached; only changes on calibration
    m1_lo = max(M1_LO_FLOOR, M1_LO_SIGMAS * sigma)

    shank_i = sess.shank_i
    thigh_i = sess.thigh_i
    impact_i = sess.impact_i
    if len(shank_i) == 0:
        flags.add("no_shank")

    def ring_max(arr: np.ndarray, rows: np.ndarray) -> float:
        sel = arr[rows]
        vsel = sess.ring_valid[rows]
        if not vsel.any():
            return 0.0
        return float(np.where(vsel, sel, -np.inf).max())

    if sat_frac > SAT_SUPPRESS_FRACTION:
        m1 = m2 = None            # a truncated peak is worse than no peak
    else:
        m1 = log_score(ring_max(sess.pk_a, impact_i), m1_lo, M1_HI)
        m2 = log_score(ring_max(sess.pk_j, list(range(n_limbs))), M2_LO, M2_HI)

    # m3 dose: power law, decays always, accumulates only while moving.
    #
    # The power law acts on the PHYSICAL load ratio, not on a log score. Until
    # 2026-08-03 it was `(intensity/100)**3` where `intensity` was itself a
    # log_score -- but Whalen's daily-stress-stimulus exponent applies to the
    # stress sigma, not to a compressed score of it, and log-compressing first
    # destroys exactly the magnitude weighting the exponent exists to apply.
    # Measured consequence: an easy walk scored intensity 57 against hard
    # running's ~80, so it accumulated dose at (0.57/0.80)^3 = 36% of the hard-
    # running rate when the physical loads differ by more than an order of
    # magnitude. 90 s of slow walking reached m3 = 30, putting a 15-point dose
    # floor under the composite and making an easy walk read as real injury
    # risk. On the physical ratio the same walk accumulates 14x less.
    #
    # The references are the sustained intensity of HARD RUNNING, which gives
    # `dose` an interpretable unit: one dose-minute is one minute of hard
    # training equivalent, so M3_HI = 60 is "a full hard hour". Both arms are
    # kept and max()'d for the SPEC Section 5.3 reason -- |a| under-reads slow
    # horizontal gym movement, while mean |w| is the best-supported IMU fatigue
    # marker in resistance training (Brice 2020).
    load_ratio = max(a_int / A_DOSE_REF, w_int / W_DOSE_REF)
    # `intensity` is retained on the 0-100 scale for the diagnostics stream and
    # for anything reading biomech:diag -- it is no longer what drives the dose.
    intensity = max(log_score(a_int, m1_lo, M1_HI), log_score(w_int, W_LO, W_HI))
    sess.dose *= 0.5 ** (step / DOSE_HALFLIFE_S)
    if moving:
        sess.dose += load_ratio**DOSE_EXPONENT * (step / 60.0)
        sess.move_t += step
    m3 = log_score(sess.dose, M3_LO, M3_HI)

    # m4: shank->thigh transmission drift vs this athlete's own fresh baseline.
    # Direction-agnostic (|.|): the literature does not fix the sign -- shock
    # attenuation usually INCREASES under fatigue, and injured runners showed
    # GREATER lower-body attenuation (SPEC Section 5.4).
    m4 = None
    R_now = None
    legs_ok = (len(shank_i) > 0 and len(thigh_i) > 0
               and bool(active[shank_i].all()) and bool(active[thigh_i].all()))
    sh_mean = float(sess.ema_adyn[shank_i].mean()) if len(shank_i) else 0.0
    th_mean = float(sess.ema_adyn[thigh_i].mean()) if len(thigh_i) else 0.0
    band = _intensity_band(sh_mean)
    if legs_ok and moving and sh_mean > MOVE_GATE_MS2:
        R_now = th_mean / sh_mean
        base = sess.r_base[band]
        if base is None:
            # Still learning THIS band. Accumulate a time-weighted MEAN rather
            # than snapshotting one tick: the old code froze R_base to a single
            # sample, so one unlucky tick set the reference for the session.
            #
            # Nothing is learned until the 20 s transmission EMA has settled.
            # `ema_adyn` seeds from the first active tick, when the gravity
            # baseline is still converging and `adyn` briefly carries most of
            # 9.81 m/s^2 -- so sh_mean sweeps down through every band during
            # warm-up. Without this gate a band could lock its baseline on that
            # sweep and then read 100 for the rest of the session once R settled
            # somewhere else. Measured: exactly that, on the activity ladder.
            if sess.move_t >= M4_SETTLE_S:
                sess.r_sum[band] += R_now * step
                sess.r_time[band] += step
            if sess.r_time[band] >= M4_BASELINE_LOCK_S:
                learned = sess.r_sum[band] / sess.r_time[band]
                # `> 0.0`, not truthiness: a baseline of exactly 0.0 would make
                # the old `if sess.R_base:` test fail forever while the
                # `is None` guard also failed, leaving m4 permanently dead with
                # no value and no flag.
                sess.r_base[band] = learned if learned > 0.0 else None
        else:
            m4 = 100.0 * min(abs(R_now / base - 1.0) / M4_FULL_SCALE, 1.0)
            sess.m4_hold = m4
            sess.m4_stale = 0.0
    if m4 is None:
        # Freeze rather than compute. If a shank drops out while its thigh
        # survives, R = thigh/shank explodes and m4 pins at 100 -- a hardware
        # fault rendering as the most alarming possible finding.
        sess.m4_stale += step
        if sess.m4_hold is not None and sess.m4_stale <= STALE_TIMEOUT_S:
            m4 = sess.m4_hold
        elif sess.m4_hold is not None and sess.debounce("legs_bad", not legs_ok, step):
            # `partial` means SENSORS are missing. Simply standing still also
            # freezes m4, but that is normal and must not raise a fault flag.
            flags.add("partial")
    if sess.r_base[band] is None:
        # R needs a shank AND a thigh. If either is unmapped OR mapped but never
        # actually streamed (flat battery, bad strap), no baseline can ever lock
        # -- a permanently degraded sensor set, not a warm-up. Flagging it
        # `warming_up` tells the UI to keep waiting for a value that is never
        # coming. `ema_seen` is the has-ever-produced-data record: testing the
        # static role indices alone caught only the unmapped case, so a dead
        # thigh still read `warming_up` for the whole session.
        #
        # Per BAND: entering a new intensity for the first time is a genuine
        # warm-up for that band, and m4 is null until it has 60 s there. That is
        # the honest answer -- the alternative is comparing against an unrelated
        # activity, which is what pinned m4 at 100.
        pair_live = (len(shank_i) and len(thigh_i)
                     and bool(sess.ema_seen[shank_i].all())
                     and bool(sess.ema_seen[thigh_i].all()))
        flags.add("warming_up" if pair_live else "degraded_sensors")

    # m5: wUSI over decaying accumulators. Both the per-tick noise weighting
    # (where the units match) and the both-sides gate are load-bearing -- SPEC
    # Section 5.5 records the measured failure modes of getting either wrong.
    left_i, right_i = sess.left_i, sess.right_i
    sides_ok = (len(left_i) > 0 and len(right_i) > 0
                and bool(active[left_i].all()) and bool(active[right_i].all()))
    m5 = None
    usi_pct = None
    # W is evaluated every tick (and published in `raw`) even when the gates
    # stop it being used: it is the diagnostic that shows the weighting is
    # applied PER TICK, where the units match, rather than to the accumulators,
    # where it evaluates to 0.99999 and wUSI silently degenerates to plain USI.
    w_noise = float("nan")
    if len(left_i) and len(right_i):
        L = float(tick_mean_adyn[left_i].mean())
        R = float(tick_mean_adyn[right_i].mean())
        # Clamped at 0: the weight goes NEGATIVE once L and R fall below the
        # noise floor (it tends to -1 as both approach 0), and a negative
        # weight subtracts from the accumulators -- eating previously measured
        # load, and able to drive them negative outright on a noisy sensor
        # where sigma is large. 0 is the correct floor: "this tick carries no
        # trustworthy asymmetry information", not "undo the last tick".
        w_noise = max(0.0, 1.0 - 2.0 * sigma**2 / (sigma**2 + L * L + R * R))
    usi_fast_pct = None
    if sides_ok and moving:
        decay = 0.5 ** (step / ASYM_HALFLIFE_S)
        sess.accL = sess.accL * decay + w_noise * L * step
        sess.accR = sess.accR * decay + w_noise * R * step
        # Fast channel, same estimator over a much shorter memory, so a discrete
        # one-sided event is visible. Diagnostic only for now.
        fast_decay = 0.5 ** (step / ASYM_FAST_HALFLIFE_S)
        sess.fastL = sess.fastL * fast_decay + w_noise * L * step
        sess.fastR = sess.fastR * fast_decay + w_noise * R * step
        fast_denom = math.sqrt(sess.fastL**2 + sess.fastR**2)
        if fast_denom > 0.0:
            usi_fast_pct = 100.0 * (sess.fastL - sess.fastR) / fast_denom
        # Time actually accumulated INTO the accumulators. move_t counts all
        # movement including ticks where a side was inactive, so on a lossy link
        # it reached the warm-up threshold while the accumulators were still
        # nearly empty -- and USI = (L-R)/sqrt(L^2+R^2) is unstable when both are
        # tiny, so m5's first emitted values were ~82 out of pure noise.
        sess.asym_t += step
    denom = math.sqrt(sess.accL**2 + sess.accR**2)
    if sides_ok and sess.asym_t >= M5_WARMUP_S and denom > 0.0:
        usi = (sess.accL - sess.accR) / denom
        usi_pct = 100.0 * usi
        m5 = 100.0 * min(abs(usi) / M5_FULL_SCALE_USI, 1.0)
        sess.m5_hold = m5
        sess.m5_stale = 0.0
    else:
        # Same has-ever-streamed test as m4: a side that never produced data
        # makes m5 permanently uncomputable. Without this the metric went null
        # with NO flag at all -- the worst case, because "no data" and "no
        # asymmetry" then look identical to the UI.
        sides_live = (len(left_i) and len(right_i)
                      and bool(sess.ema_seen[left_i].all())
                      and bool(sess.ema_seen[right_i].all()))
        if sess.asym_t < M5_WARMUP_S and sides_live:
            flags.add("warming_up")
        sess.m5_stale += step
        if sess.m5_hold is not None and sess.m5_stale <= STALE_TIMEOUT_S:
            m5 = sess.m5_hold
        elif not sides_live:
            flags.add("degraded_sensors")
        elif sess.m5_hold is not None and sess.debounce("sides_bad", not sides_ok, step):
            # As for m4: `partial` is a missing-sensor signal, not a rest signal.
            flags.add("partial")

    if m4 is not None or m5 is not None:
        flags.add("unvalidated")           # SPEC Section 11.1 -- synthetic only

    # --- 9. composite (SPEC Section 6.1) ------------------------------------
    if m1 is None and m2 is None:
        demand_inst = 0.0
    elif m1 is None or m2 is None:
        demand_inst = float(m1 if m2 is None else m2)
    else:
        demand_inst = DEMAND_MAX_W * max(m1, m2) + DEMAND_MIN_W * min(m1, m2)
    # Exposure, not peak (see DEMAND_TAU_S). m1/m2 stay peak-holds for display;
    # the risk index follows how long load is sustained, so one landing is a
    # brief bump and a minute of running is a sustained reading.
    alpha_dem = 1.0 - math.exp(-step / DEMAND_TAU_S)
    sess.demand_ema = (demand_inst if sess.demand_ema is None
                       else sess.demand_ema + alpha_dem * (demand_inst - sess.demand_ema))
    demand = sess.demand_ema
    terms = [(DEGRADE_W_M4, m4), (DEGRADE_W_M5, m5)]
    avail = [(w, v) for w, v in terms if v is not None]
    degradation = (sum(w * v for w, v in avail) / sum(w for w, _ in avail)
                   if avail else 0.0)
    capacity = 100.0 - CAPACITY_FACTOR * degradation
    # Risk rises with the load/capacity RATIO. The previous form,
    # 100*demand/(demand+capacity), is a hyperbola whose value at demand = 100
    # and healthy capacity = 100 is exactly 50 -- so for an uninjured athlete the
    # acute term could not exceed half scale however hard the session, and the
    # top half of a 0-100 "injury risk" was reachable only through accumulated
    # dose. Measured live: demand p99 = 91 yet composite p99 = 66.
    # Same ratio raised to ACUTE_EXPONENT (a Hill function, sigmoid rather than
    # hyperbolic so ordinary activity stays low), normalised so demand ==
    # capacity reads 100 for any exponent. Degradation lowers capacity and
    # therefore reaches full scale sooner, which is the intent.
    ratio = demand / capacity if capacity > 0 else float("inf")
    r_n = ratio ** ACUTE_EXPONENT if math.isfinite(ratio) else float("inf")
    acute = 100.0 if not math.isfinite(r_n) else min(100.0, 200.0 * r_n / (r_n + 1.0))
    floor = FLOOR_FACTOR * m3
    composite = floor + (100.0 - floor) * acute / 100.0

    metrics = Metrics(
        m1=m1, m2=m2, m3=m3, m4=m4, m5=m5,
        composite=float(min(max(composite, 0.0), 100.0)),
        flags=frozenset(flags),
        raw={
            "R": R_now if R_now is not None else float("nan"),
            # baseline FOR THE BAND currently being worked in, plus which band
            # that is and how much of its 60 s lock has been served -- without
            # these a null m4 is indistinguishable from a broken one.
            "R_base": (sess.r_base[band] if sess.r_base[band] is not None
                       else float("nan")),
            "m4_band": float(band),
            "m4_band_t": sess.r_time[band],
            "usi_pct": usi_pct if usi_pct is not None else float("nan"),
            # m5's FAST channel (SPEC Section 5.5 open item). The reported m5 is
            # a 5-minute accumulator, so one second of one-sided load displaces
            # only ~0.2% of it -- measured on a worn session, a single-leg
            # landing barely moved it. This short-window estimate is exposed for
            # study before any decision to promote it; the reported metric is
            # unchanged. Magnitude only, never a direction (SPEC Section 5.5).
            "usi_fast_pct": usi_fast_pct if usi_fast_pct is not None else float("nan"),
            # per-tick wUSI noise weight (SPEC Section 10 item 3): near 1 during
            # movement, well below it at the noise floor. A constant 1.0 here
            # means the weighting is being applied to the accumulators.
            "W": w_noise,
            "dose": sess.dose,
            "move_t": sess.move_t,
            "intensity": intensity,
            "a_int": a_int,
            "w_int": w_int,
            "sat_frac": sat_frac,
            "m1_lo": m1_lo,
            "demand": demand,
            "degradation": degradation,
        },
    )
    sess.prev = metrics
    return metrics
